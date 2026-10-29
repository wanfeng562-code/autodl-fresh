from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
import pytest

from autodl_fresh.models import Instance, RunReport
from autodl_fresh.rental_ui import RentalPanel
from autodl_fresh.storage import Store
from autodl_fresh.ui import MainWindow
from autodl_fresh.storage import Settings
from autodl_fresh.worker import BrowserWorker


def source(uuid="sourceaa-12345678", **kwargs):
    return Instance(uuid=uuid, name="测试源", region="测试地区 / A01机", status="已关机",
                    gpu_name="RTX 4090", gpu_count=1, **kwargs)


def test_panel_requires_budget_consent_and_current_source_catalog(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    panel = RentalPanel(store)
    assert panel.interval.value() == 1 and panel.interval.minimum() == 1
    calls = []
    panel.requested.connect(lambda mode, options: calls.append((mode, options)))
    panel.set_instances([source(), Instance("runninga-12345678", status="运行中")])
    assert panel.source.count() == 1
    panel._start()
    assert "先加载" in panel.status.text() and calls == []
    panel.catalog_ready({"source": source().to_dict(), "gpu_names": ["RTX 3090", "RTX 4090"],
                         "gpu_prices": {"RTX 4090": {"min": "2.00", "max": "2.00"}}})
    assert panel.gpus.item(0).data(Qt.ItemDataRole.UserRole) == "RTX 4090"
    assert "2.00" in panel.gpus.item(0).text()
    assert panel.gpus.item(0).checkState() == Qt.CheckState.Checked
    panel._start()
    assert "确认" in panel.status.text() and calls == []
    panel.consent.setChecked(True)
    panel._start()
    assert calls[0][0] == "rental" and calls[0][1].gpu_names == ["RTX 4090"]
    assert "max_hourly_price" not in store.read("rental-options.json")
    panel.set_busy(True, "rental")
    assert not panel.config.isEnabled() and panel.stop_button.isEnabled()
    panel.show_task({"message": "暂无 GPU", "phase": "monitoring"})
    assert panel.stop_button.isEnabled()
    panel.set_busy(False)
    panel.set_instances([source()])  # Finishing a probe must preserve its catalog.
    assert panel.catalog_source == source().uuid and panel.gpus.count() == 2
    panel.set_instances([source("anothera-12345678")])
    assert not panel.catalog_source and panel.gpus.count() == 0 and not panel.consent.isChecked()
    panel.close()
    app.processEvents()


def test_pending_creation_blocks_rental_and_enables_only_verification(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    task = {"phase": "accepted", "target_uuid": "targetaa-87654321", "message": "拷贝中"}
    store.write("rental-task.json", task)
    panel = RentalPanel(store)
    panel.set_instances([source()])
    panel.catalog_ready({"source": source().to_dict(), "gpu_names": ["RTX 4090"]})
    panel.consent.setChecked(True)
    calls = []
    panel.requested.connect(lambda *args: calls.append(args))
    panel._start()
    assert "尚未确认" in panel.status.text() and not calls
    assert panel.verify_button.isEnabled()
    panel.verify_button.click()
    assert calls == [("rental_verify", None)]
    panel.close()
    app.processEvents()


def test_saved_alternative_models_and_card_count_restore_without_starting(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.write("rental-options.json", {"source_uuid": source().uuid, "gpu_names": ["RTX 3090", "RTX 4090"],
                                        "gpu_count": 2, "max_hourly_price": "3.50"})
    panel = RentalPanel(store)
    calls = []
    panel.requested.connect(lambda *args: calls.append(args))
    panel.set_instances([source()])
    panel.catalog_ready({"source": source().to_dict(), "gpu_names": ["RTX 4090", "RTX 3090"]})
    assert panel.count.currentData() == 2
    assert panel.options().gpu_names == ["RTX 3090", "RTX 4090"]
    assert not hasattr(panel, "hourly") and not hasattr(panel, "disk")
    assert not panel.consent.isChecked() and not calls
    panel.close()
    app.processEvents()


def test_main_window_shares_worker_and_retains_catalog_after_probe(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.write("auth.json", {})
    window = MainWindow(store)
    window.timer.stop()
    try:
        window._receive_report(RunReport(scan_complete=True, instances=[source()]))
        assert window.rental_panel.source.count() == 1
        window.rental_panel.catalog_ready({"source": source().to_dict(), "gpu_names": ["RTX 4090"]})
        window.rental_panel.set_busy(True, "rental_probe")
        window._worker_finished()
        assert window.rental_panel.catalog_source == source().uuid
        store.write("rental-task.json", {"phase": "uncertain"})
        window._start("refresh_all")
        assert window.worker is None
        assert "尚未核验" in window.logs.toPlainText()
    finally:
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()


@pytest.mark.parametrize("visibility", ["background", "foreground"])
@pytest.mark.parametrize("mode", ["preview", "rental_probe", "rental", "rental_verify"])
def test_every_read_and_rental_action_obeys_saved_browser_visibility(tmp_path, monkeypatch, visibility, mode):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.write("auth.json", {})
    store.save_settings(Settings(browser_visibility=visibility))
    monkeypatch.setattr(BrowserWorker, "start", lambda worker: None)
    window = MainWindow(store)
    window.timer.stop()
    try:
        window._start(mode)
        assert window.worker is not None and window.worker.mode == mode
        expected = "background" if mode in {"preview", "rental_probe"} else visibility
        assert window.worker.settings.browser_visibility == expected
        assert window.settings.browser_visibility == visibility
        if expected == "background":
            assert window.worker.settings.browser_mode == "native"
    finally:
        window._worker_finished()
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()


def test_rental_success_uses_persistent_banner_sound_and_taskbar_once(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path))
    window.timer.stop()
    sounds, alerts, notifications = [], [], []
    monkeypatch.setattr(QApplication, 'beep', lambda: sounds.append(True))
    monkeypatch.setattr(QApplication, 'alert', lambda *args: alerts.append(args))
    monkeypatch.setattr(window.tray, 'showMessage', lambda *args: notifications.append(args))
    task = {'phase':'accepted', 'target_uuid':'targetaa-12345678', 'message':'克隆实例已创建，正在等待完成'}
    try:
        window._rental_updated(task)
        assert not window.rental_success_banner.isHidden()
        assert task['target_uuid'] in window.rental_success_text.text()
        assert '正在等待完成' in window.rental_success_text.text()
        assert len(sounds) == len(alerts) == len(notifications) == 1
        window._rental_updated({**task, 'phase':'completed', 'message':'GPU 与数据盘已就绪'})
        assert '数据盘已就绪' in window.rental_success_text.text()
        assert len(sounds) == len(alerts) == len(notifications) == 1
    finally:
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()


def test_cached_quotes_load_without_browser_and_updates_preserve_unsaved_gpu_choices(tmp_path):
    from autodl_fresh.quote_cache import QuoteCache
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    data = {"source": source().to_dict(), "gpu_names": ["RTX 4090", "RTX 3090"],
            "gpu_prices": {"RTX 4090": {"min": "1.98", "max": "1.98"}}}
    QuoteCache(store).update(data, complete=True)
    panel = RentalPanel(store)
    calls = []
    panel.requested.connect(lambda *args: calls.append(args))
    try:
        panel.set_instances([source()])
        panel._probe()
        assert not calls and '上次核对' in panel.quote_hint.text()
        panel.gpus.item(0).setCheckState(Qt.CheckState.Unchecked)
        panel.gpus.item(1).setCheckState(Qt.CheckState.Checked)
        panel.gpus.setCurrentRow(1)
        panel._move(-1)
        panel.count.setCurrentIndex(panel.count.findData(2))
        panel.catalog_ready({**data, "background": True})
        assert panel.options().gpu_names == ['RTX 3090'] and panel.count.currentData() == 2
        assert panel.gpus.item(0).data(Qt.ItemDataRole.UserRole) == 'RTX 3090'
        panel._probe(force=True)
        assert len(calls) == 1 and calls[0][0] == 'rental_probe'
    finally:
        panel.close()
        app.processEvents()



@pytest.mark.parametrize('legacy_setting', [True, False])
def test_startup_and_repeated_ticks_never_recheck_quotes_even_with_old_auto_settings(tmp_path, monkeypatch, legacy_setting):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.write('auth.json', {})
    store.write('settings.json', {'quote_auto_enabled':legacy_setting, 'quote_interval_hours':1})
    calls = []
    monkeypatch.setattr(BrowserWorker, 'start', lambda worker: calls.append(worker.mode))
    window = MainWindow(store)
    window.timer.stop()
    try:
        item = source()
        item.region = '西北B区 / A01机'
        window._receive_report(RunReport(scan_complete=True, instances=[item]))
        assert window.rental_panel.gpus.count() == 20
        for _ in range(10):
            window._tick()
        window.rental_panel._probe()
        assert window.worker is None and not calls
        assert '至少需要 2 分钟' in window.rental_panel.quote_note.text()
        assert not hasattr(window.settings, 'quote_auto_enabled')
    finally:
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()


def test_missing_quotes_require_explicit_recheck_click(tmp_path):
    app = QApplication.instance() or QApplication([])
    panel = RentalPanel(Store(tmp_path))
    calls = []
    panel.requested.connect(lambda *args: calls.append(args))
    try:
        panel.set_instances([source()])
        panel._probe()
        assert not calls and '至少需要 2 分钟' in panel.status.text()
        panel.requote_button.click()
        assert len(calls) == 1 and calls[0][0] == 'rental_probe'
    finally:
        panel.close()
        app.processEvents()


def test_removed_automatic_quote_job_cannot_be_dispatched(tmp_path):
    with pytest.raises(ValueError, match='不支持'):
        BrowserWorker(Settings(), Store(tmp_path), 'rental_quotes')


def test_api_only_recovery_preserves_auth_and_requests_browser_cleanup(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from autodl_fresh import worker as worker_module, rental_browser
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    auth = {'cookies':[], 'origins':[{'origin':'https://www.autodl.com',
                                    'localStorage':[{'name':'token','value':'fixture-only'}]}]}
    store.write('auth.json', auth)
    store.write('rental-task.json', {'phase':'accepted','target_uuid':'targetaa-12345678',
                                   'options':{'source_uuid':source().uuid,'gpu_names':['RTX 4090']}})
    events = []
    @contextmanager
    def session(*args, **kwargs):
        assert kwargs['close_browser_on_exit'] is True
        yield SimpleNamespace(), SimpleNamespace(storage_state=lambda **kw: (_ for _ in ()).throw(
            AssertionError('API-only recovery must preserve the saved website state')))
        events.append('browser_closed')
    class Adapter:
        def __init__(self, console, *, auth_state):
            assert auth_state == auth
        def show_status(self, task):
            pass
        def verify(self, task):
            events.append('clone_completed')
            return Instance(task['target_uuid'])
    monkeypatch.setattr(worker_module, 'browser_session', session)
    monkeypatch.setattr(rental_browser, 'CloneConsole', Adapter)
    errors = []
    worker = BrowserWorker(Settings(), store, 'rental_verify')
    worker.failed.connect(errors.append)
    worker.run()
    assert not errors and events == ['clone_completed','browser_closed']
    assert store.read('auth.json') == auth
    app.processEvents()
