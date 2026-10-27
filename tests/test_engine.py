from dataclasses import replace
import threading

from autodl_fresh.browser import ActionRejected, ConsoleError
from autodl_fresh.engine import RefreshEngine
from autodl_fresh.models import DAY, Instance
from autodl_fresh.storage import Settings, Store


class FakeConsole:
    def __init__(self, *, fail_stop=False, fail_reset=False, reject=False, ambiguous=False, cancel=None):
        self.items = [Instance(uuid=f"abcdef{i:02d}-12345678", status="已关机", charge="按量计费", remaining_seconds=2 * DAY) for i in range(2)]
        self.events = []
        self.fail_stop = fail_stop
        self.fail_reset = fail_reset
        self.reject = reject
        self.ambiguous = ambiguous
        self.cancel = cancel

    def scan(self, cancel=None):
        return [replace(item) for item in self.items]

    def get(self, uuid):
        return replace(next(item for item in self.items if item.uuid == uuid))

    def set(self, uuid, **kwargs):
        self.items = [replace(item, **kwargs) if item.uuid == uuid else item for item in self.items]

    def start_no_gpu(self, uuid, accepted, submitted=None, *, refresh_all=False):
        self.events.append(("start", uuid))
        if self.reject:
            raise ActionRejected("无卡实例已占用")
        if submitted:
            submitted()
        if self.ambiguous:
            raise ConsoleError("无法确认响应")
        assert not any(item.status == "运行中" and item.no_gpu for item in self.items)
        self.set(uuid, status="运行中", no_gpu=True)
        accepted()
        if self.cancel:
            self.cancel.set()

    def stop(self, uuid):
        self.events.append(("stop", uuid))
        if self.fail_stop:
            raise ConsoleError("关机失败")
        self.set(uuid, status="已关机", no_gpu=False,
                 remaining_seconds=2 * DAY if self.fail_reset else 15 * DAY - 60,
                 release_text="14天23小时59分后释放")

    def wait(self, uuid, predicate, label):
        current = self.get(uuid)
        if not predicate(current):
            raise ConsoleError(f"等待{label}超时")
        return current


def test_serial_refresh_and_readback(tmp_path):
    console = FakeConsole()
    store = Store(tmp_path)
    report = RefreshEngine(console, store, Settings()).run()
    assert not report.errors
    assert report.successes == 2
    assert [action for action, _ in console.events] == ["start", "stop", "start", "stop"]
    assert not store.pending()
    assert all(item.remaining_seconds > 14 * DAY for item in report.instances)


def test_preview_never_starts_or_cleans_up_pending(tmp_path):
    console = FakeConsole()
    store = Store(tmp_path)
    store.set_pending({"uuid": console.items[0].uuid, "phase": "accepted", "before_seconds": 2 * DAY})
    RefreshEngine(console, store, Settings()).run(preview=True)
    assert console.events == []
    assert store.pending()


def test_cancellation_still_stops_current_instance(tmp_path):
    cancel = threading.Event()
    console = FakeConsole(cancel=cancel)
    report = RefreshEngine(console, Store(tmp_path), Settings(), cancel=cancel).run()
    assert report.cancelled
    assert report.successes == 1
    assert [action for action, _ in console.events] == ["start", "stop"]


def test_failed_stop_halts_queue_and_preserves_recovery(tmp_path):
    console = FakeConsole(fail_stop=True)
    store = Store(tmp_path)
    report = RefreshEngine(console, store, Settings()).run()
    assert report.errors
    assert sum(action == "start" for action, _ in console.events) == 1
    assert store.pending()["phase"] == "accepted"
    console.fail_stop = False
    recovered = RefreshEngine(console, store, Settings()).run()
    assert not recovered.errors
    assert recovered.successes == 2
    assert not store.pending()


def test_unchanged_countdown_is_not_success_and_halts_queue(tmp_path):
    console = FakeConsole(fail_reset=True)
    store = Store(tmp_path)
    report = RefreshEngine(console, store, Settings()).run()
    assert report.successes == 0
    assert report.errors
    assert store.pending()
    assert sum(action == "start" for action, _ in console.events) == 1


def test_ambiguous_start_cannot_be_automatically_repeated(tmp_path):
    console = FakeConsole(ambiguous=True)
    store = Store(tmp_path)
    report = RefreshEngine(console, store, Settings()).run()
    assert report.errors
    assert store.pending()["phase"] == "submitted"
    again = RefreshEngine(console, store, Settings()).run()
    assert again.errors
    assert len(console.events) == 1


def test_explicit_rejection_does_not_leave_an_owned_instance(tmp_path):
    console = FakeConsole(reject=True)
    store = Store(tmp_path)
    report = RefreshEngine(console, store, Settings()).run()
    assert report.errors
    assert not store.pending()
    assert len(console.events) == 1


def test_existing_no_gpu_instance_is_never_stopped(tmp_path):
    console = FakeConsole()
    console.items.append(Instance(uuid="existing-12345678", status="运行中", no_gpu=True))
    report = RefreshEngine(console, Store(tmp_path), Settings()).run()
    assert report.errors
    assert console.events == []


def test_preflight_failure_does_not_poison_future_runs(tmp_path):
    from autodl_fresh.browser import LayoutChanged

    class PreflightFailure(FakeConsole):
        def start_no_gpu(self, uuid, accepted, submitted=None):
            raise LayoutChanged(f"无法唯一定位实例 {uuid}，已停止。")

    console = PreflightFailure()
    store = Store(tmp_path)
    report = RefreshEngine(console, store, Settings()).run()
    assert report.errors
    assert len(report.instances) == 2
    assert report.scan_complete
    assert not store.pending()
    recovered = RefreshEngine(FakeConsole(), store, Settings()).run()
    assert recovered.successes == 2


