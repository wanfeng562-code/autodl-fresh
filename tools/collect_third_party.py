"""Collect exact upstream sources and notices for the current Windows release."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import ssl
import tarfile
import tomllib
import urllib.request
import zipfile

import certifi

ROOT = Path(__file__).resolve().parent.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
QT = "6.10.2"
SOURCES = {
    "pyside-setup-everywhere-src-6.10.2.tar.xz":
        "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.2-src/pyside-setup-everywhere-src-6.10.2.tar.xz",
    **{f"{name}-everywhere-src-{QT}.tar.xz":
       f"https://download.qt.io/official_releases/qt/6.10/{QT}/submodules/{name}-everywhere-src-{QT}.tar.xz"
       for name in ("qtbase", "qtsvg", "qtimageformats")},
}


def main():
    for package in ("PySide6", "shiboken6"):
        if metadata.version(package) != QT:
            raise SystemExit(f"This release requires {package} {QT}; update the source mapping for other versions.")
    cache = ROOT / ".scratch" / "upstream-sources"
    cache.mkdir(parents=True, exist_ok=True)
    licenses = ROOT / "licenses" / "third-party"
    licenses.mkdir(parents=True, exist_ok=True)

    def download(item):
        name, url = item
        target = cache / name
        if not target.exists():
            temp = target.with_suffix(target.suffix + ".part")
            request = urllib.request.Request(url, headers={"User-Agent": f"AutoDLFresh-Release/{VERSION}"})
            context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(request, timeout=60, context=context) as response, temp.open("wb") as file:
                shutil.copyfileobj(response, file)
            temp.replace(target)
        with tarfile.open(target, "r:xz") as archive:
            # Read only regular notice files. Never extract links or execute source.
            for entry in archive.getmembers():
                parts = Path(entry.name).parts
                if not entry.isfile() or ".." in parts or Path(entry.name).is_absolute():
                    continue
                base = parts[-1].lower()
                notice_name = (base in {"license", "copying", "copyright", "notice"}
                               or base.startswith(("license.", "license-", "copying.", "copying-",
                                                   "copyright.", "notice.")))
                selected = ("LICENSES" in parts or notice_name or base == "qt_attribution.json")
                if selected:
                    relative = Path(*parts[1:])
                    output = licenses / name.removesuffix(".tar.xz") / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(entry) as source, output.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
        return {"file": name, "url": url, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}

    with ThreadPoolExecutor(max_workers=4) as executor:
        sources = list(executor.map(download, SOURCES.items()))
    for package in ("playwright", "pyee", "greenlet", "typing_extensions", "PyInstaller"):
        distribution = metadata.distribution(package)
        for entry in distribution.files or []:
            if entry.name.lower().startswith(("license", "copying", "notice")):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    output = licenses / f"{package}-{distribution.version}" / source.name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, output)
    import playwright
    driver = Path(playwright.__file__).parent / "driver"
    for source in (driver / "LICENSE", driver / "package/LICENSE", driver / "package/NOTICE",
                   driver / "package/ThirdPartyNotices.txt"):
        output = licenses / "playwright-driver" / source.relative_to(driver)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
    import sys
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    output = licenses / "Python-3.12.4" / "LICENSE.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(python_license, output)
    (licenses / "sources.json").write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    releases = ROOT / "releases"
    releases.mkdir(exist_ok=True)
    artifact = releases / f"AutoDLFresh-{VERSION}-third-party-sources.zip"
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_STORED) as bundle:
        for name in SOURCES:
            bundle.write(cache / name, name)
        bundle.write(licenses / "sources.json", "sources.json")
        bundle.write(ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md")
    print(json.dumps({"source_bundle": str(artifact), "size": artifact.stat().st_size,
                      "notice_files": sum(1 for file in licenses.rglob("*") if file.is_file())}))


if __name__ == "__main__":
    main()
