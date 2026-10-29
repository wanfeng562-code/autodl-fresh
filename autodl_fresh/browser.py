from __future__ import annotations

from contextlib import contextmanager
import re
import time
from typing import Callable
from urllib.parse import urlsplit

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from .models import DAY, Instance, compact, parse_countdown
from .storage import Settings, Store
from .native_browser import NativeBrowser, import_saved_state
from .browser_ui import background_work_window, close_empty_pages, create_work_page, is_empty_page


class ConsoleError(RuntimeError):
    pass


class SessionExpired(ConsoleError):
    pass


class LayoutChanged(ConsoleError):
    pass


class ActionRejected(ConsoleError):
    """Server explicitly rejected an action; no power-on was accepted."""


class Cancelled(ConsoleError):
    pass


class InventoryUnavailable(ConsoleError):
    """A temporary table read failed before any order was submitted."""


@contextmanager
def browser_session(settings: Settings, store: Store, *, login: bool = False, native=None, window_mode=None,
                    close_browser_on_exit=False):
    settings.validate()
    window_mode = "foreground" if login else window_mode or (
        "foreground" if settings.browser_visibility == "foreground" else "minimized")
    with sync_playwright() as playwright:
        if settings.browser_mode == "native":
            native = native or NativeBrowser(settings, store, window_mode=window_mode)
            newly_started = native.endpoint() is None
            endpoint = native.ensure_open(login=login, chromium_path=playwright.chromium.executable_path)
            browser = playwright.chromium.connect_over_cdp(endpoint, no_defaults=True)
            context = browser.contexts[0]
            context.set_default_timeout(12000)
            context.set_default_navigation_timeout(30000)
            original_empty = [page for page in context.pages if is_empty_page(page)]
            focus_messages = []
            startup = original_empty[0] if newly_started and not login and original_empty else None
            page = None
            try:
                page = create_work_page(browser, context,
                                        foreground=not login and settings.browser_visibility == "foreground",
                                        focus_warning=focus_messages.append, startup_page=startup)
                work_session = context.new_cdp_session(page)
                # Keep requestAnimationFrame/actionability checks working in a
                # background or minimized tab. Apply it only to our work page;
                # the user's visible tabs retain their normal focus state.
                work_session.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
                if not login and window_mode != "foreground":
                    background_work_window(browser, page, hidden=window_mode == "hidden")
                if not login and store.auth_path.exists():
                    import_saved_state(page, context, store)
                console = AutoDLConsole(page, settings)
                console.browser_warnings = focus_messages
                # Remove spare startup tabs as soon as a work tab exists, rather
                # than leaving a visible blank tab for the duration of the job.
                close_empty_pages(context, original_empty, protected=page)
                yield console, context
            finally:
                if close_browser_on_exit:
                    try:
                        browser.new_browser_cdp_session().send("Browser.close")
                    except Exception:
                        pass  # Already closed by the user or browser itself.
                else:
                    close_empty_pages(context, original_empty, protected=page)
                    # Non-rental jobs leave other dedicated-browser tabs intact.
                    if page is not None and not page.is_closed():
                        try:
                            page.close()
                        except Exception:
                            pass  # The user may close the ordinary browser themselves.
            return
        channel = None if settings.browser_channel == "chromium" else settings.browser_channel
        try:
            browser = playwright.chromium.launch(
                channel=channel, headless=window_mode == "hidden",
                args=["--disable-extensions", "--disable-sync", "--disable-features=msImplicitSignin",
                      *(["--start-minimized"] if window_mode == "minimized" else [])],
            )
        except Exception as exc:
            raise ConsoleError("浏览器无法启动。请安装所选 Edge / Chrome，或选择 Chromium 并先安装浏览器组件。") from exc
        context = None
        try:
            state = str(store.auth_path) if store.auth_path.exists() else None
            if not state and not login:
                raise SessionExpired("尚未保存登录态，请先点击“登录 AutoDL”。")
            context = browser.new_context(storage_state=state, viewport={"width": 1600, "height": 1000})
            context.set_default_timeout(12000)
            context.set_default_navigation_timeout(30000)
            page = context.new_page()
            session = context.new_cdp_session(page)
            session.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            if window_mode == "minimized":
                background_work_window(browser, page)
            # Native JS confirm dialogs are NOT used by the supported AutoDL UI.
            # Never silently accept an unfamiliar dialog.
            page.on("dialog", lambda dialog: dialog.dismiss())
            yield AutoDLConsole(page, settings), context
        finally:
            if context is not None:
                context.close()
            browser.close()


