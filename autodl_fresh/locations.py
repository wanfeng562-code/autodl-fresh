"""Data-directory selection with non-destructive, app-file-only migration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

DATA_FILES = (
    "settings.json", "auth.json", "pending.json", "scheduler.json", "history.json",
    "manual_resolutions.json", "recovery_events.json", "activity.log", "activity.previous.log",
    "rental-options.json", "rental-task.json", "rental-history.json",
    "gpu-quotes.json",
)


def default_directory() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "AutoDLFresh"


def configuration_directory() -> Path:
    override = os.environ.get("AUTODL_FRESH_DATA_DIR")
    return Path(override).resolve() if override else default_directory()


def configured_directory() -> Path:
    if override := os.environ.get("AUTODL_FRESH_DATA_DIR"):
        return Path(override).resolve()
    anchor = default_directory()
    pointer = anchor / "data-location.json"
    if not pointer.exists():
        return anchor
    try:
        directory = Path(json.loads(pointer.read_text(encoding="utf-8"))["directory"])
        if not directory.is_absolute():
            raise ValueError("relative data directory")
        return directory.resolve()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("数据目录配置无法读取，请检查默认目录中的 data-location.json。") from exc


def migrate_data(source: Path, destination: Path, *, anchor: Path | None = None) -> Path:
    """Copy only app state to an empty directory, commit its pointer last.

    Caller must stop jobs first. Browser caches, profiles and locks are excluded.
    Original data is always retained; pre-existing destination files are never
    overwritten, including files that appear after the initial emptiness check.
    """
    if not destination.is_absolute():
        raise ValueError("请选择绝对路径的数据目录。")
    source, destination = source.resolve(), destination.resolve()
    if destination == source:
        return destination
    if destination.parent == destination:
        raise ValueError("请选择一个独立文件夹，不能使用磁盘根目录。")
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("新旧数据目录不能互相包含，请选择独立的空文件夹。")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("新数据目录必须是空文件夹，以免覆盖已有数据。")
    destination.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name in DATA_FILES:
            original = source / name
            if not original.exists():
                continue
            if original.is_symlink() or not original.is_file():
                raise ValueError(f"应用数据文件 {name} 类型异常，已停止切换目录。")
            target = destination / name
            with target.open("xb") as output:
                created.append(target)
                with original.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output)
                output.flush()
                os.fsync(output.fileno())
        config = anchor or default_directory()
        config.mkdir(parents=True, exist_ok=True)
        pointer = config / "data-location.json"
        tmp = config / "data-location.json.tmp"
        with tmp.open("w", encoding="utf-8") as file:
            json.dump({"version": 1, "directory": str(destination)}, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, pointer)
    except Exception:
        for target in created:
            target.unlink(missing_ok=True)
        raise
    return destination
