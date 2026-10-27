"""Package the Windows folder with notices, without personal runtime data."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    app = ROOT / "dist" / "AutoDLFresh"
    if not (app / "AutoDLFresh.exe").is_file():
        raise SystemExit("Build dist/AutoDLFresh first.")
    for name in ("README.md", "LICENSE", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(ROOT / name, app / name)
    shutil.copytree(ROOT / "licenses", app / "licenses", dirs_exist_ok=True)
    shutil.copytree(ROOT / "docs", app / "docs", dirs_exist_ok=True)
    files = [file for file in app.rglob("*") if file.is_file()]
    private_names = {"auth.json", "settings.json", "pending.json", "scheduler.json", "history.json", "Preferences", "app.lock"}
    if any(file.name in private_names or file.name.endswith(".log") or file.is_symlink() for file in files):
        raise SystemExit("Unexpected runtime/private data found in the release folder.")
    releases = ROOT / "releases"
    releases.mkdir(exist_ok=True)
    artifact = releases / "AutoDLFresh-0.5.0-windows-x64.zip"
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(files):
            archive.write(file, Path("AutoDLFresh") / file.relative_to(app))
    assets = [artifact]
    source = releases / "AutoDLFresh-0.5.0-third-party-sources.zip"
    if source.exists():
        assets.append(source)
    checksums = releases / "SHA256SUMS.txt"
    checksums.write_text("".join(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}\n" for file in assets), encoding="ascii")
    for file in [*assets, checksums]:
        print(f"{file.name}: {file.stat().st_size} bytes")


if __name__ == "__main__":
    main()
