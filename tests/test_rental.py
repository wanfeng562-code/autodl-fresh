from dataclasses import replace
import threading

import pytest

from autodl_fresh.browser import ActionRejected, ConsoleError, InventoryUnavailable
from autodl_fresh.models import Instance
from autodl_fresh.rental import GPUOffer, PENDING_PHASES, RentalEngine, RentalOptions
from autodl_fresh.storage import Store


def options(**changes):
    return replace(RentalOptions(source_uuid="sourceaa-12345678", gpu_names=["RTX 4090", "RTX 3090"]), **changes)


class FakeClone:
    def __init__(self, store, *, cancel=None):
        self.store, self.cancel = store, cancel
        self.events = []
        self.offered = [GPUOffer("m1", "RTX 4090", 1, "2.00"), GPUOffer("m2", "RTX 3090", 2, "1.20")]
        self.error = None
        self.verify_error = False
        self.unsafe = set()
        self.clock = 0

    def prepare(self, uuid):
        self.events.append("prepare")
        return Instance(uuid, region="西北B区 / A01机")

    def offers(self, options, cancel):
        self.events.append("offers")
        return self.offered

    def select(self, offer, options):
        self.events.append(("select", offer.machine_id))
        if offer.machine_id in self.unsafe:
            return None
        return {"hourly": offer.hourly_price, "disk_daily": "0", "expand_gb": "0"}

    def create(self, offer, options, submitted):
        submitted()
        assert self.store.read("rental-task.json")["phase"] == "submitted"
        self.events.append(("create", offer.machine_id))
        if self.error:
            raise self.error
        if self.cancel:
            self.cancel.set()
        return "targetaa-87654321"

    def verify(self, task):
        assert task["target_uuid"] == "targetaa-87654321"
        self.events.append("verify")
        if self.verify_error:
            raise ConsoleError("拷贝仍未完成")
        return Instance(task["target_uuid"], status="运行中")

    def wait(self, seconds):
        self.clock += seconds


def engine(store, adapter, config=None, **kwargs):
    return RentalEngine(adapter, store, config or options(), clock=lambda: adapter.clock,
                        wait=adapter.wait, **kwargs)


@pytest.mark.parametrize("changes", [
    {"gpu_count": 9}, {"gpu_names": []},
    {"gpu_names": ["RTX 4090", "RTX 4090"]}, {"interval_seconds": 0}, {"max_minutes": 0},
])
def test_reject_invalid_or_unbounded_conditions(changes):
    with pytest.raises(ValueError):
        options(**changes).validate()


def test_priority_beats_price_and_exactly_one_creation(tmp_path):
    store = Store(tmp_path)
    adapter = FakeClone(store)
    task = engine(store, adapter).run()
    assert task["phase"] == "completed" and task["source_region"] == "西北B区"
    assert ("create", "m1") in adapter.events and ("create", "m2") not in adapter.events
    assert adapter.events[-1] == "verify"
    assert store.read("rental-history.json")[0]["target_uuid"] == "targetaa-87654321"


def test_filters_exact_model_and_count_without_price_cap(tmp_path):
    store = Store(tmp_path)
    adapter = FakeClone(store)
    adapter.offered += [GPUOffer("m3", "RTX 4090 D", 8, "0.10"), GPUOffer("m4", "RTX 4090", 2, "2")]
    task = engine(store, adapter, options(gpu_count=2)).run()
    assert task["phase"] == "completed"
    assert ("create", "m4") in adapter.events  # Preferred model wins, regardless of a former cap.


def test_candidate_quote_rejected_tries_next_without_submitting(tmp_path):
    store = Store(tmp_path)
    adapter = FakeClone(store)
    adapter.unsafe.add("m1")
    assert engine(store, adapter).run()["phase"] == "completed"
    assert ("create", "m1") not in adapter.events
    assert ("create", "m2") in adapter.events


def test_empty_stock_polls_to_deadline_and_stops_without_order(tmp_path):
    store = Store(tmp_path)
    adapter = FakeClone(store)
    adapter.offered = []
    task = engine(store, adapter, options(max_minutes=1, interval_seconds=30)).run()
    assert task["phase"] == "expired" and task["attempts"] == 2
    assert not any(isinstance(e, tuple) and e[0] == "create" for e in adapter.events)


def test_default_one_second_polling_and_cancellation_between_checks(tmp_path):
    store = Store(tmp_path)
    adapter = FakeClone(store)
    adapter.offered = []
    config = options(max_minutes=1)
    assert config.interval_seconds == 1
    config.validate()
    task = engine(store, adapter, config).run()
    assert task["phase"] == "expired" and task["attempts"] == 60
    assert adapter.clock == 60


