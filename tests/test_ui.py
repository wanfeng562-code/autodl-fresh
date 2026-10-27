from PySide6.QtWidgets import QApplication

from autodl_fresh.models import DAY, Instance, RunReport
from autodl_fresh.storage import Store
from autodl_fresh.ui import MainWindow


def test_ui_settings_report_and_search(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path))
    window.timer.stop()
    assert window.stack.count() == 4
    assert not window.settings.schedule_enabled
    report = RunReport(instances=[
        Instance(uuid="abcdefgh-12345678", name="训练环境", status="已关机", charge="按量计费", remaining_seconds=2*DAY),
        Instance(uuid="ijklmnop-12345678", name="其他环境", status="运行中"),
    ])
    window._receive_report(report)
    assert window.table.rowCount() == 2
    window._receive_report(RunReport(errors=["网络断开，未能读取列表"]))
    assert window.table.rowCount() == 2
    assert "上次快照" in window.operation_label.text()
    window.search.setText("训练")
    assert window.table.rowCount() == 1
    window.threshold.setValue(2)
    window._save_settings()
    assert window.store.settings().threshold_days == 2
    window._quitting = True
    window.tray.hide()
    window.close()
    app.processEvents()


def test_a_successful_empty_scan_can_clear_old_rows(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path))
    window.timer.stop()
    window._receive_report(RunReport(instances=[Instance(uuid="abcdefgh-12345678")], scan_complete=True))
    window._receive_report(RunReport(scan_complete=True))
    assert window.table.rowCount() == 0
    window._quitting = True
    window.tray.hide()
    window.close()
    app.processEvents()


def test_startup_repairs_only_proven_legacy_preflight_record(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    uuid = "abcdefgh-12345678"
    store.append_report(RunReport(started_at="2026-10-27T15:26:03+08:00",
                        finished_at="2026-10-27T15:26:11+08:00",
                        instances=[Instance(uuid=uuid)], errors=[f"无法唯一定位实例 {uuid}，已停止。"]))
    store.set_pending({"uuid": uuid, "phase": "intent", "created_at": "2026-10-27T15:26:09+08:00"})
    window = MainWindow(store)
    window.timer.stop()
    assert not store.pending()
    assert window.table.rowCount() == 1
    assert "已修复旧版" in window.logs.toPlainText()
    window._quitting = True
    window.tray.hide()
    window.close()
    app.processEvents()


def test_startup_recovers_last_nonempty_snapshot_from_history(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.append_report(RunReport(instances=[Instance(uuid="abcdefgh-12345678")]))
    store.append_report(RunReport(errors=["待核验失败"]))
    window = MainWindow(store)
    window.timer.stop()
    assert window.table.rowCount() == 1
    assert window.report.errors
    window._quitting = True
    window.tray.hide()
    window.close()
    app.processEvents()


def test_settings_can_copy_data_and_request_restart(tmp_path, monkeypatch):
    from autodl_fresh import ui
    from autodl_fresh.locations import configured_directory
    monkeypatch.delenv("AUTODL_FRESH_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "launcher"))
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "source")
    store.write("auth.json", {"cookies": [], "origins": []})
    target = tmp_path / "new"
    target.mkdir()
    window = MainWindow(store)
    window.timer.stop()
    restarted = []
    monkeypatch.setattr(window, "_restart_after_migration", lambda: restarted.append(True))
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *args: str(target))
    try:
        window._choose_data_directory()
        assert window.save_button.text() == "保存并重启"
        assert window.data_directory_field.text() == str(target)
        window.browser_visibility.setCurrentIndex(window.browser_visibility.findData("foreground"))
        window._save_settings()
        assert restarted == [True]
        assert not window.timer.isActive()
        assert window._directory_switch_pending
        assert configured_directory() == target
        assert Store(target).settings().browser_visibility == "foreground"
        assert (target / "auth.json").read_bytes() == store.auth_path.read_bytes()
        assert store.auth_path.exists()
        window._start("preview")
        assert window.worker is None  # Old process cannot run another job.
    finally:
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()


def test_invalid_directory_does_not_request_restart(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTODL_FRESH_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "launcher"))
    app = QApplication.instance() or QApplication([])
    target = tmp_path / "existing"
    target.mkdir()
    (target / "do-not-overwrite.txt").write_text("existing", encoding="utf-8")
    window = MainWindow(Store(tmp_path / "source"))
    window.timer.stop()
    window._pending_data_dir = target
    restarted = []
    monkeypatch.setattr(window, "_restart_after_migration", lambda: restarted.append(True))
    try:
        window._save_settings()
        assert "空文件夹" in window.save_feedback.text()
        assert not restarted and not window._directory_switch_pending
        assert window.timer.isActive()
        assert (target / "do-not-overwrite.txt").read_text(encoding="utf-8") == "existing"
    finally:
        window.timer.stop()
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()


def test_pagination_search_size_and_shrinking_result(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    window = MainWindow(store)
    window.timer.stop()
    report = RunReport(scan_complete=True, instances=[
        Instance(uuid=f"uuid-{index:03d}", name=f"实例{index}", page=index // 10 + 1) for index in range(23)
    ])
    try:
        window._receive_report(report)
        assert window.table.rowCount() == 10
        assert window.metrics[0].text() == "23"
        assert not window.previous_page.isEnabled() and window.next_page.isEnabled()
        window.next_page.click()
        assert "uuid-010" in window.table.item(0, 0).text()
        window.page_number.setValue(3)
        assert window.table.rowCount() == 3
        assert "uuid-020" in window.table.item(0, 0).text()
        assert not window.next_page.isEnabled()
        window.search.setText("uuid-022")
        assert window._current_page == 1 and window.table.rowCount() == 1
        assert "uuid-022" in window.table.item(0, 0).text()
        window.search.clear()
        window.page_size.setCurrentIndex(window.page_size.findData(20))
        assert store.settings().page_size == 20
        assert window.table.rowCount() == 20 and window._current_page == 1
        window.next_page.click()
        assert window.table.rowCount() == 3
        window._receive_report(RunReport(scan_complete=True, instances=report.instances[:2]))
        assert window._current_page == 1 and window.table.rowCount() == 2
        assert not window.next_page.isEnabled() and not window.previous_page.isEnabled()
        calls = []
        monkeypatch.setattr(window, "_start", lambda mode: calls.append(mode))
        window.run_all_button.click()
        assert calls == ["refresh_all"]
    finally:
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()


def test_full_refresh_counts_all_pages_and_records_the_mode(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    window = MainWindow(store)
    window.timer.stop()
    report = RunReport(scan_complete=True, refresh_all=True, instances=[
        Instance(uuid=f"uuid-{index:03d}", status="已关机", charge="按量计费", remaining_seconds=4 * DAY)
        for index in range(12)
    ], outcomes={"uuid-000": "刷新成功", "uuid-001": "刷新成功"})
    try:
        window._receive_report(report)
        assert window.table.rowCount() == 10
        assert window.metrics[0].text() == "12"
        assert window.metrics[1].text() == "10"
        assert window.table.item(2, 4).text() == "待刷新"
        store.append_report(report)
        window._load_history()
        assert window.history_table.item(0, 1).text() == "全部刷新"
    finally:
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()