def test_unknown_recovery_still_keeps_the_entire_list(tmp_path):
    store = Store(tmp_path)
    console = FakeConsole()
    store.set_pending({"uuid": console.items[0].uuid, "phase": "submitted", "before_seconds": 2*DAY})
    report = RefreshEngine(console, store, Settings()).run()
    assert report.errors
    assert report.scan_complete
    assert len(report.instances) == 2
    assert console.events == []


def test_legacy_preflight_journal_is_migrated_using_original_run_evidence(tmp_path):
    from autodl_fresh.models import RunReport
    store = Store(tmp_path)
    console = FakeConsole()
    uuid = console.items[0].uuid
    store.set_pending({"uuid": uuid, "phase": "intent", "before_seconds": 2*DAY,
                       "created_at": "2026-10-27T15:26:09+08:00"})
    store.append_report(RunReport(started_at="2026-10-27T15:26:03+08:00",
                        finished_at="2026-10-27T15:26:11+08:00",
                        errors=[f"无法唯一定位实例 {uuid}，已停止。"], instances=console.items))
    report = RefreshEngine(console, store, Settings()).run()
    assert not report.errors
    assert report.successes == 2
    assert store.read("recovery_events.json")[0]["uuid"] == uuid


def test_legacy_intent_without_evidence_is_not_cleared(tmp_path):
    store = Store(tmp_path)
    console = FakeConsole()
    store.set_pending({"uuid": console.items[0].uuid, "phase": "intent", "before_seconds": 2*DAY,
                       "created_at": "2026-10-27T15:26:09+08:00"})
    report = RefreshEngine(console, store, Settings()).run()
    assert report.errors
    assert store.pending()
    assert not console.events


def test_manual_completion_of_submitted_unknown_request_is_recognised(tmp_path):
    store = Store(tmp_path)
    console = FakeConsole()
    uuid = console.items[0].uuid
    console.set(uuid, remaining_seconds=15*DAY - 60)
    store.set_pending({"uuid": uuid, "phase": "submitted", "before_seconds": 2*DAY})
    report = RefreshEngine(console, store, Settings()).run()
    assert not report.errors
    assert report.successes == 2
    assert sum(action == "start" for action, _ in console.events) == 1


def test_full_refresh_includes_four_day_and_already_fresh_instances(tmp_path):
    console = FakeConsole()
    console.items[0].remaining_seconds = 4 * DAY
    console.items[1].remaining_seconds = 15 * DAY - 60
    report = RefreshEngine(console, Store(tmp_path), Settings()).run(refresh_all=True)
    assert not report.errors, report.errors
    assert report.refresh_all
    assert report.successes == 2
    assert [action for action, _ in console.events] == ["start", "stop", "start", "stop"]


def test_full_refresh_still_skips_unsafe_and_running_instances(tmp_path):
    console = FakeConsole()
    template = console.items[0]
    console.items.extend([
        replace(template, uuid="running", status="运行中"),
        replace(template, uuid="prepaid", charge="包年包月"),
        replace(template, uuid="unreadable", remaining_seconds=None),
        replace(template, uuid="expired", remaining_seconds=0),
        replace(template, uuid="blocked", blocked_reason="主机不可用"),
    ])
    report = RefreshEngine(console, Store(tmp_path), Settings()).run(refresh_all=True)
    assert not report.errors
    assert report.successes == 2
    assert {uuid for _, uuid in console.events} == {item.uuid for item in console.items[:2]}


def test_full_refresh_recovery_does_not_refresh_the_recovered_instance_twice(tmp_path):
    console = FakeConsole()
    store = Store(tmp_path)
    uuid = console.items[0].uuid
    console.set(uuid, status="运行中", no_gpu=True)
    store.set_pending({"uuid": uuid, "phase": "accepted", "before_seconds": 15 * DAY - 60, "refresh_all": True})
    report = RefreshEngine(console, store, Settings()).run(refresh_all=True)
    assert not report.errors, report.errors
    assert report.successes == 2
    assert console.events == [("stop", uuid), ("start", console.items[1].uuid), ("stop", console.items[1].uuid)]
    assert not store.pending()


def test_full_refresh_cannot_treat_an_ambiguous_fresh_countdown_as_proof(tmp_path):
    console = FakeConsole(ambiguous=True)
    console.items[0].remaining_seconds = 15 * DAY - 60
    store = Store(tmp_path)
    first = RefreshEngine(console, store, Settings()).run(refresh_all=True)
    second = RefreshEngine(console, store, Settings()).run(refresh_all=True)
    assert first.errors and second.errors
    assert first.successes == second.successes == 0
    assert store.pending()["phase"] == "submitted"
    assert len(console.events) == 1


def test_full_refresh_rejects_an_unchanged_four_day_countdown(tmp_path):
    console = FakeConsole(fail_reset=True)
    console.items[0].remaining_seconds = 4 * DAY
    report = RefreshEngine(console, Store(tmp_path), Settings()).run(refresh_all=True)
    assert report.errors and not report.successes
    assert [action for action, _ in console.events].count("start") == 1


def test_full_refresh_cancellation_still_cleans_up_the_current_instance(tmp_path):
    cancel = threading.Event()
    console = FakeConsole(cancel=cancel)
    console.items[0].remaining_seconds = 4 * DAY
    report = RefreshEngine(console, Store(tmp_path), Settings(), cancel=cancel).run(refresh_all=True)
    assert report.cancelled and report.successes == 1
    assert [action for action, _ in console.events] == ["start", "stop"]
