"""One user-started GPU rental, with a durable journal before the paid click."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal, InvalidOperation
import threading
import time

from playwright.sync_api import Error as PlaywrightError

from .browser import ActionRejected, Cancelled, ConsoleError, InventoryUnavailable
from .models import now

PENDING_PHASES = {"submitted", "accepted", "uncertain"}
GPU_COUNTS = (1, 2, 3, 4, 5, 6, 7, 8, 10, 12)


def _task_identity(task):
    return task.get("started_at"), task.get("target_uuid"), (task.get("options") or {}).get("source_uuid")


def confirm_rental_result(store, outcome, *, expected):
    """Record a user's result without a browser or any further order."""
    if outcome not in {"completed", "released", "not_created"}:
        raise ValueError("不支持的人工确认结果")
    task = store.read("rental-task.json", {})
    if task.get("phase") not in PENDING_PHASES or _task_identity(task) != _task_identity(expected):
        return task
    if outcome in {"completed", "released"} and not task.get("target_uuid"):
        raise ValueError("没有已创建的新实例 ID，请先核对是否创建")
    if outcome == "not_created" and task.get("target_uuid"):
        raise ValueError("平台已返回新实例 ID，请确认克隆完成或实例已释放")
    messages = {"completed": "已由用户确认克隆完成，本次候租已结束",
                "released": "已由用户确认新实例已释放，本次候租已结束，可手动重新候租",
                "not_created": "已由用户确认本次未创建实例，可手动重新候租"}
    task.update(phase="completed" if outcome == "completed" else "resolved", verification="manual",
                manual_result=outcome, message=messages[outcome], updated_at=now().isoformat())
    store.write("rental-task.json", task)
    history = store.read("rental-history.json", [])
    store.write("rental-history.json", [task, *history][:100])
    return task


class ManualRentalConfirmation(Exception):
    def __init__(self, outcome):
        super().__init__(outcome)
        self.outcome = outcome


class RentalConfirmation:
    """UI requests; the worker alone finishes its active journal and browser."""

    def __init__(self):
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._request = None

    def request(self, outcome, task):
        if outcome not in {"completed", "released"} or not task.get("target_uuid"):
            raise ValueError("请先核对新实例结果")
        with self._lock:
            if self._request is None:
                self._request = (_task_identity(task), outcome)
                self._event.set()

    def check(self, task):
        outcome = self.result(task)
        if outcome:
            raise ManualRentalConfirmation(outcome)

    def result(self, task):
        with self._lock:
            if self._request and self._request[0] == _task_identity(task):
                return self._request[1]
        return ""

    def wait(self, seconds):
        self._event.wait(seconds)


class StockLost(ActionRejected):
    """A definite stock rejection, or a changed selection before submission."""

    def __init__(self, message, *, code=""):
        super().__init__(message)
        self.code = code


def money(value) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("费用必须是有限的非负数") from exc


@dataclass
class RentalOptions:
    source_uuid: str = ""
    gpu_names: list[str] = field(default_factory=list)
    gpu_count: int = 1
    interval_seconds: int = 1
    max_minutes: int = 120

    def validate(self):
        if not self.source_uuid or len(self.source_uuid) > 80:
            raise ValueError("请选择一个源实例")
        if (not self.gpu_names or len(self.gpu_names) > 50
                or any(not isinstance(s, str) or not s.strip() or len(s) > 100 for s in self.gpu_names)
                or len(set(self.gpu_names)) != len(self.gpu_names)):
            raise ValueError("请勾选可接受的 GPU 型号")
        if self.gpu_count not in GPU_COUNTS:
            raise ValueError("不支持的 GPU 卡数")
        if not 1 <= self.interval_seconds <= 3600 or not 1 <= self.max_minutes <= 1440:
            raise ValueError("刷新间隔需为 1–3600 秒，最长候租需为 1–1440 分钟")

    @classmethod
    def from_dict(cls, values):
        # Old journals can include the removed price limits. They still need
        # verification after upgrading, without reviving those restrictions.
        return cls(**{f.name: values[f.name] for f in fields(cls) if f.name in values})


