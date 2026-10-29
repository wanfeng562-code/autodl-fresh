"""Non-modal browser notices and lifecycle for app-owned working tabs."""
from __future__ import annotations

import time
from urllib.parse import urlsplit
from uuid import uuid4

from .platform_support import activate_browser_window, background_browser_windows

# Edge's command line treats about:blank with a fragment as an unknown start URL
# and substitutes its online new-tab page. Keep the initial URL plain.
STARTUP_URL = "about:blank"
WORK_PREFIX = "about:blank#autodl-fresh-work-"
NOTICE_ID = "autodl-fresh-login-saved"


def background_work_window(browser, page, *, hidden=False):
    client = browser.new_browser_cdp_session()
    session = page.context.new_cdp_session(page)
    try:
        target_id = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
        window = client.send("Browser.getWindowForTarget", {"targetId": target_id})
        client.send("Browser.setWindowBounds", {"windowId": window["windowId"],
                                                "bounds": {"windowState": "minimized"}})
        processes = client.send("SystemInfo.getProcessInfo")["processInfo"]
        process_id = next(int(p["id"]) for p in processes if p["type"] == "browser")
        if hidden and not background_browser_windows(process_id, hidden=True):
            raise RuntimeError("未能隐藏后台核价专用窗口，本次核价已停止")
        if not hidden:
            background_browser_windows(process_id)
    finally:
        session.detach()
        client.detach()


def is_empty_page(page) -> bool:
    return page.url in {"about:blank", "edge://newtab/", "chrome://newtab/", STARTUP_URL} or page.url.startswith(WORK_PREFIX)


def close_empty_pages(context, original_empty, *, protected=None):
    """Close startup/legacy blank tabs, preserving tabs the user navigated elsewhere."""
    for page in list(context.pages):
        if page is protected or page.is_closed():
            continue
        if (page in original_empty or page.url == STARTUP_URL or page.url.startswith(WORK_PREFIX)) and is_empty_page(page):
            try:
                page.close()
            except Exception:
                pass


def create_work_page(browser, context, *, foreground=False, focus_warning=None, startup_page=None):
    """Unlike new_page(), CDP can create a tab without activating it."""
    client = browser.new_browser_cdp_session()
    marker = WORK_PREFIX + uuid4().hex
    target = None
    try:
        if startup_page is not None and not startup_page.is_closed() and is_empty_page(startup_page):
            session = context.new_cdp_session(startup_page)
            try:
                target = {"targetId": session.send("Target.getTargetInfo")["targetInfo"]["targetId"]}
            finally:
                session.detach()
        else:
            with context.expect_page():
                target = client.send("Target.createTarget", {"url": marker, "background": not foreground})
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            for candidate in context.pages:
                if candidate.is_closed():
                    continue
                session = context.new_cdp_session(candidate)
                try:
                    info = session.send("Target.getTargetInfo")["targetInfo"]
                    if info["targetId"] == target["targetId"]:
                        if foreground:
                            restore = client.send("Browser.getWindowForTarget", {"targetId": info["targetId"]})
                            if restore["bounds"].get("windowState") == "minimized":
                                client.send("Browser.setWindowBounds", {"windowId": restore["windowId"],
                                                                        "bounds": {"windowState": "normal"}})
                            candidate.bring_to_front()
                            title = "AutoDL Fresh " + uuid4().hex
                            candidate.evaluate("title => document.title = title", title)
                            if not activate_browser_window(title) and focus_warning:
                                focus_warning("已显示浏览器工作标签；Windows 未允许切到系统前台，可手动切换到专用窗口。任务继续执行。")
                        return candidate
                finally:
                    session.detach()
            client.send("Target.getTargets")  # Pump target-attachment events.
            time.sleep(0.05)
        raise RuntimeError("未能连接本次检查标签页，请关闭专用窗口后重试。")
    except Exception:
        if target:
            client.send("Target.closeTarget", {"targetId": target["targetId"]})
        raise
    finally:
        client.detach()


def show_login_saved(context, preferred_page, site_url: str) -> int:
    """Show a status banner, without alert(), focus changes or a confirmation button."""
    host = urlsplit(site_url).hostname
    pages = [page for page in context.pages if not page.is_closed() and urlsplit(page.url).hostname == host]
    if preferred_page not in pages and not preferred_page.is_closed():
        pages.append(preferred_page)
    count = 0
    for page in pages:
        try:
            page.evaluate("""({id, text}) => {
                document.getElementById(id)?.remove();
                const notice = document.createElement('div');
                notice.id = id;
                notice.setAttribute('role', 'status');
                notice.setAttribute('aria-live', 'polite');
                notice.textContent = text;
                notice.style.cssText = 'position:fixed;top:24px;left:50%;transform:translateX(-50%);'
                    + 'z-index:2147483647;background:#e6f5eb;color:#145b3b;border:1px solid #88c7a2;'
                    + 'border-radius:10px;padding:16px 24px;box-shadow:0 8px 30px #0002;'
                    + 'font:16px system-ui;max-width:80vw;text-align:center;pointer-events:none;';
                (document.body || document.documentElement).appendChild(notice);
                setTimeout(() => notice.remove(), 20000);
            }""", {"id": NOTICE_ID, "text": "AutoDL 登录信息已保存，可以关闭当前窗口。"})
            count += 1
        except Exception:
            pass  # Closing a window must not turn a successful save into a failure.
    return count
