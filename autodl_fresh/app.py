from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .storage import Store
from .locations import configuration_directory
from .ui import MainWindow
from .display import prepare_display, application_font
from . import __version__


def main():
    parser = argparse.ArgumentParser(description="AutoDL Fresh 桌面实例保活助手")
    parser.add_argument("--minimized", action="store_true", help="启动后留在系统托盘")
    parser.add_argument("--restart", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    prepare_display()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("AutoDL Fresh")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AutoDLFresh")
    app.setStyle("Fusion")
    app.setFont(application_font())
    app.setQuitOnLastWindowClosed(True)
    try:
        store = Store()
        # Lock at the stable launcher location, not the movable data directory.
        # Otherwise changing data directories could create two active schedulers.
        lock_root = configuration_directory()
        lock_root.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(lock_root / "app.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(10000 if args.restart else 100):
            QMessageBox.information(None, "AutoDL Fresh", "应用已经在运行，请双击系统托盘中的 Fresh 图标。")
            return
        window = MainWindow(store)
    except Exception as exc:
        QMessageBox.critical(None, "AutoDL Fresh 无法启动", str(exc))
        return
    # Keep the lock alive for the entire process, preventing duplicate automatic jobs.
    app._instance_lock = lock
    app.aboutToQuit.connect(lock.unlock)
    if not args.minimized or not window.tray.isVisible():
        window.show()
    QTimer.singleShot(2500, window.check_updates_automatically)
    sys.exit(app.exec())
