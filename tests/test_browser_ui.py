import time
import ctypes
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

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

    server = HTTPServer(("127.0.0.1", 0), Handler)
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
        previous_foreground = foreground_process()
        with browser_session(Settings(browser_visibility="background"), store) as (console, context):
            console.page.goto(url + "/work")
            console.page.get_by_role("button", name="Click", exact=True).click()
            assert console.page.get_by_role("button", name="Clicked").count() == 1
            assert selected_title(native, "Original")
            assert foreground_process() == previous_foreground
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
