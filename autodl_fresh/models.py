from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import re

SHANGHAI = timezone(timedelta(hours=8))
DAY = 86400


def now() -> datetime:
    return datetime.now(SHANGHAI)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def parse_countdown(text: str) -> int | None:
    """Only accept a release countdown, never a running-instance retention label/date."""
    text = compact(text)
    match = re.search(r"((?:\d+(?:天|小时|分钟|分|秒))+)(?:后释放)", text)
    if not match or (match.start() > 0 and text[match.start() - 2:match.start()] in {"关机", "到期"}):
        return None
    units = {"天": DAY, "小时": 3600, "分钟": 60, "分": 60, "秒": 1}
    return sum(int(n) * units[u] for n, u in re.findall(r"(\d+)(天|小时|分钟|分|秒)", match[1]))


@dataclass
class Instance:
    uuid: str
    name: str = ""
    region: str = ""
    status: str = "未知"
    release_text: str = ""
    remaining_seconds: int | None = None
    no_gpu: bool = False
    charge: str = ""
    page: int = 1
    blocked_reason: str = ""
    retention_days: int = 15
    gpu_name: str = ""
    gpu_count: int = 0

    def reason(self, threshold_days: int, *, refresh_all: bool = False) -> str:
        if self.blocked_reason:
            return self.blocked_reason
        if self.status != "已关机":
            return "运行中，跳过" if self.status == "运行中" else f"{self.status}，跳过"
        if self.charge != "按量计费":
            return "非按量计费，跳过"
        if self.remaining_seconds is None:
            return "无法识别释放倒计时，需检查"
        if self.remaining_seconds <= 0:
            return "已到释放时间，需人工检查"
        if not refresh_all and self.remaining_seconds >= threshold_days * DAY:
            return "距离释放充足"
        return "待刷新"

    def eligible(self, threshold_days: int, *, refresh_all: bool = False) -> bool:
        return self.reason(threshold_days, refresh_all=refresh_all) == "待刷新"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunReport:
    started_at: str = field(default_factory=lambda: now().isoformat())
    finished_at: str = ""
    preview: bool = False
    refresh_all: bool = False
    instances: list[Instance] = field(default_factory=list)
    outcomes: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    scan_complete: bool = False
    snapshot_at: str = ""

    @property
    def successes(self) -> int:
        return sum(value == "刷新成功" for value in self.outcomes.values())

    def to_dict(self) -> dict:
        return asdict(self)


def retain_snapshot(report: RunReport, previous: RunReport | None) -> RunReport:
    """A failed/cancelled scan must not make previously known instances disappear."""
    if report.scan_complete or report.instances or previous is None or not previous.instances:
        return report
    from copy import deepcopy
    from dataclasses import replace
    return replace(report, instances=deepcopy(previous.instances),
                   snapshot_at=previous.snapshot_at or previous.started_at)


def schedule_due(settings, last_attempt: str, current: datetime) -> bool:
    if not settings.schedule_enabled:
        return False
    hour, minute = map(int, settings.daily_time.split(":"))
    if (current.hour, current.minute) < (hour, minute):
        return False
    return not last_attempt or datetime.fromisoformat(last_attempt).astimezone(SHANGHAI).date() < current.date()
