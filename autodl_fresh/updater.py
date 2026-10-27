"""Anonymous GitHub release checks and verified, atomic update downloads."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import zipfile

from PySide6.QtCore import QThread, Signal

from . import __version__

REPOSITORY = "wanfeng562-code/autodl-fresh"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_ASSET_SIZE = 2_000_000_000
ALLOWED_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com",
                 "objects.githubusercontent.com", "github-releases.githubusercontent.com"}


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


def version_tuple(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"[vV]?((?:0|[1-9]\d{0,8})\.(?:0|[1-9]\d{0,8})\.(?:0|[1-9]\d{0,8}))", text)
    if not match:
        raise UpdateError("版本号格式无法识别，请到 GitHub 发布页查看。")
    return tuple(int(part) for part in match[1].split("."))


def _trusted_url(url: str):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username
            or parsed.password or parsed.port not in {None, 443}):
        raise UpdateError("更新地址不是受支持的 GitHub HTTPS 地址，已停止下载。")


class _GitHubRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubHTTP:
    def __init__(self):
        self.opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _GitHubRedirects())

    def open(self, url):
        _trusted_url(url)
        request = Request(url, headers={"User-Agent": f"AutoDLFresh/{__version__}",
                                        "Accept": "application/vnd.github+json" if url == LATEST_API else "application/octet-stream"})
        try:
            return self.opener.open(request, timeout=15)
        except HTTPError as exc:
            if exc.code in {403, 429}:
                raise UpdateError("GitHub 请求受到限制，请稍后重试，或打开发布页下载。") from exc
            if exc.code == 404:
                raise UpdateError("未找到公开的正式版本或下载文件，请打开 GitHub 发布页查看。") from exc
            raise UpdateError(f"GitHub 返回 HTTP {exc.code}，请稍后重试。") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise UpdateError("无法连接 GitHub，请检查网络或代理后重试。") from exc


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str = ""


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: str
    url: str
    notes: str
    asset: ReleaseAsset | None
    checksums: ReleaseAsset | None = None


@dataclass(frozen=True)
class UpdateCheck:
    current: str
    release: ReleaseInfo

    @property
    def available(self):
        return version_tuple(self.release.version) > version_tuple(self.current)


def _cancelled(cancel):
    if cancel and cancel.is_set():
        raise UpdateCancelled("更新任务已取消。")


def _read_small(http, url, limit, cancel):
    _cancelled(cancel)
    try:
        with http.open(url) as response:
            data = response.read(limit + 1)
    except (OSError, URLError) as exc:
        raise UpdateError("无法完整读取 GitHub 更新信息，请检查网络后重试。") from exc
    _cancelled(cancel)
    if len(data) > limit:
        raise UpdateError("GitHub 返回的更新信息过大，请打开发布页查看。")
    return data


def check_latest(current=__version__, *, http=None, cancel=None):
    version_tuple(current)
    http = http or GitHubHTTP()
    raw = _read_small(http, LATEST_API, 1024 * 1024, cancel)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False:
            raise ValueError("not a stable release")
        tag = data["tag_name"]
        parts = version_tuple(tag)
        version = ".".join(map(str, parts))
        assets = data["assets"]
        if not isinstance(assets, list):
            raise ValueError("invalid asset list")

        def find_asset(name):
            matches = [item for item in assets if isinstance(item, dict) and item.get("name") == name]
            if not matches:
                return None
            if len(matches) != 1:
                raise ValueError("duplicate asset")
            asset = matches[0]
            expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
            if asset.get("browser_download_url") != expected_url or asset.get("state") != "uploaded":
                raise ValueError("invalid asset URL/state")
            size = asset.get("size")
            if type(size) is not int or not 0 < size <= MAX_ASSET_SIZE:
                raise ValueError("invalid asset size")
            digest = asset.get("digest") or ""
            if digest and not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
                raise ValueError("invalid asset digest")
            return ReleaseAsset(name, expected_url, size, digest[7:].lower() if digest else "")

        asset = find_asset(f"AutoDLFresh-{version}-windows-x64.zip")
        checksums = find_asset("SHA256SUMS.txt")
        notes = data.get("body") or "暂无发布说明。"
        if not isinstance(notes, str):
            raise ValueError("invalid notes")
        release = ReleaseInfo(tag, version, f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
                              notes[:20000], asset, checksums)
        return UpdateCheck(current, release)
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("GitHub 更新信息格式不完整或下载地址异常，请打开发布页查看。") from exc


def _expected_hash(release, http, cancel):
    if release.asset.sha256:
        return release.asset.sha256
    if release.checksums:
        raw = _read_small(http, release.checksums.url, 65536, cancel)
        if release.checksums.sha256 and not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), release.checksums.sha256):
            raise UpdateError("更新校验文件不匹配，请稍后重新检查版本。")
        matches = []
        for line in raw.decode("utf-8-sig").splitlines():
            match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
            if match and match[2] == release.asset.name:
                matches.append(match[1].lower())
        if len(matches) == 1:
            return matches[0]
    raise UpdateError("此版本没有可用的 SHA-256 校验信息，请打开官方发布页下载。")


def download_release(release: ReleaseInfo, destination: Path, *, http=None, cancel=None, progress=None):
    if release.asset is None:
        raise UpdateError("此版本尚未提供 Windows 更新包，请打开发布页查看。")
    destination = Path(destination)
    if not destination.is_absolute() or destination.suffix.lower() != ".zip":
        raise UpdateError("请选择完整的 ZIP 文件保存路径。")
    asset = release.asset
    if version_tuple(release.tag) != version_tuple(release.version):
        raise UpdateError("更新版本与发布标签不匹配。")
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{release.tag}/{asset.name}"
    if asset.url != expected_url or asset.name != f"AutoDLFresh-{release.version}-windows-x64.zip":
        raise UpdateError("更新包来源或名称不匹配，已停止下载。")
    http = http or GitHubHTTP()
    _cancelled(cancel)
    checksum = _expected_hash(release, http, cancel)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{destination.name}.", suffix=".part",
                                         dir=destination.parent, delete=False) as output:
            temp = Path(output.name)
            digest, received = hashlib.sha256(), 0
            with http.open(asset.url) as response:
                while True:
                    _cancelled(cancel)
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > asset.size:
                        raise UpdateError("更新包大小与发布信息不符，请重新检查版本。")
                    output.write(chunk)
                    digest.update(chunk)
                    if progress:
                        progress(received, asset.size)
            _cancelled(cancel)
            if received != asset.size or not hmac.compare_digest(digest.hexdigest(), checksum):
                raise UpdateError("更新包不完整或 SHA-256 校验失败，已丢弃本次下载。")
            output.flush()
            os.fsync(output.fileno())
        with zipfile.ZipFile(temp) as archive:
            if "AutoDLFresh/AutoDLFresh.exe" not in archive.namelist():
                raise UpdateError("下载文件不是 AutoDL Fresh Windows 程序包。")
        _cancelled(cancel)
        # Preserve any existing user file until the new package is verified.
        os.replace(temp, destination)
        return destination
    except (OSError, URLError, zipfile.BadZipFile, UnicodeError) as exc:
        raise UpdateError("下载未完成，请检查网络、可用空间和保存路径后重试。") from exc
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


class UpdateWorker(QThread):
    checked = Signal(object)
    downloaded = Signal(object)
    progress = Signal(int, int)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, mode, *, release=None, destination=None, manual=True, parent=None):
        super().__init__(parent)
        self.mode, self.release, self.destination, self.manual = mode, release, destination, manual
        self.cancel = threading.Event()

    def run(self):
        try:
            if self.mode == "check":
                self.checked.emit(check_latest(cancel=self.cancel))
            elif self.mode == "download":
                path = download_release(self.release, self.destination, cancel=self.cancel, progress=self.progress.emit)
                self.downloaded.emit(path)
            else:
                raise UpdateError("未知的更新任务。")
        except UpdateCancelled:
            self.cancelled.emit()
        except UpdateError as exc:
            if self.cancel.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("更新任务未完成，请稍后重试或打开 GitHub 发布页。")
