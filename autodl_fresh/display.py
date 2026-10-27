"""Native DPI setup, before QApplication creates any Windows handles."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QFont


def prepare_display():
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # Packaged Windows builds declare this in their manifest. The Python source
    # launcher has no app-specific manifest, so initialize it before QApplication.
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        import ctypes
        try:
            setter = ctypes.windll.user32.SetProcessDpiAwarenessContext
            setter.argtypes = [ctypes.c_void_p]
            setter(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        except AttributeError:
            pass


def application_font():
    font = QFont("Microsoft YaHei UI" if sys.platform == "win32" else "sans-serif")
    font.setPixelSize(14)  # Logical pixels; Qt rasterizes at each monitor's DPI.
    return font
