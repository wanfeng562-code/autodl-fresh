"""Package the Windows folder with notices, without personal runtime data."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=ROOT / "dist" / "AutoDLFresh")
    args = parser.parse_args()
    app = args.app_dir.resolve()
    if not (app / "AutoDLFresh.exe").is_file():
        raise SystemExit("Build dist/AutoDLFresh first.")
    for name in ("README.md", "LICENSE", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(ROOT / name, app / name)
    shutil.copytree(ROOT / "licenses", app / "licenses", dirs_exist_ok=True)
    shutil.copytree(ROOT / "docs", app / "docs", dirs_exist_ok=True)
    files = [file for file in app.rglob("*") if file.is_file()]
    private_names = {"auth.json", "settings.json", "pending.json", "scheduler.json", "history.json",
                     "rental-options.json", "rental-task.json", "rental-history.json", "gpu-quotes.json", "Preferences", "app.lock"}
    if any(file.name in private_names or file.name.endswith(".log") or file.is_symlink() for file in files):
        raise SystemExit("Unexpected runtime/private data found in the release folder.")
    releases = ROOT / "releases"
    releases.mkdir(exist_ok=True)
    artifact = releases / f"AutoDLFresh-{VERSION}-windows-x64.zip"
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(files):
            archive.write(file, Path("AutoDLFresh") / file.relative_to(app))
    assets = [artifact]
    source = releases / f"AutoDLFresh-{VERSION}-third-party-sources.zip"
    if source.exists():
        assets.append(source)
    checksums = releases / "SHA256SUMS.txt"
    checksums.write_text("".join(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}\n" for file in assets), encoding="ascii")
    for file in [*assets, checksums]:
        print(f"{file.name}: {file.stat().st_size} bytes")


if __name__ == "__main__":
    main()
