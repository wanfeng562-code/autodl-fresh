import os
import sys

from autodl_fresh.platform_support import external_environment


def test_frozen_launch_does_not_leak_qt_library_paths(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    system = tmp_path / "system-browser"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(system), str(bundle), str(bundle / "PySide6")]))
    assert external_environment()["PATH"] == str(system)
    assert str(bundle) in os.environ["PATH"]  # The parent process is unchanged.


def test_frozen_restart_creates_an_independent_application_process(monkeypatch, tmp_path):
    from autodl_fresh import platform_support
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "AutoDLFresh.exe"))
    calls = []
    monkeypatch.setattr(platform_support, "launch_external", lambda args, **kwargs: calls.append((args, kwargs)))
    platform_support.restart_application()
    args, kwargs = calls[0]
    assert args == [sys.executable, "--restart"]
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "--minimized" not in args
