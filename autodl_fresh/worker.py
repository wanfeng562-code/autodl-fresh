from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import threading
import time

from PySide6.QtCore import QThread, Signal

from playwright.sync_api import Error as PlaywrightError

from .browser import Cancelled, InventoryUnavailable, SessionExpired, browser_session, save_auth
from .engine import RefreshEngine
from .native_browser import NativeBrowser
from .browser_ui import show_login_saved


class BrowserWorker(QThread):
    log = Signal(str)
    updated = Signal(object)
    completed = Signal(object)
    failed = Signal(str)
    auth_expired = Signal()
    login_ready = Signal()
    logged_in = Signal()
    rental_catalog = Signal(object)
    rental_updated = Signal(object)

    def __init__(self, settings, store, mode, parent=None):
        super().__init__(parent)
        if mode not in {"login", "preview", "refresh", "refresh_all", "resolve", "rental_probe", "rental", "rental_verify"}:
            raise ValueError("不支持的浏览器任务")
        self.settings = (replace(settings, browser_visibility="background", headless=True)
                         if mode in {"preview", "rental_probe"} else settings)
        self.store = store
        self.mode = mode
        self.cancel = threading.Event()
        self.save_login = threading.Event()
        self.rental_options = None

    def run(self):
        try:
            if self.mode == "login" and self.settings.browser_mode == "native":
                self._native_login()
                return
            with browser_session(self.settings, self.store, login=self.mode == "login",
                                 close_browser_on_exit=self.mode in {"rental", "rental_verify"}) as (console, context):
                console.log = self.log.emit
                for message in getattr(console, "browser_warnings", []):
                    self.log.emit(message)
                if self.mode.startswith("rental"):
                    from .rental_browser import CloneConsole
                    from .rental import RentalEngine
                    adapter = CloneConsole(console, auth_state=self.store.read("auth.json", {}))
                    adapter.cancel = self.cancel
                    if self.mode == "rental_probe":
                        from .quote_cache import QuoteCache
                        cache = QuoteCache(self.store)
                        def catalog_changed(data, *, complete=False):
                            data = cache.update(data, complete=complete)
                            self.rental_catalog.emit(deepcopy(data))
                        for attempt in range(3):
                            if self.cancel.is_set():
                                raise Cancelled("已停止读取 GPU 型号与参考报价")
                            try:
                                adapter.prepare(self.rental_options.source_uuid)
                                catalog_changed(adapter.catalog(include_prices=True, cancel=self.cancel,
                                                                changed=catalog_changed), complete=True)
                                break
                            except InventoryUnavailable:
                                if attempt == 2:
                                    raise
                                self.log.emit("本次读取暂未完成，正在后台重试…")
                                console.page.wait_for_timeout(1000)
                    else:
                        def rental_changed(task):
                            adapter.show_status(task)
                            self.rental_updated.emit(deepcopy(task))
                            if task.get("phase") == "accepted":
                                # Capture website state before replacing it with
                                # the local wait page; notify before this I/O.
                                try:
                                    save_auth(context, self.store)
                                except Exception:
                                    self.log.emit("新实例已创建；登录态未能另存，现有登录文件保留。")
                        result = RentalEngine(
                            adapter, self.store, self.rental_options, log=self.log.emit,
                            changed=rental_changed, cancel=self.cancel,
                        ).run(recover=self.mode == "rental_verify")
                        if result["phase"] in {"failed", "uncertain"}:
                            if "登录" in result["message"]:
                                self.auth_expired.emit()
                            self.failed.emit(result["message"])
                    if self.mode == "rental_probe" or (self.mode == "rental" and not result.get("target_uuid")):
                        save_auth(context, self.store)
                    return
                if self.mode == "login":
                    console.page.goto(self.settings.console_url, wait_until="domcontentloaded")
                    self.login_ready.emit()
                    self.log.emit("请在打开的浏览器中手动登录，然后回到本窗口点击“保存登录态”。")
                    while not self.cancel.is_set() and not console.page.is_closed():
                        console.page.wait_for_timeout(200)
                        if not self.save_login.is_set():
                            continue
                        self.save_login.clear()
                        try:
                            console.open()
                        except Exception:
                            self.log.emit("尚未检测到完整实例列表，请完成登录，并确认网页显示必要表格列后重试保存。")
                            continue
                        save_auth(context, self.store)
                        show_login_saved(context, console.page, self.settings.console_url)
                        self.logged_in.emit()
                        self.log.emit("AutoDL 登录态已保存。可以先进行只读检查，再启用每日计划。")
                        try:
                            console.page.wait_for_timeout(1500)
                        except PlaywrightError:
                            pass
                        return
                    self.log.emit("登录已结束，未修改保存的登录态。")
                    return
                if self.mode == "resolve":
                    pending = self.store.pending()
                    if not pending:
                        self.log.emit("没有需要解除的待核验记录。")
                        return
                    current = console.get(pending["uuid"])
                    if current.status != "已关机" or current.remaining_seconds is None:
                        raise RuntimeError("该实例尚未确认关机或释放倒计时不可读，无法解除暂停。请先在网页处理。")
                    resolutions = self.store.read("manual_resolutions.json", [])
                    from .models import now
                    self.store.write("manual_resolutions.json", [{**pending, "resolved_at": now().isoformat(), "release_text": current.release_text}, *resolutions][:100])
                    self.store.set_pending({})
                    self.log.emit("已按你的人工确认核对关机状态并解除暂停。本次未执行开关机，可重新检查并刷新。")
                    return
                engine = RefreshEngine(
                    console, self.store, self.settings, log=self.log.emit,
                    changed=lambda report: self.updated.emit(deepcopy(report)), cancel=self.cancel,
                )
                report = engine.run(preview=self.mode == "preview", refresh_all=self.mode == "refresh_all")
                if not report.errors and not report.cancelled:
                    save_auth(context, self.store)
                if any("登录" in error for error in report.errors):
                    self.auth_expired.emit()
                self.completed.emit(deepcopy(report))
        except Cancelled as exc:
            self.log.emit(str(exc))
            if self.mode == "rental_probe":
                self.rental_updated.emit({"phase": "cancelled", "message": "已停止读取 GPU 型号与参考报价"})
        except SessionExpired as exc:
            self.auth_expired.emit()
            self.failed.emit(str(exc))
        except Exception as exc:
            if isinstance(exc, PlaywrightError) and "has been closed" in str(exc):
                self.log.emit("浏览器窗口已关闭；已保存的登录态不会被删除。")
            else:
                self.failed.emit(str(exc))

    def _native_login(self):
        native = NativeBrowser(self.settings, self.store)
        if self.settings.browser_channel == "chromium":
            raise RuntimeError("普通窗口登录请先选择 Edge 或 Chrome；Chromium 可使用兼容模式。")
        native.open_login_window(cancel=self.cancel)
        self.login_ready.emit()
        self.log.emit("已打开 AutoDL 专用窗口：浏览器账号登录、同步和用户扩展已禁用。请手动登录 AutoDL 后点击“保存登录态”。")
        last_checked = 0
        while not self.cancel.wait(0.2):
            if time.monotonic() - last_checked > 1:
                last_checked = time.monotonic()
                if native.endpoint() is None:
                    self.log.emit("普通浏览器已关闭；已保存的登录信息仍会保留。")
                    return
            if not self.save_login.is_set():
                continue
            self.save_login.clear()
            try:
                with browser_session(self.settings, self.store, login=True, native=native) as (console, context):
                    console.open()
                    save_auth(context, self.store)
                    show_login_saved(context, console.page, self.settings.console_url)
                self.logged_in.emit()
                self.log.emit("AutoDL 登录已保存，可手动关闭专用窗口。应用仅复用 AutoDL 登录态，不同步浏览器账号和扩展。")
                return
            except SessionExpired:
                self.log.emit("尚未检测到登录后的实例列表，请先在普通浏览器中完成登录，再保存。")
            except Exception as exc:
                self.log.emit(f"本次未保存登录信息：{exc}。完成登录后可重试。")
        self.log.emit("已结束登录等待，普通浏览器窗口可继续手动使用。")
