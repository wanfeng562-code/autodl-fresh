import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import time

from playwright.sync_api import sync_playwright
import pytest

from autodl_fresh.native_browser import NativeBrowser, browser_args, prepare_private_profile
from autodl_fresh.storage import Settings, Store


def test_normal_browser_arguments_use_app_only_profile(tmp_path):
    profile = tmp_path / "dedicated-profile"
    args = browser_args(Path("msedge.exe"), profile, "about:blank", 38456)
    assert f"--user-data-dir={profile}" in args
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=38456" in args
    assert "--disable-extensions" in args
    assert "--disable-sync" in args
    assert "--disable-features=msImplicitSignin" in args
    assert not any("enable-automation" in arg or "headless" in arg or "AutomationControlled" in arg for arg in args)
    assert "--start-minimized" not in args
    background_args = browser_args(Path("msedge.exe"), profile, "about:blank", 38456, background=True)
    assert "--start-minimized" in background_args


def test_privacy_profile_blocks_browser_identity_and_personal_autofill(tmp_path):
    profile = tmp_path / "private"
    prepare_private_profile(profile)
    prefs = json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8"))
    assert prefs["signin"] == {"allowed": False, "allowed_on_next_startup": False}
    assert not prefs["credentials_enable_service"]
    assert not prefs["autofill"]["profile_enabled"]
    assert not prefs["autofill"]["credit_card_enabled"]


def test_legacy_synced_profile_is_not_reused_or_modified(tmp_path):
    old = tmp_path / "browser-profile-msedge" / "Default"
    old.mkdir(parents=True)
    path = old / "Preferences"
    original = '{"account_info": [{"email": "fixture-only@example.invalid"}]}'
    path.write_text(original, encoding="utf-8")
    native = NativeBrowser(Settings(), Store(tmp_path))
    prepare_private_profile(native.profile)
    assert native.profile != old.parent
    assert not json.loads((native.profile / "Default" / "Preferences").read_text(encoding="utf-8")).get("account_info")
    assert path.read_text(encoding="utf-8") == original


def test_an_account_in_the_new_private_profile_stops_reuse(tmp_path):
    profile = tmp_path / "private"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Preferences").write_text('{"account_info": [{"fixture": true}]}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="浏览器账号资料"):
        prepare_private_profile(profile)


def test_compatibility_mode_also_disables_browser_sync_and_extensions(tmp_path):
    from autodl_fresh.browser import browser_session
    with browser_session(Settings(browser_mode="playwright"), Store(tmp_path), login=True) as (console, context):
        console.page.goto("edge://version")
        command_line = console.page.locator("#command_line").inner_text()
        assert "--disable-extensions" in command_line
        assert "--disable-sync" in command_line
        assert "--disable-features=msImplicitSignin" in command_line


def test_invalid_debugging_metadata_is_rejected(tmp_path):
    native = NativeBrowser(Settings(), Store(tmp_path))
    native.profile.mkdir()
    for content in ["", "not a port", "0\n/devtools/browser/x", "70000\nx"]:
        native.port_file.write_text(content, encoding="utf-8")
        assert native.endpoint() is None


def close_test_browser(browser, native):
    try:
        browser.new_browser_cdp_session().send("Browser.close")
    except Exception:
        pass
    deadline = time.monotonic() + 5
    while native.endpoint() and time.monotonic() < deadline:
        time.sleep(0.1)


def test_native_browser_can_reconnect_and_preserve_its_own_profile(tmp_path):
    """Launch ordinary Edge, attach later, close, relaunch and verify saved state.

    Only a local fixture origin and this test's profile are accessed.
    """
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<!doctype html><title>Fresh local fixture</title>")

        def log_message(self, *args):
            pass

    # Some Windows hosts allocate port 0 from a low range containing Chromium's
    # blocked service ports (e.g. 2049). Bind and retain a safe fixture port.
    for port in range(20000, 20100):
        try:
            server = HTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        pytest.fail("No free local fixture port in 20000–20099")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    store = Store(tmp_path)
    native = NativeBrowser(Settings(), store)
    with sync_playwright() as playwright:
        browser = None
        try:
            endpoint = native.ensure_open()
            assert endpoint.startswith("http://127.0.0.1:")
            browser = playwright.chromium.connect_over_cdp(endpoint)
            context = browser.contexts[0]
            page = context.new_page()
            page.goto("edge://version")
            command_line = page.locator("#command_line").inner_text()
            assert "--disable-extensions" in command_line
            assert "--disable-sync" in command_line
            assert "--disable-features=msImplicitSignin" in command_line
            page.goto(origin)
            page.evaluate("localStorage.setItem('fixture-only', 'retained')")
            page.close()
            close_test_browser(browser, native)
            browser = None
            prefs = json.loads((native.profile / "Default" / "Preferences").read_text(encoding="utf-8"))
            assert not prefs.get("account_info")
            assert prefs["signin"]["allowed"] is False
            secure = native.profile / "Default" / "Secure Preferences"
            if secure.exists():
                extension_settings = json.loads(secure.read_text(encoding="utf-8")).get("extensions", {}).get("settings", {})
                assert not [item for item in extension_settings.values() if item.get("location") != 5]
            native = NativeBrowser(Settings(), store)
            assert not native.fresh_profile
            endpoint = native.ensure_open()
            browser = playwright.chromium.connect_over_cdp(endpoint)
            page = browser.contexts[0].new_page()
            page.goto(origin)
            assert page.evaluate("localStorage.getItem('fixture-only')") == "retained"
        finally:
            if browser:
                close_test_browser(browser, native)
            server.shutdown()
            server.server_close()