@pytest.mark.parametrize("interval", [1, 60])
def test_temporary_failures_back_off_then_recover_without_reopening_or_duplicate_order(tmp_path, interval):
    store = Store(tmp_path)
    adapter = FakeClone(store)
    replies = [InventoryUnavailable("网络暂时不可用")] * 8 + [[], InventoryUnavailable("再次断网"), adapter.offered]
    delays = []

    def offers(config, cancel):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def wait(seconds):
        delays.append(seconds)
        adapter.wait(seconds)

    adapter.offers = offers
    task = RentalEngine(adapter, store, options(interval_seconds=interval),
                        clock=lambda: adapter.clock, wait=wait).run()
    assert task["phase"] == "completed"
    assert delays == [max(interval, d) for d in [1, 2, 4, 8, 16, 30, 30, 30, interval, 1]]
    assert adapter.events.count("prepare") == 1
    assert len([e for e in adapter.events if isinstance(e, tuple) and e[0] == "create"]) == 1


def test_cancel_before_creation_and_after_acceptance(tmp_path):
    store = Store(tmp_path)
    cancel = threading.Event()
    cancel.set()
    adapter = FakeClone(store)
    assert engine(store, adapter, cancel=cancel).run()["phase"] == "cancelled"
    assert "offers" not in adapter.events
    cancel.clear()
    adapter = FakeClone(store, cancel=cancel)
    assert engine(store, adapter, cancel=cancel).run()["phase"] == "completed"
    assert cancel.is_set() and adapter.events[-1] == "verify"


def test_ambiguous_submission_survives_restart_and_blocks_duplicate(tmp_path):
    store = Store(tmp_path)
    adapter = FakeClone(store)
    adapter.error = ConsoleError("响应丢失")
    task = engine(store, adapter).run()
    assert task["phase"] == "uncertain"
    with pytest.raises(ConsoleError, match="尚未确认"):
        engine(Store(tmp_path), FakeClone(store)).run()
    with pytest.raises(ConsoleError, match="没有可自动核验"):
        engine(Store(tmp_path), FakeClone(store)).run(recover=True)


def test_explicit_rejection_stops_without_pending_order(tmp_path):
    store = Store(tmp_path)
    adapter = FakeClone(store)
    adapter.error = ActionRejected("库存不足")
    assert engine(store, adapter).run()["phase"] == "failed"
    assert store.read("rental-task.json")["phase"] not in PENDING_PHASES
    assert ("create", "m2") not in adapter.events


def test_copy_failure_keeps_target_and_recovery_never_creates_again(tmp_path):
    store = Store(tmp_path)
    adapter = FakeClone(store)
    adapter.verify_error = True
    task = engine(store, adapter).run()
    assert task["phase"] == "uncertain" and task["target_uuid"] == "targetaa-87654321"
    adapter2 = FakeClone(store)
    result = engine(Store(tmp_path), adapter2).run(recover=True)
    assert result["phase"] == "completed" and adapter2.events == ["verify"]


def test_keepalive_recovery_blocks_new_rental(tmp_path):
    store = Store(tmp_path)
    store.set_pending({"uuid": "pendingaa-12345678", "phase": "accepted"})
    adapter = FakeClone(store)
    with pytest.raises(ConsoleError, match="保活任务"):
        engine(store, adapter).run()
    assert not adapter.events


@pytest.mark.parametrize('after_submission', [False, True])
def test_browser_closed_is_explained_and_accepted_orders_never_repeat(tmp_path, after_submission):
    from playwright.sync_api import Error as PlaywrightError
    store = Store(tmp_path)
    adapter = FakeClone(store)
    def closed(*args):
        raise PlaywrightError('Target page, context or browser has been closed\nCall log: waiting for nth(95)')
    if after_submission:
        adapter.verify = closed
    else:
        adapter.offers = closed
    task = engine(store, adapter).run()
    assert '浏览器或工作页已关闭' in task['message'] and 'Call log' not in task['message']
    if after_submission:
        assert task['phase'] == 'uncertain' and task['target_uuid']
        with pytest.raises(ConsoleError, match='尚未确认'):
            engine(store, FakeClone(store)).run()
    else:
        assert task['phase'] == 'failed' and not task['target_uuid']
        assert not any(isinstance(e, tuple) and e[0] == 'create' for e in adapter.events)


def test_old_price_limits_are_ignored_and_known_target_recovery_still_works(tmp_path):
    config = RentalOptions.from_dict({"source_uuid": "sourceaa-12345678", "gpu_names": ["RTX 4090"],
                                      "max_hourly_price": "0.01", "max_disk_daily_price": "0"})
    config.validate()
    assert not hasattr(config, "max_hourly_price")
    store = Store(tmp_path)
    adapter = FakeClone(store)
    assert engine(store, adapter, config).run()["phase"] == "completed"
    task = store.read("rental-task.json")
    task["phase"] = "accepted"
    task["options"].update(max_hourly_price="0.01", max_disk_daily_price="0")
    store.write("rental-task.json", task)
    recovery = FakeClone(store)
    assert engine(store, recovery).run(recover=True)["phase"] == "completed"
    assert recovery.events == ["verify"]
