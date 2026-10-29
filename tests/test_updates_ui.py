from dataclasses import replace
from pathlib import Path
import threading
import time

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from autodl_fresh import __version__, updater, ui
from autodl_fresh.storage import Store
from autodl_fresh.ui import MainWindow
from autodl_fresh.updater import ReleaseAsset, ReleaseInfo, UpdateCancelled, UpdateCheck, UpdateError

FUTURE_VERSION = f"{int(__version__.split('.')[0]) + 1}.0.0"


def release_info(asset=True):
    return ReleaseInfo(f"v{FUTURE_VERSION}", FUTURE_VERSION,
                       f"https://github.com/wanfeng562-code/autodl-fresh/releases/tag/v{FUTURE_VERSION}",
                       "新功能\n<script>plain text, never HTML</script>",
                       ReleaseAsset(f"AutoDLFresh-{FUTURE_VERSION}-windows-x64.zip", "https://github.com/fixture", 10) if asset else None)


def wait_finished(window):
    deadline = time.monotonic() + 3
    while window.update_worker and time.monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(5)
    assert window.update_worker is None


def dispose(window):
    if window.update_worker:
        window.update_worker.cancel.set()
        wait_finished(window)
    window.worker = None
    window._quitting = True
    window.timer.stop()
    window.tray.hide()
    window.close()
    QApplication.processEvents()


def test_check_prompt_download_and_open_folder_do_not_interrupt_browser_work(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path / "data"))
    window.timer.stop()
    release = release_info()
    monkeypatch.setattr(updater, "check_latest", lambda **kwargs: UpdateCheck(__version__, release))
    target = tmp_path / release.asset.name
    def download(info, destination, *, cancel, progress):
        progress(5, 10)
        destination.write_bytes(b"verified test fixture")
        progress(10, 10)
        return destination
    monkeypatch.setattr(updater, "download_release", download)
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", lambda *args: (str(target), "ZIP"))
    opened = []
    monkeypatch.setattr(ui, "open_directory", lambda directory: opened.append(Path(directory)))
    browser_work = object()
    window.worker = browser_work
    try:
        window.update_check_button.click()
        wait_finished(window)
        assert window.worker is browser_work
        assert FUTURE_VERSION in window.update_status_label.text()
        assert not window.update_banner.isHidden()
        assert window.update_download_button.isEnabled()
        assert "<script>" in window.update_notes.toPlainText()
        window.update_download_button.click()
        wait_finished(window)
        assert window.worker is browser_work
        assert window.downloaded_update == target
        assert target.read_bytes() == b"verified test fixture"
        assert "校验" in window.update_status_label.text()
        window.update_folder_button.click()
        assert opened == [tmp_path]
        app.processEvents()
    finally:
        dispose(window)


def test_missing_package_current_version_and_silent_auto_failure(tmp_path, monkeypatch):
    _app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path))
    window.timer.stop()
    try:
        monkeypatch.setattr(updater, "check_latest", lambda **kwargs: UpdateCheck(__version__, release_info(False)))
        window._check_updates(manual=True)
        wait_finished(window)
        assert "尚未提供" in window.update_status_label.text()
        assert not window.update_download_button.isEnabled()
        current = replace(release_info(), tag=f"v{__version__}", version=__version__)
        monkeypatch.setattr(updater, "check_latest", lambda **kwargs: UpdateCheck(__version__, current))
        window._check_updates(manual=True)
        wait_finished(window)
        assert "已是最新" in window.update_status_label.text()
        assert window.update_banner.isHidden()
        def fail(**kwargs):
            raise UpdateError("GitHub 请求受到限制，请稍后重试。")
        monkeypatch.setattr(updater, "check_latest", fail)
        original_logs = window.logs.toPlainText()
        window._check_updates(manual=False)
        wait_finished(window)
        assert "受到限制" in window.update_status_label.text()
        assert window.logs.toPlainText() == original_logs
        window._check_updates(manual=True)
        wait_finished(window)
        assert "软件更新" in window.logs.toPlainText()
    finally:
        dispose(window)


def test_quit_cancels_update_and_waits_for_worker_cleanup(tmp_path, monkeypatch):
    _app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path))
    window.timer.stop()
    started = threading.Event()
    def check(*, cancel):
        started.set()
        cancel.wait(2)
        raise UpdateCancelled()
    monkeypatch.setattr(updater, "check_latest", check)
    quit_calls = []
    monkeypatch.setattr(ui.QApplication, "quit", lambda: quit_calls.append(True))
    try:
        window._check_updates(manual=False)
        assert started.wait(1)
        window._quit()
        wait_finished(window)
        assert quit_calls == [True]
        assert not window.timer.isActive()
        assert "取消" in window.update_status_label.text()
    finally:
        dispose(window)


def test_startup_check_preference_is_saved_and_respected(tmp_path, monkeypatch):
    _app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path))
    window.timer.stop()
    checks = []
    monkeypatch.setattr(window, "_check_updates", lambda **kwargs: checks.append(kwargs))
    try:
        window.auto_update_check.setChecked(False)
        window._save_settings()
        assert not window.store.settings().auto_check_updates
        window.check_updates_automatically()
        assert checks == []
        window.settings = replace(window.settings, auto_check_updates=True)
        window.check_updates_automatically()
        assert checks == [{"manual": False}]
    finally:
        dispose(window)
