from contextlib import contextmanager
from copy import deepcopy
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from autodl_fresh.browser import ConsoleError
from autodl_fresh.models import Instance
from autodl_fresh.rental import (
    PENDING_PHASES, RentalConfirmation, RentalEngine, confirm_rental_result,
)
from autodl_fresh.rental_ui import RentalPanel
from autodl_fresh.storage import Store
from autodl_fresh.ui import MainWindow


def pending():
    return {"phase": "uncertain", "target_uuid": "targetaa-87654321", "started_at": "fixture-start",
            "options": {"source_uuid": "sourceaa-12345678", "gpu_names": ["RTX 4090"]},
            "message": "新实例仍需核验"}


@pytest.mark.parametrize("outcome,phase", [("completed", "completed"), ("released", "resolved")])
def test_idle_manual_result_unblocks_new_rental_and_preserves_auditable_record(tmp_path, outcome, phase):
    store = Store(tmp_path)
    task = pending()
    store.write("rental-task.json", task)
    result = confirm_rental_result(store, outcome, expected=task)
    assert result["phase"] == phase and result["phase"] not in PENDING_PHASES
    assert result["verification"] == "manual" and result["manual_result"] == outcome
    assert result["target_uuid"] == task["target_uuid"] and result["options"] == task["options"]
    assert store.read("rental-history.json") == [result]
    assert confirm_rental_result(store, outcome, expected=task) == result
    assert len(store.read("rental-history.json")) == 1


def test_stale_confirmation_does_not_resolve_a_different_rental(tmp_path):
    store = Store(tmp_path)
    old, current = pending(), pending()
    current.update(started_at="next-task", target_uuid="different-12345678")
    store.write("rental-task.json", current)
    assert confirm_rental_result(store, "released", expected=old) == current
    assert store.read("rental-history.json", []) == []


def test_unknown_submission_requires_explicit_not_created_result(tmp_path):
    store = Store(tmp_path)
    task = pending()
    task["target_uuid"] = ""
    store.write("rental-task.json", task)
    with pytest.raises(ValueError, match="新实例 ID"):
        confirm_rental_result(store, "completed", expected=task)
    result = confirm_rental_result(store, "not_created", expected=task)
    assert result["phase"] == "resolved" and result["manual_result"] == "not_created"


@pytest.mark.parametrize("outcome", ["completed", "released"])
@pytest.mark.parametrize("late_error", [False, True])
def test_confirmation_during_verification_wins_over_return_or_browser_error(tmp_path, outcome, late_error):
    store, confirmation = Store(tmp_path), RentalConfirmation()
    task = pending()
    store.write("rental-task.json", task)
    class Adapter:
        def verify(self, task):
            confirmation.request(outcome, task)
            if late_error:
                raise ConsoleError("Browser closed after user confirmation")
            return Instance(task["target_uuid"])
    updates = []
    result = RentalEngine(Adapter(), store, None, confirmation=confirmation,
                          changed=lambda task: updates.append(deepcopy(task))).run(recover=True)
    assert result["manual_result"] == outcome and result["phase"] not in PENDING_PHASES
    assert updates == [result] and store.read("rental-history.json") == [result]


@pytest.mark.parametrize("outcome", ["completed", "released"])
def test_pending_buttons_need_only_one_confirmation_without_login_or_browser(tmp_path, monkeypatch, outcome):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.write("rental-task.json", pending())
    panel = RentalPanel(store)
    questions, requests = [], []
    def answer(*args):
        questions.append(args)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", answer)
    panel.requested.connect(lambda *args: requests.append(args))
    try:
        assert panel.complete_button.isEnabled() and panel.resolve_button.isEnabled()
        (panel.complete_button if outcome == "completed" else panel.resolve_button).click()
        assert len(questions) == 1 and not requests
        assert store.read("rental-task.json")["manual_result"] == outcome
        assert panel.start_button.isEnabled() and not panel.consent.isChecked()
        assert not panel.complete_button.isEnabled() and not panel.verify_button.isEnabled()
    finally:
        panel.close()
        app.processEvents()


def test_cancelled_confirmation_and_dialog_completion_race_do_not_change_result(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.write("rental-task.json", pending())
    panel = RentalPanel(store)
    try:
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
        panel.resolve_button.click()
        assert store.read("rental-task.json") == pending()
        def completed_while_dialog_open(*args):
            task = pending()
            task.update(phase="completed", verification="automatic", message="自动核验已完成")
            store.write("rental-task.json", task)
            return QMessageBox.StandardButton.Yes
        monkeypatch.setattr(QMessageBox, "question", completed_while_dialog_open)
        panel.complete_button.click()
        assert store.read("rental-task.json")["verification"] == "automatic"
        assert panel.status.text() == "自动核验已完成"
    finally:
        panel.close()
        app.processEvents()


@pytest.mark.parametrize("outcome", ["completed", "released"])
def test_active_worker_accepts_confirmation_and_closes_browser_without_new_order(tmp_path, monkeypatch, outcome):
    from autodl_fresh import worker as worker_module, rental_browser
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.write("auth.json", {})
    store.write("settings.json", {"auto_check_updates": False})
    store.write("rental-task.json", pending())
    entered, closed = threading.Event(), threading.Event()
    @contextmanager
    def session(*args, **kwargs):
        assert kwargs["close_browser_on_exit"] is True
        try:
            yield SimpleNamespace(), SimpleNamespace()
        finally:
            closed.set()
    class Adapter:
        def __init__(self, *args, **kwargs):
            pass
        def show_status(self, task):
            pass
        def verify(self, task):
            entered.set()
            self.confirmation.wait(5)
            self.confirmation.check(task)
            raise AssertionError("Confirmation should wake verification")
    monkeypatch.setattr(worker_module, "browser_session", session)
    monkeypatch.setattr(rental_browser, "CloneConsole", Adapter)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow(store)
    window.timer.stop()
    try:
        window._start("rental_verify")
        assert entered.wait(2)
        assert window.rental_panel.complete_button.isEnabled()
        assert window.rental_panel.resolve_button.isEnabled()
        (window.rental_panel.complete_button if outcome == "completed" else window.rental_panel.resolve_button).click()
        deadline = time.monotonic() + 3
        while window.worker and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert not window.worker and closed.is_set()
        result = store.read("rental-task.json")
        assert result["manual_result"] == outcome and result["phase"] not in PENDING_PHASES
        assert window.rental_panel.start_button.isEnabled()
        assert not window.rental_panel.consent.isChecked()
        assert len(store.read("rental-history.json")) == 1
    finally:
        if window.worker:
            window.worker.rental_confirmation.request(outcome, pending())
            window.worker.wait(2000)
            app.processEvents()
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()


def test_confirmation_at_worker_exit_is_applied_after_the_worker_stops(tmp_path):
    from autodl_fresh.worker import BrowserWorker
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    store.write("rental-task.json", pending())
    window = MainWindow(store)
    window.timer.stop()
    try:
        worker = BrowserWorker(window.settings, store, "rental_verify")
        window.worker = worker
        window.rental_panel.set_busy(True, "rental_verify")
        worker.rental_confirmation.request("released", pending())
        window._worker_finished()
        assert not window.worker
        assert store.read("rental-task.json")["manual_result"] == "released"
        assert window.rental_panel.start_button.isEnabled()
    finally:
        window._quitting = True
        window.tray.hide()
        window.close()
        app.processEvents()