def save_auth(context, store: Store) -> None:
    state = context.storage_state(indexed_db=True)
    # Keep authentication limited to AutoDL; do not export unrelated browser state.
    state["cookies"] = [c for c in state["cookies"] if c["domain"].lstrip(".") == "autodl.com"
                        or c["domain"].lstrip(".").endswith(".autodl.com")]
    state["origins"] = [o for o in state["origins"] if urlsplit(o["origin"]).hostname in
                        {"www.autodl.com", "autodl.com"}]
    store.write("auth.json", state)


class AutoDLConsole:
    """DOM-only actions, with passive observation of each power request's acknowledgement.

    Selectors were checked against AutoDL's published Vue/Element Plus frontend.
    Any unrecognised table, dialog, countdown or pagination fails closed.
    """

    # Vue passes `class` both to the wrapper and to its inner el-table.
    # Select the loading-box wrapper so pagination stays in scope and rows aren't duplicated.
    TABLE = ".loading-box.instance-table"
    ROWS = ".el-table__body-wrapper tbody tr.el-table__row"
    ID_RE = re.compile(r"(?<![A-Za-z0-9-])(?:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}|[A-Za-z0-9]{6,24}-[A-Za-z0-9]{6,16})(?![A-Za-z0-9-])")

    def __init__(self, page: Page, settings: Settings, log: Callable[[str], None] | None = None):
        self.page = page
        self.settings = settings
        self.log = log or (lambda _: None)
        self.locations: dict[str, int] = {}

    def _check_origin(self):
        host = urlsplit(self.page.url).hostname
        if host != urlsplit(self.settings.console_url).hostname:
            raise SessionExpired("页面已离开 AutoDL 控制台，请重新登录。")
        if "/login" in urlsplit(self.page.url).path:
            raise SessionExpired("登录已过期，请重新登录 AutoDL。")

    def open(self, page_number: int = 1):
        # Fresh unfiltered route: include all statuses, tags and subaccounts.
        url = self.settings.console_url + f"?page_index={page_number}&page_size=10"
        self.page.goto(url, wait_until="domcontentloaded")
        self.wait_ready()

    def wait_ready(self):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._check_origin()
            if self.page.locator(self.TABLE).count() == 1:
                table = self.page.locator(self.TABLE)
                if table.locator(self.ROWS).count() or table.locator(".el-table__empty-block").is_visible():
                    self._wait_loading()
                    return
            self.page.wait_for_timeout(200)
        raise LayoutChanged("未找到实例表格。请确认已登录普通“容器实例”，并在网页列设置中显示状态、付费方式、释放时间及操作列。")

    def _wait_loading(self):
        masks = self.page.locator(self.TABLE).locator(".el-loading-mask:visible")
        deadline = time.monotonic() + 30
        while masks.count():
            if time.monotonic() >= deadline:
                raise LayoutChanged("实例列表仍在加载，请检查网络后重试")
            self.page.wait_for_timeout(100)

    def _headers(self) -> dict[str, int]:
        headers = self.page.locator(self.TABLE).locator(".el-table__header-wrapper th").all_inner_texts()
        result = {}
        for index, header in enumerate(headers):
            label = compact(header)
            if label.startswith("规格详情"):
                result["spec"] = index
            for key, pattern in {"id": "实例ID", "status": "状态", "release": "释放时间", "charge": "付费方式", "action": "操作"}.items():
                if label.startswith(pattern):
                    result[key] = index
        if not {"id", "status", "release", "charge", "action"}.issubset(result):
            raise LayoutChanged("实例表格缺少必要列。请在 AutoDL 网页的列设置中显示：实例ID、状态、付费方式、释放时间、操作。")
        return result

    def _read_page(self, page_number: int) -> list[Instance]:
        self._check_origin()
        headers = self._headers()
        rows = self.page.locator(self.TABLE).locator(self.ROWS)
        result = []
        for row in rows.all():
            cells = row.locator("td").all_inner_texts()
            if len(cells) <= max(headers.values()):
                raise LayoutChanged("表格行与表头不匹配，已停止操作。")
            identity = cells[headers["id"]]
            match = self.ID_RE.search(identity)
            if not match:
                raise LayoutChanged("有实例行无法识别唯一 ID，已停止操作。")
            uuid = match[0]
            lines = [line.strip() for line in identity.splitlines() if line.strip()]
            after = identity[match.end():].strip()
            status_text = compact(cells[headers["status"]])
            status = next((value for value in ("开机中", "关机中", "运行中", "已关机") if value in status_text), "未知")
            release_text = cells[headers["release"]]
            blocked = ""
            all_text = compact(" ".join(cells))
            if any(word in all_text for word in ("下架", "租期截止")):
                blocked = "主机下架倒计时，需人工处理"
            elif any(word in status_text for word in ("异常", "失败", "宕机", "错误", "维护", "重置", "迁移")):
                blocked = "实例异常或维护中，需人工处理"
            retention = re.search(r"关机(\d+)天后释放", compact(release_text))
            spec = re.search(r"(.+?)\s*\*\s*(\d+)\s*卡", cells[headers["spec"]], re.S) if "spec" in headers else None
            instance = Instance(
                uuid=uuid, name=after, region=lines[0] if lines else "",
                status=status, release_text=release_text,
                remaining_seconds=parse_countdown(release_text), no_gpu="无卡模式" in status_text,
                charge=compact(cells[headers["charge"]]).split("余额")[0], page=page_number,
                blocked_reason=blocked, retention_days=int(retention[1]) if retention else 15,
                gpu_name=spec[1].strip() if spec else "", gpu_count=int(spec[2]) if spec else 0,
            )
            result.append(instance)
            self.locations[uuid] = page_number
        return result

    def _total(self) -> int | None:
        text = self.page.locator(self.TABLE).inner_text()
        match = re.search(r"共\s*(\d+)\s*条", text)
        return int(match[1]) if match else None

    def scan(self, cancel=None) -> list[Instance]:
        self.open()
        result: dict[str, Instance] = {}
        total = self._total()
        signatures = set()
        for page_number in range(1, 1001):
            if cancel and cancel.is_set():
                raise Cancelled("已停止检查")
            rows = self._read_page(page_number)
            signature = tuple(row.uuid for row in rows)
            if signature in signatures and rows:
                raise LayoutChanged("翻页后实例列表没有变化，无法确认已检查所有分页。")
            signatures.add(signature)
            for row in rows:
                if row.uuid in result:
                    raise LayoutChanged("分页出现重复实例，列表可能发生变化，请重新检查。")
                result[row.uuid] = row
            self.log(f"已读取第 {page_number} 页，共发现 {len(result)} 个实例")
            next_button = self.page.locator(self.TABLE).locator(".el-pagination .btn-next")
            if not next_button.count():
                if total is None or total > len(result):
                    raise LayoutChanged("未识别到分页控件，无法证明列表已完整读取。")
                break
            if not next_button.is_enabled() or next_button.get_attribute("aria-disabled") == "true":
                break
            before = signature
            next_button.click()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self._wait_loading()
                if tuple(r.uuid for r in self._read_page(page_number + 1)) != before:
                    break
                self.page.wait_for_timeout(200)
            else:
                raise LayoutChanged("分页加载超时，请检查网络。")
        else:
            raise LayoutChanged("分页数量超过上限，已停止。")
        if total is not None and len(result) != total:
            raise LayoutChanged(f"网页显示 {total} 个实例，但只读取到 {len(result)} 个；本轮不执行开关机。")
        return list(result.values())

    def get(self, uuid: str) -> Instance:
        page_number = self.locations.get(uuid, 1)
        self.open(page_number)
        for instance in self._read_page(page_number):
            if instance.uuid == uuid:
                self._focus_row(uuid)
                return instance
        # Sorting may change during boot/shutdown. Resolve again by ID across every page.
        for instance in self.scan():
            if instance.uuid == uuid:
                # scan() finishes on the LAST page, not necessarily this row's page.
                self.open(instance.page)
                for refreshed in self._read_page(instance.page):
                    if refreshed.uuid == uuid:
                        self._focus_row(uuid)
                        return refreshed
        raise ConsoleError(f"找不到实例 {uuid}，可能列表或账号发生变化。")

    def _row(self, uuid: str):
        # Element Plus puts ID and name in adjacent divs. textContent joins them
        # (e.g. "...e3af4a7bRAMP5"), so regex word boundaries on the whole row fail.
        # Match the ID element itself, then locate its containing table row.
        identity = self.page.get_by_text(uuid, exact=True)
        rows = self.page.locator(self.TABLE).locator(self.ROWS).filter(has=identity)
        if rows.count() != 1:
            raise LayoutChanged(f"无法唯一定位实例 {uuid}，已停止。")
        return rows

    def _focus_row(self, uuid: str):
        row = self._row(uuid)
        row.scroll_into_view_if_needed()
        # Center the complete row in all scrolling ancestors, rather than just
        # letting a button click reveal a small part of the row at the edge.
        row.evaluate("element => element.scrollIntoView({block:'center', inline:'nearest', behavior:'instant'})")
        return row

    def _dialog(self, action: str, uuid: str):
        dialog = self.page.locator(".el-message-box:visible")
        dialog.wait_for(state="visible", timeout=8000)
        text = compact(dialog.inner_text())
        if action == "start":
            valid = "确认无卡模式开机吗" in text and "无GPU卡" in text
        else:
            valid = "确认关机吗" in text and uuid in text
        if not valid:
            raise LayoutChanged("出现非预期的确认弹窗，请在浏览器中人工检查；本轮已停止。")
        button = dialog.get_by_role("button", name=re.compile(r"^(确定|确认)$"))
        if button.count() != 1:
            raise LayoutChanged("未识别到唯一的操作确认按钮。")
        return button

    def _submit(self, action: str, uuid: str, button, accepted: Callable[[], None], submitted=None):
        endpoint = "/api/v1/instance/power_on" if action == "start" else "/api/v1/instance/power_off"

        def matches(response):
            request = response.request
            if urlsplit(response.url).path != endpoint or request.method != "POST":
                return False
            try:
                body = request.post_data_json
            except Exception:
                return False
            return isinstance(body, dict) and body.get("instance_uuid") == uuid and (action != "start" or body.get("payload") == "non_gpu")

        try:
            # Observe the website's request, do not directly invoke any private endpoint.
            with self.page.expect_response(matches, timeout=20000) as response_info:
                if submitted:
                    # Persist BEFORE attempting the irreversible confirmation click.
                    # Errors finding the row/menu/dialog are still only preflight.
                    submitted()
                button.click()
            response = response_info.value
            payload = response.json()
        except (PlaywrightTimeoutError, ValueError) as exc:
            raise ConsoleError("操作已提交但未能确认平台响应，已保存待核验记录；请重新检查，勿重复开机。") from exc
        if response.status != 200 or payload.get("code") != "Success":
            # Deliberately avoid including full server responses (may contain credentials).
            raise ActionRejected(f"平台拒绝{'无卡开机' if action == 'start' else '关机'}操作（{payload.get('code', response.status)}），请在网页检查余额、权限或无卡实例占用。")
        accepted()

    def start_no_gpu(self, uuid: str, accepted: Callable[[], None], submitted=None, *, refresh_all=False):
        current = self.get(uuid)
        if not current.eligible(self.settings.threshold_days, refresh_all=refresh_all):
            raise ActionRejected("实例状态或释放时间已变化，已跳过开机。")
        self._row(uuid).get_by_role("button", name="更多", exact=True).click()
        item = self.page.locator(".el-dropdown-menu:visible").get_by_text("无卡模式开机", exact=True)
        item.wait_for(state="visible")
        disabled = item.evaluate("el => !!el.closest('.is-disabled, [aria-disabled=\"true\"]')")
        if disabled:
            raise ActionRejected("该实例的无卡模式开机不可用。")
        item.click()
        self._submit("start", uuid, self._dialog("start", uuid), accepted, submitted)

    def stop(self, uuid: str):
        current = self.get(uuid)
        if current.status == "已关机":
            return
        if current.status != "运行中" or not current.no_gpu:
            raise ConsoleError("待关机实例不是运行中的无卡实例，已停止自动操作。")
        self._row(uuid).get_by_role("button", name="关机", exact=True).click()
        self._submit("stop", uuid, self._dialog("stop", uuid), lambda: None)

    def wait(self, uuid: str, predicate: Callable[[Instance], bool], label: str) -> Instance:
        deadline = time.monotonic() + self.settings.timeout_seconds
        while time.monotonic() < deadline:
            current = self.get(uuid)
            if predicate(current):
                return current
            self.page.wait_for_timeout(self.settings.poll_seconds * 1000)
        raise ConsoleError(f"等待{label}超时（{self.settings.timeout_seconds} 秒），请检查实例 {uuid}。")


def reset_verified(instance: Instance, retention_days: int, before_seconds: int, *, accepted_full_refresh=False) -> bool:
    # An accepted full refresh may begin with an already fresh countdown. The
    # page rounds to minutes, so a verified start/stop need not increase it.
    # Ambiguous/unacknowledged requests ALWAYS retain the strict-increase rule.
    near_full_before = accepted_full_refresh and before_seconds >= retention_days * DAY - 900
    return (instance.status == "已关机" and instance.remaining_seconds is not None
            and instance.remaining_seconds >= retention_days * DAY - 900
            and (instance.remaining_seconds > before_seconds or near_full_before))
