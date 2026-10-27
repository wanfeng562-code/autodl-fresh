from dataclasses import replace
import hashlib
import io
import json
import threading
import zipfile

import pytest

from autodl_fresh.updater import (
    GitHubHTTP, LATEST_API, REPOSITORY, UpdateCancelled, UpdateError, check_latest,
    download_release, version_tuple,
)


def package_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(zipfile.ZipInfo("AutoDLFresh/AutoDLFresh.exe"), b"fixture only, never executed")
    return output.getvalue()


def release_payload(content, *, tag="v0.7.0"):
    name = f"AutoDLFresh-{tag[1:]}-windows-x64.zip"
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "body": "新功能\n<script>untrusted release notes</script>",
            "assets": [{"name": name, "state": "uploaded", "size": len(content),
                        "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}",
                        "digest": "sha256:" + hashlib.sha256(content).hexdigest()}]}


class FixtureHTTP:
    def __init__(self, data):
        self.data = data
        self.urls = []

    def open(self, url):
        self.urls.append(url)
        value = self.data[url]
        return value() if callable(value) else io.BytesIO(value)


def fixture_release(content=None):
    content = content if content is not None else package_bytes()
    payload = release_payload(content)
    url = payload["assets"][0]["browser_download_url"]
    http = FixtureHTTP({LATEST_API: json.dumps(payload).encode(), url: content})
    return check_latest("0.6.0", http=http).release, http


def test_numeric_versions_and_no_downgrade():
    assert version_tuple("v0.10.0") > version_tuple("0.9.99")
    release, http = fixture_release()
    assert check_latest("0.6.0", http=http).available
    assert not check_latest("0.7.0", http=http).available
    assert not check_latest("1.0.0", http=http).available
    assert release.version == "0.7.0"


@pytest.mark.parametrize("version", ["v0.7.0-rc.1", "latest", "../0.7.0", "0.07.0", "1.2", "1.2.3+build"])
def test_invalid_or_prerelease_version_tags_are_rejected(version):
    with pytest.raises(UpdateError):
        version_tuple(version)


@pytest.mark.parametrize("change", ["draft", "prerelease", "external_url", "other_repo", "duplicate", "bad_hash"])
def test_unpublished_or_untrusted_release_cannot_be_downloaded(change):
    payload = release_payload(package_bytes())
    if change in {"draft", "prerelease"}:
        payload[change] = True
    elif change == "external_url":
        payload["assets"][0]["browser_download_url"] = "https://example.com/update.zip"
    elif change == "other_repo":
        payload["assets"][0]["browser_download_url"] = payload["assets"][0]["browser_download_url"].replace(REPOSITORY, "other/project")
    elif change == "duplicate":
        payload["assets"].append(payload["assets"][0].copy())
    else:
        payload["assets"][0]["digest"] = "not a digest"
    with pytest.raises(UpdateError):
        check_latest("0.6.0", http=FixtureHTTP({LATEST_API: json.dumps(payload).encode()}))


def test_new_release_without_windows_asset_is_still_reported():
    payload = release_payload(b"")
    payload["assets"] = []
    check = check_latest("0.6.0", http=FixtureHTTP({LATEST_API: json.dumps(payload).encode()}))
    assert check.available and check.release.asset is None


def test_verified_package_is_saved_atomically_and_reports_progress(tmp_path):
    release, http = fixture_release()
    target = tmp_path / release.asset.name
    target.write_bytes(b"previous file")
    progress = []
    result = download_release(release, target, http=http, progress=lambda *values: progress.append(values))
    assert result == target
    assert target.read_bytes() == package_bytes()
    assert progress[-1] == (release.asset.size, release.asset.size)
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("failure", ["hash", "truncated", "oversized", "connection", "cancel"])
def test_failed_download_preserves_existing_file_and_cleans_temporary_file(tmp_path, failure):
    release, http = fixture_release()
    target = tmp_path / release.asset.name
    target.write_bytes(b"keep my existing file")
    cancel = threading.Event()
    progress = None
    if failure == "hash":
        release = replace(release, asset=replace(release.asset, sha256="0" * 64))
    elif failure == "truncated":
        http.data[release.asset.url] = package_bytes()[:-5]
    elif failure == "oversized":
        http.data[release.asset.url] = package_bytes() + b"extra"
    elif failure == "connection":
        class BrokenStream(io.BytesIO):
            def read(self, size=-1):
                raise OSError("fixture disconnect")
        http.data[release.asset.url] = lambda: BrokenStream(b"")
    else:
        def progress(*_):
            cancel.set()
    expected_error = UpdateCancelled if failure == "cancel" else UpdateError
    with pytest.raises(expected_error):
        download_release(release, target, http=http, cancel=cancel, progress=progress)
    assert target.read_bytes() == b"keep my existing file"
    assert not list(tmp_path.glob("*.part"))


def test_checksum_file_fallback_and_absent_hash_rejection(tmp_path):
    content = package_bytes()
    payload = release_payload(content)
    payload["assets"][0].pop("digest")
    file_name = payload["assets"][0]["name"]
    checksum = f"{hashlib.sha256(content).hexdigest()}  {file_name}\n".encode()
    checksum_url = f"https://github.com/{REPOSITORY}/releases/download/v0.7.0/SHA256SUMS.txt"
    payload["assets"].append({"name": "SHA256SUMS.txt", "browser_download_url": checksum_url,
                              "size": len(checksum), "state": "uploaded"})
    http = FixtureHTTP({LATEST_API: json.dumps(payload).encode(), checksum_url: checksum,
                        payload["assets"][0]["browser_download_url"]: content})
    release = check_latest("0.6.0", http=http).release
    download_release(release, tmp_path / file_name, http=http)
    assert checksum_url in http.urls
    with pytest.raises(UpdateError, match="校验信息"):
        download_release(replace(release, checksums=None), tmp_path / "new.zip", http=http)
    assert not (tmp_path / "new.zip").exists()


def test_tls_redirects_and_rate_limits_do_not_weaken_download_rules():
    from urllib.error import HTTPError
    from urllib.request import Request
    from autodl_fresh.updater import _GitHubRedirects
    redirects = _GitHubRedirects()
    with pytest.raises(UpdateError):
        redirects.redirect_request(Request(LATEST_API), None, 302, "Found", {}, "http://github.com/file.zip")
    with pytest.raises(UpdateError):
        redirects.redirect_request(Request(LATEST_API), None, 302, "Found", {}, "https://evil.example/file.zip")
    class Limited:
        def open(self, request, **kwargs):
            assert "Authorization" not in request.headers
            raise HTTPError(request.full_url, 403, "rate limit", {}, None)
    http = GitHubHTTP()
    http.opener = Limited()
    with pytest.raises(UpdateError, match="请求受到限制"):
        http.open(LATEST_API)
