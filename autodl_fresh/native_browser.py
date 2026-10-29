"""Ordinary installed browser with an app-only profile and a loopback CDP channel.

The user signs in manually BEFORE Playwright attaches. No automation-hiding flags,
CAPTCHA interaction, personal profile access, or verification bypass is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from .storage import Settings, Store
from .platform_support import launch_external
from .browser_ui import STARTUP_URL

PROFILE_PREFIX = "browser-autodl-only-v1"


def prepare_private_profile(profile: Path) -> None:
    """Configure only this app's stopped browser profile before its first page.

    A separate directory alone does not prevent Edge's Windows-account sign-in.
    No registry or daily-browser policies are changed, and legacy profile data
    is never imported. Website authentication continues to use auth.json.
    """
    directory = profile / "Default"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "Preferences"
    try:
        prefs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (ValueError, OSError) as exc:
        raise RuntimeError("AutoDL 专用浏览器配置无法读取，已停止启动。") from exc
    if not isinstance(prefs, dict):
        raise RuntimeError("AutoDL 专用浏览器配置格式无效，已停止启动。")
    if prefs.get("account_info"):
        raise RuntimeError("专用浏览器配置中检测到浏览器账号资料，已停止复用。请关闭此专用窗口后联系修复；不会读取账号内容。")
    prefs.setdefault("signin", {}).update(allowed=False, allowed_on_next_startup=False)
    prefs["credentials_enable_service"] = False
    prefs.setdefault("autofill", {}).update(profile_enabled=False, credit_card_enabled=False)
    tmp = path.with_suffix(".fresh.tmp")
    tmp.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def browser_executable(channel: str, chromium_path: str | None = None) -> Path:
    if channel == "chromium" and chromium_path and Path(chromium_path).is_file():
        return Path(chromium_path)
    programs = [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                os.environ.get("LOCALAPPDATA", "")]
    suffix = "Microsoft/Edge/Application/msedge.exe" if channel == "msedge" else "Google/Chrome/Application/chrome.exe"
    candidates = [Path(root) / suffix for root in programs if root]
    name = "msedge" if channel == "msedge" else "google-chrome"
    if executable := shutil.which(name):
        candidates.insert(0, Path(executable))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("未找到所选浏览器。请先安装 Edge / Chrome，或在设置中选择已安装的浏览器。")


def browser_args(executable: Path, profile: Path, url: str, port: int, *, background=False) -> list[str]:
    if not 1 <= port <= 65535:
        raise ValueError("浏览器连接端口无效")
    return [str(executable), f"--user-data-dir={profile}", "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}", "--disable-extensions", "--disable-sync",
            "--disable-features=msImplicitSignin", "--no-first-run", "--no-default-browser-check",
            *(["--start-minimized"] if background else []), "--new-window", url]


class NativeBrowser:
    def __init__(self, settings: Settings, store: Store, *, window_mode=None):
        self.settings = settings
        self.store = store
        self.window_mode = window_mode or ("foreground" if settings.browser_visibility == "foreground" else "minimized")
        # New namespace deliberately never reuses v0.2 profiles, which may have
        # implicitly signed into Microsoft and downloaded personal extensions.
        self.profile = store.root / f"{PROFILE_PREFIX}-{settings.browser_channel}"
        if self.window_mode == "hidden":
            # Idle quotation work must never hide or close a manual/rental window.
            self.profile = self.profile.with_name(self.profile.name + "-quotes")
        self.port_file = self.profile / "FreshDebugPort"
        self.fresh_profile = not (self.profile / "Default").exists()
        self.process = None

    def open_login_window(self, *, cancel=None):
        endpoint = self.endpoint()
        if not endpoint:
            return self.ensure_open(login=True, cancel=cancel)
        # Ask the already running dedicated browser for a visible login window,
        # without attaching an automation client to its pages.
        self._launch(login=True)
        return endpoint

    def _launch(self, *, login=False, chromium_path=None):
        self.profile.mkdir(parents=True, exist_ok=True)
        if endpoint := self.endpoint():
            port = urlsplit(endpoint).port
        else:
            prepare_private_profile(self.profile)
            # Use a regular explicit CDP port, reserved from the loopback range.
            # Never attach to the default personal profile or a fixed shared port.
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            self.port_file.write_text(str(port), encoding="utf-8")
        background = not login and self.window_mode != "foreground"
        args = browser_args(browser_executable(self.settings.browser_channel, chromium_path), self.profile,
                            self.settings.console_url if login else STARTUP_URL, port, background=background)
        options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 1 if login or self.window_mode == "foreground" else 0 if self.window_mode == "hidden" else 7
            options["startupinfo"] = info
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self.process = launch_external(args, **options)

    def endpoint(self) -> str | None:
        try:
            port = int(self.port_file.read_text(encoding="utf-8").splitlines()[0])
            if not 1 <= port <= 65535:
                return None
            endpoint = f"http://127.0.0.1:{port}"
            # Never send localhost debugging traffic through the user's web proxy.
            with build_opener(ProxyHandler({})).open(endpoint + "/json/version", timeout=0.5) as response:
                metadata = json.load(response)
            socket_url = urlsplit(metadata.get("webSocketDebuggerUrl", ""))
            if socket_url.hostname != "127.0.0.1" or socket_url.port != port or socket_url.scheme != "ws":
                return None
            return endpoint
        except (OSError, ValueError, IndexError, KeyError):
            return None

    def ensure_open(self, *, login=False, chromium_path=None, cancel=None) -> str:
        if endpoint := self.endpoint():
            return endpoint
        self._launch(login=login, chromium_path=chromium_path)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if cancel and cancel.is_set():
                raise RuntimeError("已结束浏览器连接。")
            if endpoint := self.endpoint():
                return endpoint
            time.sleep(0.15)
        raise RuntimeError("普通浏览器未能建立本机连接。请关闭 Fresh 专用浏览器窗口后重试，或在设置中切换兼容模式。")


def import_saved_state(page, context, store: Store):
    """Restore missing app-owned session state, never overwrite a newer browser login."""
    state = store.read("auth.json", {})
    cookies = [cookie for cookie in state.get("cookies", [])
               if cookie["domain"].lstrip(".") == "autodl.com"
               or cookie["domain"].lstrip(".").endswith(".autodl.com")]
    existing = {(c["domain"].lstrip("."), c["path"], c["name"]) for c in context.cookies()}
    missing = [c for c in cookies if (c["domain"].lstrip("."), c["path"], c["name"]) not in existing]
    if missing:
        context.add_cookies(missing)
    origins = [origin for origin in state.get("origins", [])
               if urlsplit(origin["origin"]).hostname in {"www.autodl.com", "autodl.com"}]
    if origins:
        script = """(states => {
            const state = states.find(s => s.origin === location.origin);
            if (state) for (const item of state.localStorage || [])
                if (localStorage.getItem(item.name) === null) localStorage.setItem(item.name, item.value);
        })(""" + json.dumps(origins) + ");"
        page.add_init_script(script=script)
