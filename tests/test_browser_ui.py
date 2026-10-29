import time
import ctypes
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from contextlib import nullcontext

import pytest

from playwright.sync_api import sync_playwright

from autodl_fresh.browser import browser_session
from autodl_fresh.browser_ui import NOTICE_ID, STARTUP_URL, create_work_page, show_login_saved
from autodl_fresh.native_browser import NativeBrowser
from autodl_fresh.storage import Settings, Store


def close_native(native):
    endpoint = native.endpoint()
    if not endpoint:
        return
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(endpoint, no_defaults=True)
        try:
            browser.new_browser_cdp_session().send("Browser.close")
        except Exception:
            pass


def window_titles(native):
    """Inspect only this test process's windows, not unrelated browser tabs.

    document.visibilityState is also hidden when Windows occludes the browser,
    so the native window title is the reliable selected-tab check here.
    """
    titles = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == native.process.pid:
            buffer = ctypes.create_unicode_buffer(1024)
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
            if buffer.value:
                titles.append(buffer.value)
        return True
    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return titles


def selected_title(native, title):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if any(text.startswith(title) for text in window_titles(native)):
            return True
        time.sleep(.05)
    return False


def foreground_process():
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value


def test_background_session_preserves_active_tab_and_cleans_blanks(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            title = "Original" if self.path == "/" else "Working"
            self.wfile.write(f'<!doctype html><title>{title}</title><button onclick="this.textContent=\'Clicked\'">Click</button>'.encode())

        def log_message(self, *args):
            pass

    for port in range(20100, 20200):
        try:
            server = HTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError("No free safe fixture port")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    store = Store(tmp_path)
    native = NativeBrowser(Settings(browser_visibility="foreground"), store)
    native.ensure_open()
    try:
        # A separate driver, as in successive real application operations.
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(native.endpoint(), no_defaults=True)
            context = browser.contexts[0]
            original = context.pages[0]
            original.goto(url)
            original.bring_to_front()
            legacy_blank = context.new_page()
            legacy_blank.goto(STARTUP_URL)
            original.bring_to_front()
            assert selected_title(native, "Original")
            probe = create_work_page(browser, context, foreground=False)
            assert probe.evaluate("document.visibilityState") == "hidden"
            assert selected_title(native, "Original")
            probe.close()
        with browser_session(Settings(browser_visibility="background"), store) as (console, context):
            assert not any(page.url == STARTUP_URL for page in context.pages if page is not console.page)
            console.page.goto(url + "/work")
            console.page.get_by_role("button", name="Click", exact=True).click()
            assert console.page.get_by_role("button", name="Clicked").count() == 1
            assert selected_title(native, "Original")
            client = context.new_cdp_session(console.page)
            target = client.send('Target.getTargetInfo')['targetInfo']['targetId']
            assert client.send('Browser.getWindowForTarget', {'targetId':target})['bounds']['windowState'] == 'minimized'
            client.detach()
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(native.endpoint(), no_defaults=True)
            assert [page.url for page in browser.contexts[0].pages] == [url + "/"]
        with browser_session(Settings(browser_visibility="foreground"), store) as (console, context):
            console.page.goto(url + "/work")
            assert selected_title(native, "Working")
            if foreground_process() != native.process.pid:
                assert any("Windows 未允许" in message for message in console.browser_warnings)
            active = next(page for page in context.pages if page is not console.page)
            assert show_login_saved(context, console.page, url) == 2
            notice = active.locator(f"#{NOTICE_ID}")
            assert "可以关闭当前窗口" in notice.inner_text()
            assert notice.get_attribute("role") == "status"
            assert notice.locator("button").count() == 0
            assert active.evaluate("document.activeElement?.id") != NOTICE_ID
    finally:
        close_native(native)
        server.shutdown()
        server.server_close()


def test_check_only_browser_leaves_no_empty_window(tmp_path):
    store = Store(tmp_path)
    native = NativeBrowser(Settings(), store)
    with browser_session(Settings(), store) as (console, context):
        assert len(context.pages) == 1  # Reuse the initial tab, never add a spare blank.
        console.page.set_content("<title>Working</title>")
    deadline = time.monotonic() + 5
    try:
        while native.endpoint() and time.monotonic() < deadline:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(native.endpoint())
                pages = browser.contexts[0].pages
                assert not pages
            time.sleep(0.1)
    finally:
        close_native(native)


def test_background_cold_start_stays_minimized_and_keeps_dom_working(tmp_path):
    store = Store(tmp_path)
    native = NativeBrowser(Settings(), store)
    try:
        with browser_session(Settings(), store, native=native) as (console, context):
            console.page.set_content('<title>Silent reading</title><button onclick="this.textContent=\'Read\'">Read instances</button>')
            console.page.get_by_role("button", name="Read instances").click()
            assert len(context.pages) == 1
            client = context.new_cdp_session(console.page)
            info = client.send("Target.getTargetInfo")["targetInfo"]
            bounds = client.send("Browser.getWindowForTarget", {"targetId": info["targetId"]})["bounds"]
            assert bounds["windowState"] == "minimized"
            client.detach()
    finally:
        close_native(native)


def test_read_worker_uses_ordinary_minimized_browser(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from playwright.sync_api import BrowserType
    from autodl_fresh import worker as worker_module
    from autodl_fresh.worker import BrowserWorker
    from test_browser import FakeSite
    store = Store(tmp_path)
    store.write('auth.json', {'cookies':[], 'origins':[]})
    settings = Settings(browser_visibility='foreground', browser_mode='native',
                        console_url='http://127.0.0.1:18937/console/instance/list')
    monkeypatch.setattr(Settings, 'validate', lambda self: None)
    launches = []
    original_launch = BrowserType.launch
    def launch(self, *args, **kwargs):
        launches.append(kwargs.get('headless'))
        return original_launch(self, *args, **kwargs)
    monkeypatch.setattr(BrowserType, 'launch', launch)
    site = FakeSite()
    states = []
    @contextmanager
    def intercepted(settings, store, **kwargs):
        with browser_session(settings, store, **kwargs) as (console, context):
            context.route('**/*', site.route)
            client = context.new_cdp_session(console.page)
            target = client.send('Target.getTargetInfo')['targetInfo']['targetId']
            states.append(client.send('Browser.getWindowForTarget', {'targetId': target})['bounds']['windowState'])
            client.detach()
            yield console, context
    monkeypatch.setattr(worker_module, 'browser_session', intercepted)
    reports, failures = [], []
    worker = BrowserWorker(settings, store, 'preview')
    worker.completed.connect(reports.append)
    worker.failed.connect(failures.append)
    worker.run()
    assert launches == [] and states == ['minimized'] and not failures
    assert len(reports) == 1 and len(reports[0].instances) == 4 and not reports[0].errors
    assert not site.events


def test_hidden_quote_window_has_no_visible_taskbar_window_and_uses_separate_profile(tmp_path):
    settings, store = Settings(), Store(tmp_path)
    normal = NativeBrowser(settings, store)
    hidden = NativeBrowser(settings, store, window_mode="hidden")
    assert normal.profile != hidden.profile
    visible = []
    try:
        with browser_session(settings, store, native=hidden, window_mode="hidden") as (console, context):
            console.page.set_content('<title>Hidden fixture quotes</title><button onclick="this.textContent=\'Read\'">Read quotes</button>')
            console.page.get_by_role('button', name='Read quotes').click()
            assert console.page.get_by_role('button', name='Read', exact=True).count() == 1
            user32 = ctypes.windll.user32
            user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
            user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
            client = console.page.context.browser.new_browser_cdp_session()
            processes = client.send('SystemInfo.getProcessInfo')['processInfo']
            process_id = next(int(p['id']) for p in processes if p['type'] == 'browser')
            client.detach()
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def visit(hwnd, _):
                owner = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value == process_id:
                    name = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, name, len(name))
                    if name.value == 'Chrome_WidgetWin_1':
                        visible.append(bool(user32.IsWindowVisible(hwnd)))
                return True
            user32.EnumWindows(callback_type(visit), 0)
            assert visible == [False]
            # Windows can choose a different foreground app (or initially have
            # none). Only this dedicated hidden browser must never be active.
            assert foreground_process() != process_id
    finally:
        close_native(hidden)


@pytest.mark.parametrize('failure', [False, True])
def test_rental_session_ends_its_dedicated_browser_process_and_preserves_exceptions(tmp_path, failure):
    settings, store = Settings(), Store(tmp_path)
    native = NativeBrowser(settings, store)
    try:
        with pytest.raises(RuntimeError, match='fixture failure') if failure else nullcontext():
            with browser_session(settings, store, native=native, close_browser_on_exit=True) as (console, context):
                context.new_page().set_content('<title>Another dedicated test tab</title>')
                assert native.endpoint()
                if failure:
                    raise RuntimeError('fixture failure')
        deadline = time.monotonic() + 5
        while native.endpoint() and time.monotonic() < deadline:
            time.sleep(.05)
        assert native.endpoint() is None
        native.process.wait(timeout=5)
    finally:
        close_native(native)
