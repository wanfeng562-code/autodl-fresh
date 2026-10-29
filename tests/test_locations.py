import json
from pathlib import Path

import pytest

from autodl_fresh.locations import (
    configuration_directory, configured_directory, default_directory, migrate_data,
)


def test_migration_preserves_app_state_and_excludes_browser_profiles(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTODL_FRESH_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "launcher"))
    source, target = tmp_path / "old", tmp_path / "new"
    source.mkdir()
    (source / "auth.json").write_text('{"fixture":"login"}', encoding="utf-8")
    (source / "pending.json").write_text('{"phase":"accepted"}', encoding="utf-8")
    (source / "settings.json").write_text('{"threshold_days":3}', encoding="utf-8")
    (source / "rental-task.json").write_text('{"phase":"submitted"}', encoding="utf-8")
    (source / "gpu-quotes.json").write_text('{"regions":{}}', encoding="utf-8")
    (source / "app.lock").write_text("do not copy", encoding="utf-8")
    (source / "browser-autodl-only-v1-msedge").mkdir()
    lock_root = configuration_directory()
    assert migrate_data(source, target) == target
    assert (target / "auth.json").read_bytes() == (source / "auth.json").read_bytes()
    assert (target / "pending.json").exists()
    assert (target / "rental-task.json").read_bytes() == (source / "rental-task.json").read_bytes()
    assert (target / "gpu-quotes.json").read_bytes() == (source / "gpu-quotes.json").read_bytes()
    assert not (target / "app.lock").exists()
    assert not (target / "browser-autodl-only-v1-msedge").exists()
    assert source.exists() and (source / "auth.json").exists()
    assert configured_directory() == target
    assert configuration_directory() == lock_root  # One global lock after moving.


def test_existing_directory_contents_are_not_overwritten(tmp_path):
    source, target = tmp_path / "old", tmp_path / "new"
    source.mkdir()
    target.mkdir()
    (target / "auth.json").write_text("existing", encoding="utf-8")
    with pytest.raises(ValueError, match="空文件夹"):
        migrate_data(source, target, anchor=tmp_path / "anchor")
    assert (target / "auth.json").read_text(encoding="utf-8") == "existing"


def test_copy_failure_keeps_source_and_old_directory_pointer(tmp_path, monkeypatch):
    from autodl_fresh import locations
    source, target, anchor = tmp_path / "old", tmp_path / "new", tmp_path / "anchor"
    source.mkdir()
    anchor.mkdir()
    (source / "settings.json").write_text("{}", encoding="utf-8")
    (source / "auth.json").write_text("{}", encoding="utf-8")
    pointer = anchor / "data-location.json"
    pointer.write_text(json.dumps({"directory": str(source)}), encoding="utf-8")
    original = locations.shutil.copyfileobj
    calls = []

    def fail_second_copy(input_file, output):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("fixture failure")
        return original(input_file, output)

    monkeypatch.setattr(locations.shutil, "copyfileobj", fail_second_copy)
    with pytest.raises(OSError):
        migrate_data(source, target, anchor=anchor)
    assert json.loads(pointer.read_text(encoding="utf-8"))["directory"] == str(source)
    assert list(target.iterdir()) == []
    assert (source / "auth.json").exists()


@pytest.mark.parametrize("kind", ["relative", "nested", "parent"])
def test_invalid_directory_layout_is_rejected(tmp_path, kind):
    source = tmp_path / "old"
    source.mkdir()
    target = Path("relative") if kind == "relative" else source / "child" if kind == "nested" else tmp_path
    with pytest.raises(ValueError):
        migrate_data(source, target, anchor=tmp_path / "anchor")


def test_environment_override_has_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "launcher"))
    anchor = default_directory()
    anchor.mkdir(parents=True)
    (anchor / "data-location.json").write_text(json.dumps({"directory": str(tmp_path / "chosen")}), encoding="utf-8")
    monkeypatch.setenv("AUTODL_FRESH_DATA_DIR", str(tmp_path / "override"))
    assert configured_directory() == tmp_path / "override"
