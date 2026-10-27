from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time

_launch_lock = threading.Lock()


def activate_browser_window(marker: str) -> bool:
    """Raise the dedicated work window by its temporary unique tab title.

    Target.activateTarget selects a tab but Windows may still keep its window
    behind another application. This is called ONLY for explicit foreground
    mode; no window outside the unique app-owned marker is modified.
    """
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def visit(hwnd, _):
        buffer = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        if marker in buffer.value:
            found.append(hwnd)
        return True

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        found.clear()
        user32.EnumWindows(callback_type(visit), 0)
        if len(found) == 1:
            break
        time.sleep(.05)
    if len(found) != 1:
        return False
    hwnd = found[0]
    # SW_SHOW preserves maximized state; restore only a minimized window.
    user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)
    user32.SetForegroundWindow(hwnd)
    # Windows may deny focus changes while another app owns active input.
    # Respect that restriction rather than injecting keyboard/mouse input.
    return user32.GetForegroundWindow() == hwnd


def external_environment() -> dict[str, str]:
    """System-installed browsers must not inherit bundled Qt library directories."""
    env = os.environ.copy()
    if not getattr(sys, "frozen", False):
        return env
    bundle = Path(sys._MEIPASS).resolve()
    env["PATH"] = os.pathsep.join(
        part for part in env.get("PATH", "").split(os.pathsep)
        if part and not Path(part).resolve().is_relative_to(bundle)
    )
    if sys.platform != "win32":
        env["LD_LIBRARY_PATH"] = env.get("LD_LIBRARY_PATH_ORIG", "")
    return env


def launch_external(args, **kwargs):
    """Temporarily restore the Windows DLL search path for an external browser.

    See PyInstaller's documented external-process launch requirements. This does
    not alter the OS configuration or disable any browser sandbox/security flag.
    """
    kwargs.setdefault("env", external_environment())
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return subprocess.Popen(args, **kwargs)
    import ctypes
    with _launch_lock:
        buffer = ctypes.create_unicode_buffer(32768)
        ctypes.windll.kernel32.GetDllDirectoryW(len(buffer), buffer)
        previous = buffer.value
        ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            return subprocess.Popen(args, **kwargs)
        finally:
            ctypes.windll.kernel32.SetDllDirectoryW(previous or None)


def set_windows_startup(enabled: bool):
    if sys.platform != "win32":
        if enabled:
            raise RuntimeError("开机启动设置目前仅支持 Windows")
        return
    import winreg
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--minimized"]
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        launcher = Path(__file__).resolve().parent.parent / "run_app.py"
        args = [str(pythonw if pythonw.exists() else sys.executable), str(launcher), "--minimized"]
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            winreg.SetValueEx(key, "AutoDLFresh", 0, winreg.REG_SZ, subprocess.list2cmdline(args))
        else:
            try:
                winreg.DeleteValue(key, "AutoDLFresh")
            except FileNotFoundError:
                pass


def restart_application():
    env = external_environment()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--restart"]
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        args = [str(pythonw if pythonw.exists() else sys.executable),
                str(Path(__file__).resolve().parent.parent / "run_app.py"), "--restart"]
    return launch_external(args, env=env)


def open_directory(path: Path):
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
