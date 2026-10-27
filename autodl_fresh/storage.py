from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit
from .locations import configured_directory

CONSOLE_URL = "https://www.autodl.com/console/instance/list"


def data_directory() -> Path:
    return configured_directory()


@dataclass
class Settings:
    threshold_days: int = 3
    schedule_enabled: bool = False
    daily_time: str = "09:00"
    browser_channel: str = "msedge"
    browser_mode: str = "native"
    headless: bool = True
    browser_visibility: str = "background"
    page_size: int = 10
    auto_check_updates: bool = True
    timeout_seconds: int = 240
    poll_seconds: int = 5
    minimize_to_tray: bool = True
    start_with_windows: bool = False
    console_url: str = CONSOLE_URL

    def validate(self) -> None:
        if not 1 <= self.threshold_days <= 7:
            raise ValueError("刷新阈值需在 1–7 天之间")
        import re
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.daily_time):
            raise ValueError("每日时间格式应为 HH:mm")
        if self.browser_channel not in {"msedge", "chrome", "chromium"}:
            raise ValueError("不支持的浏览器")
        if self.browser_mode not in {"native", "playwright"}:
            raise ValueError("不支持的浏览器连接方式")
        if self.browser_visibility not in {"background", "foreground"}:
            raise ValueError("不支持的浏览器显示方式")
        if self.page_size not in {10, 20, 50, 100}:
            raise ValueError("每页条数需为 10、20、50 或 100")
        if not isinstance(self.auto_check_updates, bool):
            raise ValueError("自动检查更新选项必须为开启或关闭")
        if not 30 <= self.timeout_seconds <= 900 or not 1 <= self.poll_seconds <= 30:
            raise ValueError("状态等待时间需为 30–900 秒，轮询间隔需为 1–30 秒")
        parsed = urlsplit(self.console_url)
        if (parsed.scheme, parsed.netloc, parsed.path) != (
            "https", "www.autodl.com", "/console/instance/list"
        ) or parsed.query or parsed.fragment:
            raise ValueError("仅支持 AutoDL 官方普通容器实例列表地址")


class Store:
    """Atomic local persistence. Runtime data and credentials never belong in the repository."""

    def __init__(self, root: Path | None = None):
        self.root = root or data_directory()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.auth_path = self.root / "auth.json"

    def read(self, name: str, default=None):
        with self._lock:
            path = self.root / name
            if not path.exists():
                return default
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"本地文件 {name} 无法读取，请在数据目录检查并恢复备份") from exc

    def write(self, name: str, value) -> None:
        with self._lock:
            path = self.root / name
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass

    def settings(self) -> Settings:
        values = self.read("settings.json", {})
        if "browser_visibility" not in values:
            values["browser_visibility"] = "background" if values.get("headless", True) else "foreground"
        result = Settings(**{f.name: values[f.name] for f in fields(Settings) if f.name in values})
        result.validate()
        return result

    def save_settings(self, settings: Settings):
        settings.validate()
        self.write("settings.json", asdict(settings))

    def pending(self) -> dict:
        return self.read("pending.json", {})

    def set_pending(self, value: dict):
        self.write("pending.json", value)

    def clear_legacy_preflight(self) -> bool:
        """Migrate only v0.1 records proven to fail at _row BEFORE any power request.

        Unknown legacy `intent` records remain blocked. A matching error must be
        from the run that created the record, not a later recovery attempt.
        """
        from .models import now
        pending = self.pending()
        if pending.get("phase") != "intent":
            return False
        created = pending.get("created_at", "")
        error = f"无法唯一定位实例 {pending.get('uuid')}，已停止。"
        for report in self.read("history.json", []):
            if (report.get("started_at", "") <= created <= report.get("finished_at", "")
                    and error in report.get("errors", [])):
                events = self.read("recovery_events.json", [])
                self.write("recovery_events.json", [{**pending, "resolved_at": now().isoformat(),
                           "reason": "v0.1 定位失败发生在提交开机请求之前"}, *events][:100])
                self.set_pending({})
                return True
        return False

    def append_report(self, report):
        with self._lock:
            history = self.read("history.json", [])
            self.write("history.json", [report.to_dict(), *history][:100])

    def log(self, text: str):
        with self._lock:
            path = self.root / "activity.log"
            if path.exists() and path.stat().st_size > 2_000_000:
                os.replace(path, self.root / "activity.previous.log")
            with path.open("a", encoding="utf-8") as file:
                file.write(text + "\n")