@dataclass(frozen=True)
class GPUOffer:
    machine_id: str
    gpu_name: str
    idle: int
    hourly_price: str
    alias: str = ""


class RentalEngine:
    def __init__(self, adapter, store, options, *, log=None, changed=None, cancel=None,
                 clock=time.monotonic, wait=None, confirmation=None):
        self.adapter, self.store, self.options = adapter, store, options
        self.log = log or (lambda _: None)
        self.changed = changed or (lambda _: None)
        self.cancel = cancel or threading.Event()
        self.clock = clock
        self.wait = wait or self.cancel.wait
        self.confirmation = confirmation or RentalConfirmation()
        self.adapter.confirmation = self.confirmation
        self.task = {}

    def _error_detail(self, exc, *, pending):
        if isinstance(exc, PlaywrightError) and any(s in str(exc) for s in ("has been closed", "Target crashed")):
            state = self.adapter.browser_state() if hasattr(self.adapter, "browser_state") else {}
            self.task["browser_state"] = state
            diagnostic = ("检测到工作页崩溃。" if state.get("page_crashed") else
                          "检测到浏览器连接断开。" if state.get("browser_connected") is False else
                          "检测到工作标签已关闭，浏览器仍连接。" if state.get("page_closed") else "")
            return diagnostic + ("浏览器或工作页已关闭，或浏览器进程已退出。已保留创建记录，"
                                  "可在应用中手动确认克隆完成或实例已释放，也可继续自动核验。"
                    if pending else "浏览器或工作页已关闭，或浏览器进程已退出。本轮未提交创建，可以重新开始候租。")
        return str(exc)

    def _save(self, **values):
        self.task.update(values, updated_at=now().isoformat())
        self.store.write("rental-task.json", self.task)
        self.changed(dict(self.task))

    def _archive(self):
        history = self.store.read("rental-history.json", [])
        self.store.write("rental-history.json", [self.task, *history][:100])

    def _verify(self):
        try:
            self.confirmation.check(self.task)
            try:
                target = self.adapter.verify(self.task)
            except Exception:
                # A user may confirm while a network request or closed browser
                # is returning an error. Their result must not be overwritten.
                self.confirmation.check(self.task)
                raise
            self.confirmation.check(self.task)
        except ManualRentalConfirmation as exc:
            self.task = confirm_rental_result(self.store, exc.outcome, expected=self.task)
            self.changed(dict(self.task))
            self.log(self.task["message"])
            return dict(self.task)
        self._save(phase="completed", target_uuid=target.uuid,
                   verification="automatic",
                   message=f"克隆完成：{target.uuid}，系统盘克隆及数据盘拷贝已完成。")
        self._archive()
        self.log(self.task["message"] + " 本次候租已结束，请使用后自行关机。")
        return dict(self.task)

    def run(self, *, recover=False):
        previous = self.store.read("rental-task.json", {})
        if recover:
            if previous.get("phase") not in PENDING_PHASES or not previous.get("target_uuid"):
                raise ConsoleError("没有可自动核验的新实例 ID；请先在网页核对创建结果。")
            self.task = previous
            self.options = RentalOptions.from_dict(previous["options"])
            try:
                return self._verify()
            except Exception as exc:
                detail = self._error_detail(exc, pending=True)
                self._save(message=f"自动核验已结束：{detail}")
                raise ConsoleError(detail) from exc
        if previous.get("phase") in PENDING_PHASES:
            raise ConsoleError("上次创建结果尚未确认，请先核验；暂停新的租用以免重复下单。")
        if self.store.pending():
            raise ConsoleError("有保活任务尚未完成关机核验，请先处理再候租。")
        self.options.validate()
        self.task = {"phase": "monitoring", "started_at": now().isoformat(),
                     "options": asdict(self.options), "attempts": 0, "stock_rejections": 0, "target_uuid": ""}
        self._save(message="正在准备克隆页面")
        deadline = self.clock() + self.options.max_minutes * 60
        try:
            while True:
                if self.cancel.is_set():
                    raise Cancelled("候租已停止，未提交创建")
                if self.clock() >= deadline:
                    self._save(phase="expired", message="已到最长候租时间，未创建实例")
                    self._archive()
                    return dict(self.task)
                try:
                    source = self.adapter.prepare(self.options.source_uuid)
                    break
                except InventoryUnavailable as exc:
                    self._save(message=f"候租页面暂未就绪：{exc}；将自动重试（可停止）")
                    self.wait(min(max(1, self.options.interval_seconds), max(0, deadline - self.clock())))
            self._save(source_region=source.region.split("/")[0].strip())
            consecutive_failures = 0
            while self.clock() < deadline:
                if self.cancel.is_set():
                    raise Cancelled("候租已停止，未提交创建")
                try:
                    offers = self.adapter.offers(self.options, self.cancel)
                    consecutive_failures = 0
                except InventoryUnavailable as exc:
                    consecutive_failures += 1
                    delay = max(self.options.interval_seconds, min(30, 2 ** min(consecutive_failures - 1, 5)))
                    self._save(message=f"库存读取暂未成功：{exc}；{delay} 秒后自动重试（可停止）")
                    self.wait(min(delay, max(0, deadline - self.clock())))
                    continue
                rank = {name: index for index, name in enumerate(self.options.gpu_names)}
                candidates = sorted((o for o in offers if o.gpu_name in rank
                                     and o.idle >= self.options.gpu_count),
                                    key=lambda o: (rank[o.gpu_name], money(o.hourly_price), o.machine_id))
                self._save(attempts=self.task["attempts"] + 1,
                           message=f"第 {self.task['attempts'] + 1} 次检查：{len(candidates)} 台符合型号及卡数")
                stock_lost = False
                for offer in candidates:
                    if self.cancel.is_set():
                        raise Cancelled("候租已停止，未提交创建")
                    if self.clock() >= deadline:
                        break
                    try:
                        quote = self.adapter.select(offer, self.options)
                    except InventoryUnavailable as exc:
                        self._save(message=f"选机页面暂未就绪：{exc}；将重新检查库存")
                        break
                    if quote is None:
                        continue
                    self._save(offer=asdict(offer), quote=quote)
                    if self.cancel.is_set():
                        raise Cancelled("候租已停止，未提交创建")
                    def submitted():
                        if self.cancel.is_set() or self.clock() >= deadline:
                            raise Cancelled("候租已停止，未提交创建")
                        self._save(phase="submitted", message="正在提交创建并开机，请勿重复下单")
                    try:
                        target_uuid = self.adapter.create(offer, self.options, submitted)
                    except StockLost as exc:
                        # The platform explicitly rejected this attempt. Drop
                        # the old snapshot and obtain fresh stock on the SAME
                        # clone page before any subsequent paid click.
                        stock_lost = True
                        self._save(phase="monitoring", target_uuid="", offer={}, quote={},
                                   stock_rejections=self.task["stock_rejections"] + 1,
                                   last_rejection_code=exc.code, last_rejected_machine=offer.machine_id,
                                   message=f"{exc}；{self.options.interval_seconds} 秒后继续候租")
                        self.log(self.task["message"])
                        break
                    self._save(phase="accepted", target_uuid=target_uuid,
                               message=f"克隆实例已创建：{target_uuid}，正在等待克隆完成")
                    # Once a request was accepted, stopping does not abandon its
                    # verification, and must never turn off a GPU rented for use.
                    return self._verify()
                if not stock_lost:
                    self._save(message=f"暂无可租机器，{self.options.interval_seconds} 秒后重试")
                self.wait(min(self.options.interval_seconds, max(0, deadline - self.clock())))
            self._save(phase="expired", message="已到最长候租时间，未创建实例")
        except Cancelled as exc:
            self._save(phase="cancelled", message=str(exc))
        except ActionRejected as exc:
            self._save(phase="failed", message=f"创建未被接受：{exc}，已停止候租")
        except Exception as exc:
            phase = "uncertain" if self.task.get("phase") in PENDING_PHASES else "failed"
            detail = self._error_detail(exc, pending=phase == "uncertain")
            self._save(phase=phase, message=f"候租暂停：{detail}")
        self.log(self.task["message"])
        self._archive()
        return dict(self.task)
