from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QTime, QTimer, QPointF, QStandardPaths, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from .models import Instance, RunReport, now, schedule_due, retain_snapshot
from .platform_support import open_directory, set_windows_startup, restart_application
from .locations import migrate_data
from .widgets import protect_settings_scroll
from .storage import Store
from .worker import BrowserWorker
from .updater import RELEASES_URL, UpdateWorker
from .rental_ui import RentalPanel
from . import __version__


STYLE = """
* { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'; font-size: 14px; }
QMainWindow, QWidget#content { background: #f4f7f6; color: #243b33; }
QWidget#sidebar { background: #152b24; }
QLabel { background: transparent; color: #243b33; }
QLabel#muted { color: #5f7569; }
QLabel#title { font-size: 27px; font-weight: 700; color: #172f26; }
QLabel#brand { color: #fff; font-size: 21px; font-weight: 700; }
QLabel#brandSub { color: #a4bdb2; font-size: 12px; }
QLabel#sideNote { color: #aac1b6; font-size: 12px; }
QLabel#eyebrow { color: #289473; font-weight: 600; font-size: 11px; }
QListWidget#nav { background: transparent; border: none; color: #a9c1b5; outline: none; }
QListWidget#nav::item { height: 42px; padding-left: 18px; border-radius: 8px; margin: 2px 0; }
QListWidget#nav::item:selected { background: #29483b; color: #edfff6; font-weight: 600; border-left: 3px solid #66d6a4; }
QListWidget#nav::item:hover { background: #203d31; }
QFrame#card, QGroupBox { background: #fff; border: 1px solid #e0e9e4; border-radius: 10px; }
QFrame#hero { background: #e5f3eb; border: 1px solid #d4e9dc; border-radius: 10px; }
QLabel#heroTitle { font-size: 16px; font-weight: 600; color: #244b36; }
QLabel#metric { font-size: 30px; font-weight: 700; color: #183c2b; }
QLabel#metricSmall { font-size: 21px; font-weight: 700; color: #183c2b; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; }
QPushButton { background: #fff; border: 1px solid #d7e2dc; border-radius: 7px; padding: 9px 16px; color: #314b3d; }
QPushButton:hover { background: #eaf2ed; border-color: #b5ccbd; }
QPushButton:pressed { background: #dceadf; }
QPushButton#primary { background: #23855d; border-color: #23855d; color: #fff; font-weight: 600; }
QPushButton#primary:hover { background: #1b714f; }
QPushButton:disabled { color: #a6b5ad; background: #edf1ee; border-color: #e1e8e3; }
QPushButton#danger { color: #ac563e; }
QTableWidget { background: #fff; border: none; gridline-color: #edf1ee; selection-background-color: #eaf5ee; selection-color: #233d2d; outline: none; }
QTableWidget::item { padding: 9px; border-bottom: 1px solid #edf1ee; }
QHeaderView::section { background: #f8faf8; border: none; border-bottom: 1px solid #e5ece7; color: #708277; padding: 12px 8px; font-weight: 500; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit { background: #fff; color: #243b33; border: 1px solid #d7e2dc; border-radius: 6px; padding: 8px; min-height: 18px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus { border-color: #278d64; }
QComboBox QAbstractItemView { background: white; color: #243b33; selection-background-color: #e1f0e7; }
QCheckBox { color: #314b3d; spacing: 9px; padding: 6px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #c0d2c6; background: white; border-radius: 4px; }
QCheckBox::indicator:checked { background: #23855d; border-color: #23855d; image: none; }
QGroupBox { margin-top: 17px; padding: 23px 18px 15px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 18px; padding: 0 6px; }
QPlainTextEdit { background: #fff; color: #4b6556; border: 1px solid #e0e9e4; border-radius: 8px; padding: 10px; font-family: 'Consolas', 'Microsoft YaHei UI'; font-size: 12px; }
QProgressBar { border: none; border-radius: 3px; background: #e6eee8; max-height: 5px; }
QProgressBar::chunk { background: #2e9368; border-radius: 3px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #cbd9cf; border-radius: 4px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu { background: #fff; color: #243b33; padding: 5px; border: 1px solid #d7e2dc; }
QMenu::item { padding: 8px 22px; }
QMenu::item:selected { background: #e7f2eb; }
QToolTip { background: #233e30; color: white; border: none; padding: 6px; }
"""


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 96, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        _paint_icon(painter, pixmap.rect())
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _paint_icon(painter, rect):
    painter.save()
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 64, rect.height() / 64)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#23855d"))
    painter.drawRoundedRect(2, 2, 60, 60, 15, 15)
    from PySide6.QtGui import QPen, QPolygonF
    painter.setPen(QPen(QColor("#f2ffe9"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(17, 17, 30, 30, 45 * 16, 285 * 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#f2ffe9"))
    painter.drawPolygon(QPolygonF([QPointF(42, 11), QPointF(50, 23), QPointF(36, 24)]))
    painter.restore()


class Logo(QWidget):
    """Paint the existing mark directly at the target monitor's pixel density."""

    def __init__(self, size):
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        _paint_icon(painter, self.rect())
        painter.end()


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def button(text, handler, primary=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(handler)
    return widget


def card(name="card"):
    widget = QFrame()
    widget.setObjectName(name)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.settings = store.settings()
        self._pending_data_dir = store.root.resolve()
        self._directory_switch_pending = False
        legacy_repaired = store.clear_legacy_preflight()
        self.worker: BrowserWorker | None = None
        self.update_worker: UpdateWorker | None = None
        self.latest_release = None
        self.downloaded_update = None
        self._notified_update_tag = ""
        self._notified_rentals = set()
        self.rental_completed_uuid = ""
        self.report: RunReport | None = None
        self.auth_valid = store.auth_path.exists()
        self._quitting = False
        self._scheduled_run = False
        self._shown_tray_hint = False
        self._current_page = 1
        self.setWindowTitle("AutoDL Fresh · 实例保活与 GPU 候租")
        self.setWindowIcon(app_icon())
        self.resize(1230, 840)
        self.setMinimumSize(1080, 720)
        self.setStyleSheet(STYLE)
        self._build()
        self._setup_tray()
        self._load_history()
        self._update_status()
        if legacy_repaired:
            self._log("已修复旧版开机前定位失败留下的错误暂停，原实例列表与登录信息已保留。")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(15000)

    def _build(self):
        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(200)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 32, 20, 22)
        brand_icon = Logo(42)
        side.addWidget(brand_icon)
        side.addSpacing(14)
        side.addWidget(label("AutoDL Fresh", "brand"))
        side.addWidget(label("让每一个实例，安心留存", "brandSub"))
        side.addSpacing(24)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems(["实例总览", "执行记录", "偏好设置", "使用指南", "GPU 候租"])
        self.nav.setSpacing(3)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(lambda index: self.stack.setCurrentIndex(index))
        side.addWidget(self.nav)
        self.sidebar_update_button = button("检查更新", self._open_update_checker)
        side.addWidget(self.sidebar_update_button)
        side.addWidget(label("LOCAL DESKTOP APP", "brandSub"))
        side.addSpacing(10)
        self.side_status = label("○  尚未连接 AutoDL", "sideNote")
        side.addWidget(self.side_status)
        side.addWidget(label(f"v{__version__}  /  本机运行", "brandSub"))
        side.addWidget(label("© 2026 wanfeng562-code", "brandSub"))
        side.addWidget(label("仅限非商业使用", "brandSub"))
        side.addWidget(label("Qt / PySide6 © The Qt Company Ltd. 及贡献者", "brandSub", True))
        side.addWidget(label("LGPLv3 · 第三方许可见程序目录", "brandSub", True))
        row.addWidget(sidebar)
        content = QWidget()
        content.setObjectName("content")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 28, 30, 18)
        layout.setSpacing(16)
        self.update_banner = card("hero")
        banner_row = QHBoxLayout(self.update_banner)
        self.update_banner_text = label("", "heroTitle", True)
        banner_row.addWidget(self.update_banner_text, 1)
        banner_row.addWidget(button("查看更新", self._open_updates))
        banner_row.addWidget(button("稍后", self.update_banner.hide))
        self.update_banner.hide()
        layout.addWidget(self.update_banner)
        self.rental_success_banner = card("hero")
        success_row = QHBoxLayout(self.rental_success_banner)
        self.rental_success_text = label("", "heroTitle", True)
        success_row.addWidget(self.rental_success_text, 1)
        success_row.addWidget(button("查看租用结果", self._open_rental_result))
        success_row.addWidget(button("复制实例 ID", lambda: QApplication.clipboard().setText(self.rental_completed_uuid)))
        success_row.addWidget(button("关闭提示", self.rental_success_banner.hide))
        self.rental_success_banner.hide()
        layout.addWidget(self.rental_success_banner)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._overview())
        self.stack.addWidget(self._records())
        self.stack.addWidget(self._preferences())
        self.stack.addWidget(self._guide())
        self.rental_panel = RentalPanel(self.store)
        self.rental_panel.requested.connect(self._request_rental)
        self.rental_panel.stop_requested.connect(self._cancel)
        self.rental_panel.logged.connect(self._rental_log)
        self.rental_panel.confirmation_requested.connect(self._confirm_rental)
        self.rental_panel.task_resolved.connect(self._rental_updated)
        self.stack.addWidget(self.rental_panel)
        layout.addWidget(self.stack)
        bottom = QHBoxLayout()
        self.status_text = label("就绪 · 首先登录 AutoDL", "muted")
        bottom.addWidget(self.status_text, 1)
        bottom.addWidget(label("所有时间均为北京时间", "muted"))
        layout.addLayout(bottom)
        row.addWidget(content, 1)
        self.setCentralWidget(root)

    def _heading(self, layout, title, subtitle):
        layout.addWidget(label("AUTODL FRESH  /  WORKSPACE", "eyebrow"))
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(subtitle, "muted", True))

    def _overview(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(label("实例总览", "title"))
        heading.addWidget(label("自动检查释放时间，在需要时为实例完成一次无卡开关机。", "muted", True))
        layout.addLayout(heading)
        hero = card("hero")
        hero_row = QHBoxLayout(hero)
        hero_row.setContentsMargins(18, 12, 18, 12)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        self.hero_title = label("从连接你的 AutoDL 开始", "heroTitle")
        self.hero_sub = label("手动登录一次，保存登录态后即可检查所有实例。", "muted", True)
        copy.addWidget(self.hero_title)
        copy.addWidget(self.hero_sub)
        hero_row.addLayout(copy, 1)
        self.save_login_button = button("保存登录态", self._save_login, True)
        self.save_login_button.hide()
        hero_row.addWidget(self.save_login_button)
        self.resolve_button = button("解除暂停", self._resolve_pending)
        self.resolve_button.hide()
        hero_row.addWidget(self.resolve_button)
        self.rental_pending_button = button("查看候租", lambda: self.nav.setCurrentRow(4))
        self.rental_pending_button.hide()
        hero_row.addWidget(self.rental_pending_button)
        self.login_button = button("登录 AutoDL", self._login, True)
        hero_row.addWidget(self.login_button)
        layout.addWidget(hero)
        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metrics = []
        for title, value in [("已发现实例", "—"), ("待刷新", "—"), ("本轮刷新成功", "—"), ("每日计划", "未启用")]:
            frame = card()
            column = QVBoxLayout(frame)
            column.setContentsMargins(16, 10, 16, 10)
            column.setSpacing(2)
            column.addWidget(label(title, "muted"))
            number = label(value, "metricSmall")
            column.addWidget(number)
            metrics.addWidget(frame, 1)
            self.metrics.append(number)
        layout.addLayout(metrics)
        panel = card()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 12)
        tools = QHBoxLayout()
        tools.setContentsMargins(18, 10, 18, 8)
        tools.addWidget(label("容器实例", "cardTitle"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索名称或实例 ID")
        self.search.setMaximumWidth(190)
        self.search.textChanged.connect(self._search_changed)
        tools.addWidget(self.search)
        tools.addStretch()
        self.preview_button = button("只读检查", lambda: self._start("preview"))
        self.preview_button.setToolTip("遍历所有分页并读取状态，不执行开关机")
        self.run_button = button("检查并刷新", lambda: self._start("refresh"), True)
        self.run_all_button = button("全部刷新", lambda: self._start("refresh_all"))
        self.run_all_button.setToolTip("遍历全部网页分页，不受当前页或搜索限制。忽略剩余天数，逐个刷新已关机按量实例；无卡开机仍会计费")
        tools.addWidget(self.preview_button)
        tools.addWidget(self.run_button)
        tools.addWidget(self.run_all_button)
        panel_layout.addLayout(tools)
        self.table_stack = QStackedWidget()
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["实例 / 名称", "主机", "状态", "距离释放", "处理结果", "网页页"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in [(1, 120), (2, 96), (3, 145), (4, 174), (5, 64)]:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col, width)
        self.table_stack.addWidget(self.table)
        empty = QWidget()
        empty_column = QVBoxLayout(empty)
        empty_column.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon = Logo(48)
        empty_column.addWidget(empty_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        self.empty_title = label("这里会显示你的全部实例", "cardTitle")
        empty_column.addWidget(self.empty_title, alignment=Qt.AlignmentFlag.AlignCenter)
        self.empty_sub = label("登录后点击“只读检查”，先看看哪些实例需要刷新。", "muted")
        empty_column.addWidget(self.empty_sub, alignment=Qt.AlignmentFlag.AlignCenter)
        self.table_stack.addWidget(empty)
        self.table_stack.setCurrentIndex(1)
        panel_layout.addWidget(self.table_stack, 1)
        pagination = QWidget()
        pagination_row = QHBoxLayout(pagination)
        pagination_row.setContentsMargins(18, 8, 18, 0)
        self.page_summary = label("共 0 条", "muted")
        pagination_row.addWidget(self.page_summary)
        pagination_row.addStretch()
        self.previous_page = button("上一页", lambda: self._change_page(self._current_page - 1))
        self.next_page = button("下一页", lambda: self._change_page(self._current_page + 1))
        self.page_number = QSpinBox()
        self.page_number.setRange(1, 1)
        self.page_number.setKeyboardTracking(False)
        self.page_number.setFixedWidth(70)
        self.page_number.valueChanged.connect(self._change_page)
        self.page_total = label("/ 1 页", "muted")
        self.page_size = QComboBox()
        for size in (10, 20, 50, 100):
            self.page_size.addItem(f"{size} 条 / 页", size)
        self.page_size.setCurrentIndex(self.page_size.findData(self.settings.page_size))
        self.page_size.currentIndexChanged.connect(self._page_size_changed)
        pagination_row.addWidget(self.previous_page)
        pagination_row.addWidget(self.page_number)
        pagination_row.addWidget(self.page_total)
        pagination_row.addWidget(self.next_page)
        pagination_row.addWidget(self.page_size)
        self.previous_page.setEnabled(False)
        self.next_page.setEnabled(False)
        self._pagination_wheel_filter = protect_settings_scroll(pagination, self)
        panel_layout.addWidget(pagination)
        hint = label("全部刷新：忽略剩余天数，逐个处理已关机按量实例；两种刷新均核验关机和倒计时。", "muted")
        hint.setContentsMargins(18, 8, 10, 0)
        hint.setWordWrap(True)
        panel_layout.addWidget(hint)
        layout.addWidget(panel, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        operation = QHBoxLayout()
        self.operation_label = label("刷新条件：已关机、按量计费、距离释放不足 3 天。", "muted", True)
        operation.addWidget(self.operation_label, 1)
        self.stop_button = button("停止后续操作", self._cancel)
        self.stop_button.setObjectName("danger")
        self.stop_button.hide()
        operation.addWidget(self.stop_button)
        layout.addLayout(operation)
        return page

    def _records(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._heading(layout, "执行记录", "查看检查结果与运行日志，遇到异常可在这里定位原因。")
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(["时间", "方式", "检查实例", "刷新成功", "结果"])
        self.history_table.verticalHeader().hide()
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.setMaximumHeight(290)
        self.history_table.cellClicked.connect(self._history_selected)
        layout.addWidget(self.history_table)
        bar = QHBoxLayout()
        bar.addWidget(label("运行日志", "cardTitle"))
        bar.addStretch()
        bar.addWidget(button("打开数据目录", lambda: open_directory(self.store.root)))
        layout.addLayout(bar)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(2000)
        log_path = self.store.root / "activity.log"
        if log_path.exists():
            self.logs.setPlainText(log_path.read_text(encoding="utf-8")[-80000:])
        layout.addWidget(self.logs, 1)
        return page

    def _preferences(self):
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self._heading(outer_layout, "偏好设置", "设定检查时间与刷新阈值，让日常维护按你的节奏运行。")
        scroll = QScrollArea()
        self.settings_scroll = scroll
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        schedule = QGroupBox("每日计划")
        form = QFormLayout(schedule)
        form.setVerticalSpacing(12)
        self.schedule_check = QCheckBox("启用每日自动检查并刷新")
        self.schedule_check.setChecked(self.settings.schedule_enabled)
        form.addRow(self.schedule_check)
        self.daily_time = QTimeEdit(QTime.fromString(self.settings.daily_time, "HH:mm"))
        self.daily_time.setDisplayFormat("HH:mm")
        form.addRow("执行时间（北京时间）", self.daily_time)
        self.threshold = QSpinBox()
        self.threshold.setRange(1, 7)
        self.threshold.setValue(self.settings.threshold_days)
        self.threshold.setSuffix(" 天")
        form.addRow("距离释放不足", self.threshold)
        form.addRow(label("每天最多自动执行一轮；错过当天时间会在应用恢复运行后补检。", "muted", True))
        layout.addWidget(schedule)
        browser = QGroupBox("浏览器与状态等待")
        form = QFormLayout(browser)
        form.setVerticalSpacing(12)
        self.browser = QComboBox()
        for title, key in [("Microsoft Edge（推荐）", "msedge"), ("Google Chrome", "chrome"), ("Playwright Chromium", "chromium")]:
            self.browser.addItem(title, key)
        self.browser.setCurrentIndex(self.browser.findData(self.settings.browser_channel))
        form.addRow("使用浏览器", self.browser)
        self.browser_mode = QComboBox()
        self.browser_mode.addItem("AutoDL 专用窗口（禁用扩展、账号与同步）", "native")
        self.browser_mode.addItem("Playwright 兼容模式", "playwright")
        self.browser_mode.setCurrentIndex(self.browser_mode.findData(self.settings.browser_mode))
        form.addRow("连接方式", self.browser_mode)
        form.addRow(label("专用窗口禁止浏览器账号登录、同步及用户扩展。应用仅保存 AutoDL 登录态；手动登录完成后才接管页面。", "muted", True))
        self.browser_visibility = QComboBox()
        self.browser_visibility.addItem("后台运行（最小化专用浏览器）", "background")
        self.browser_visibility.addItem("前台显示（切换到当前操作实例）", "foreground")
        self.browser_visibility.setCurrentIndex(self.browser_visibility.findData(self.settings.browser_visibility))
        form.addRow("操作时显示方式", self.browser_visibility)
        form.addRow(label("后台启动普通浏览器后最小化，网页继续操作。实例和 GPU 型号读取始终后台；候租、刷新和核验按此选项显示。手动登录始终可见。", "muted", True))
        self.timeout = QSpinBox()
        self.timeout.setRange(30, 900)
        self.timeout.setSingleStep(30)
        self.timeout.setValue(self.settings.timeout_seconds)
        self.timeout.setSuffix(" 秒")
        form.addRow("每个状态最长等待", self.timeout)
        self.poll = QSpinBox()
        self.poll.setRange(1, 30)
        self.poll.setValue(self.settings.poll_seconds)
        self.poll.setSuffix(" 秒")
        form.addRow("状态检查间隔", self.poll)
        layout.addWidget(browser)
        desktop = QGroupBox("桌面与后台运行")
        form = QFormLayout(desktop)
        self.tray_check = QCheckBox("关闭窗口后保留在系统托盘")
        self.tray_check.setChecked(self.settings.minimize_to_tray)
        self.startup = QCheckBox("登录 Windows 后自动启动并最小化")
        self.startup.setChecked(self.settings.start_with_windows)
        self.startup.setEnabled(sys.platform == "win32")
        form.addRow(self.tray_check)
        form.addRow(self.startup)
        form.addRow(label("每日计划依赖本机开机且应用运行；电脑睡眠或退出应用期间无法检查。", "muted", True))
        layout.addWidget(desktop)
        self.update_group = QGroupBox("软件更新")
        updates = QVBoxLayout(self.update_group)
        update_actions = QHBoxLayout()
        update_actions.addWidget(label(f"当前版本：{__version__}"))
        update_actions.addStretch()
        self.update_check_button = button("检查更新", lambda: self._check_updates(manual=True))
        self.update_page_button = button("GitHub 发布页", self._open_release_page)
        update_actions.addWidget(self.update_check_button)
        update_actions.addWidget(self.update_page_button)
        updates.addLayout(update_actions)
        self.auto_update_check = QCheckBox("启动时自动检查更新")
        self.auto_update_check.setChecked(self.settings.auto_check_updates)
        updates.addWidget(self.auto_update_check)
        self.update_status_label = label("从项目公开的 GitHub Release 检查正式版，不需要登录 GitHub。", "muted", True)
        updates.addWidget(self.update_status_label)
        self.update_notes = QPlainTextEdit()
        self.update_notes.setReadOnly(True)
        self.update_notes.setMaximumHeight(150)
        self.update_notes.hide()
        updates.addWidget(self.update_notes)
        self.update_progress = QProgressBar()
        self.update_progress.hide()
        updates.addWidget(self.update_progress)
        download_actions = QHBoxLayout()
        self.update_download_button = button("下载更新", self._download_update, True)
        self.update_download_button.setEnabled(False)
        self.update_cancel_button = button("取消下载", self._cancel_update)
        self.update_cancel_button.hide()
        self.update_folder_button = button("打开下载目录", self._open_download_folder)
        self.update_folder_button.hide()
        download_actions.addWidget(self.update_download_button)
        download_actions.addWidget(self.update_cancel_button)
        download_actions.addWidget(self.update_folder_button)
        download_actions.addStretch()
        updates.addLayout(download_actions)
        updates.addWidget(label("更新包在后台下载并校验。下载后请安全退出软件，再将整个文件夹解压到程序目录；登录态和设置保留。", "muted", True))
        layout.addWidget(self.update_group)
        data = QGroupBox("数据目录")
        data_layout = QVBoxLayout(data)
        self.data_directory_field = QLineEdit(str(self.store.root.resolve()))
        self.data_directory_field.setReadOnly(True)
        self.data_directory_field.setToolTip(str(self.store.root.resolve()))
        data_layout.addWidget(self.data_directory_field)
        data_buttons = QHBoxLayout()
        data_buttons.addWidget(button("打开当前目录", lambda: open_directory(self.store.root)))
        self.choose_directory_button = button("选择新目录…", self._choose_data_directory)
        data_buttons.addWidget(self.choose_directory_button)
        data_buttons.addStretch()
        data_layout.addLayout(data_buttons)
        data_layout.addWidget(label("选择独立的空文件夹。保存后复制设置、AutoDL 登录态和记录并重启；原目录保留，浏览器缓存重新生成。", "muted", True))
        if os.environ.get("AUTODL_FRESH_DATA_DIR"):
            self.choose_directory_button.setEnabled(False)
            data_layout.addWidget(label("目录由 AUTODL_FRESH_DATA_DIR 环境变量指定，请修改该变量后重启。", "muted", True))
        layout.addWidget(data)
        layout.addWidget(label("滚轮保护：先点击控件，才能用滚轮修改数值或选项；移开鼠标后恢复为滚动页面。", "muted", True))
        layout.addStretch()
        scroll.setWidget(container)
        self._settings_wheel_filter = protect_settings_scroll(container, self)
        outer_layout.addWidget(scroll, 1)
        footer = QHBoxLayout()
        self.save_feedback = label("设置仅保存在本机。", "muted")
        footer.addWidget(self.save_feedback, 1)
        self.save_button = button("保存设置", self._save_settings, True)
        footer.addWidget(self.save_button)
        outer_layout.addLayout(footer)
        return outer

    def _guide(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._heading(layout, "轻松开始", "完成一次登录，后续检查交给 Fresh。")
        for index, title, text in [
            ("01", "连接 AutoDL", "推荐使用“AutoDL 专用窗口”：浏览器账号登录、同步和用户扩展均禁用。手动登录 AutoDL 后点击“保存登录态”，应用随后连接页面。无需登录 Microsoft / Google 浏览器账号；人机验证仍由你手动完成。"),
            ("02", "先检查，再刷新", "“只读检查”会遍历所有分页。“检查并刷新”会处理距离释放不足阈值的已关机按量计费实例。原本运行的实例会跳过。无卡模式开机会产生费用，以 AutoDL 弹窗与计费规则为准。"),
            ("03", "启用每日计划", "在偏好设置中启用每日计划并保存。建议保留 3 天缓冲。可开启 Windows 登录后自启动，关闭窗口时留在托盘。时间统一为北京时间。"),
            ("04", "处理需要关注的情况", "登录过期后重新登录；已有无卡实例在运行时暂停刷新。开机响应不确定、关机失败或倒计时未重置时，队列停止并保留记录，下次优先核验。请到网页检查待处理实例后重新运行。"),
        ]:
            frame = card()
            row = QHBoxLayout(frame)
            row.setContentsMargins(22, 18, 22, 18)
            number = label(index, "metricSmall")
            number.setFixedWidth(54)
            row.addWidget(number)
            column = QVBoxLayout()
            column.addWidget(label(title, "cardTitle"))
            column.addWidget(label(text, "muted", True))
            row.addLayout(column, 1)
            layout.addWidget(frame)
        layout.addWidget(label("登录态和日志位于本机数据目录。请勿分享 auth.json；保活不能替代重要数据备份。", "muted", True))
        layout.addStretch()
        return page

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("AutoDL Fresh · 实例保活助手")
        menu = QMenu(self)
        for text, handler in [("显示主窗口", self._show), ("只读检查", lambda: self._start("preview")),
                              ("GPU 候租", lambda: (self._show(), self.nav.setCurrentRow(4))),
                              ("检查并刷新", lambda: self._start("refresh")), ("检查更新", self._open_update_checker)]:
            action = QAction(text, self)
            action.triggered.connect(handler)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._show() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _open_updates(self):
        self.nav.setCurrentRow(2)
        self.settings_scroll.ensureWidgetVisible(self.update_group)

    def _open_update_checker(self):
        self._show()
        self._open_updates()
        self._check_updates(manual=True)

    def _open_release_page(self):
        QDesktopServices.openUrl(QUrl(self.latest_release.url if self.latest_release else RELEASES_URL))

    def check_updates_automatically(self):
        if self.settings.auto_check_updates:
            self._check_updates(manual=False)

    def _check_updates(self, *, manual):
        if self.update_worker or self._quitting or self._directory_switch_pending:
            return
        self.update_status_label.setText("正在检查 GitHub 最新正式版本…")
        self._begin_update(UpdateWorker("check", manual=manual, parent=self))

    def _begin_update(self, worker):
        self.update_worker = worker
        worker.checked.connect(self._update_checked)
        worker.downloaded.connect(self._update_downloaded)
        worker.progress.connect(self._update_download_progress)
        worker.failed.connect(self._update_failed)
        worker.cancelled.connect(self._update_cancelled)
        worker.finished.connect(self._update_finished)
        self.update_check_button.setEnabled(False)
        self.sidebar_update_button.setEnabled(False)
        self.update_download_button.setEnabled(False)
        self.choose_directory_button.setEnabled(False)
        self.update_progress.setRange(0, 0 if worker.mode == "check" else 100)
        self.update_progress.setValue(0)
        self.update_progress.show()
        worker.start()

    def _update_checked(self, result):
        release = result.release
        self.latest_release = release if result.available else None
        self.update_notes.setPlainText(release.notes)
        self.update_notes.setVisible(result.available)
        if result.available:
            suffix = "可以下载 Windows 更新包。" if release.asset else "尚未提供 Windows 更新包，可打开发布页查看。"
            self.update_status_label.setText(f"发现新版本 {release.version}（当前 {result.current}），{suffix}")
            self.update_banner_text.setText(f"发现新版本 {release.version}，可以下载更新。")
            self.update_banner.show()
            if self._notified_update_tag != release.tag:
                self._notified_update_tag = release.tag
                self.tray.showMessage("AutoDL Fresh 有新版本", f"{release.version} 已发布，可在偏好设置中下载更新。")
        else:
            message = f"当前已是最新正式版（{result.current}）。" if release.version == result.current else (
                f"当前版本 {result.current}，GitHub 最新正式版 {release.version}，无需更新。")
            self.update_status_label.setText(message)
            self.update_banner.hide()

    def _download_update(self):
        if self.update_worker or not self.latest_release or not self.latest_release.asset or self._quitting or self._directory_switch_pending:
            return
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())
        chosen, _ = QFileDialog.getSaveFileName(self, "保存 AutoDL Fresh 更新包",
                                               str(Path(folder) / self.latest_release.asset.name), "ZIP 压缩包 (*.zip)")
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() != ".zip":
            self.update_status_label.setText("请使用 .zip 扩展名保存更新包。")
            return
        self.downloaded_update = None
        self.update_folder_button.hide()
        self.update_status_label.setText("正在后台下载更新包…")
        self.update_cancel_button.setText("取消下载")
        self.update_cancel_button.setEnabled(True)
        self.update_cancel_button.show()
        self._begin_update(UpdateWorker("download", release=self.latest_release, destination=path, parent=self))

    def _update_download_progress(self, received, total):
        self.update_progress.setValue(min(100, int(received * 100 / max(total, 1))))
        self.update_progress.setFormat(f"%p% · {received / 1048576:.1f} / {total / 1048576:.1f} MB")

    def _update_downloaded(self, path):
        self.downloaded_update = Path(path)
        self.update_status_label.setText(f"更新包已下载并通过校验：{path}。请安全退出软件后解压更新。")
        self.update_folder_button.show()
        self.tray.showMessage("AutoDL Fresh 更新包已下载", "已通过完整性校验，可打开下载目录。请先安全退出软件再解压更新。")

    def _cancel_update(self):
        if self.update_worker:
            self.update_worker.cancel.set()
            self.update_cancel_button.setEnabled(False)
            self.update_cancel_button.setText("正在取消…")

    def _update_cancelled(self):
        self.update_status_label.setText("更新任务已取消，未完成的下载已清理。")

    def _update_failed(self, message):
        self.update_status_label.setText(message)
        if self.update_worker and self.update_worker.manual:
            self._log(f"软件更新：{message}")

    def _update_finished(self):
        worker = self.update_worker
        self.update_worker = None
        if worker:
            worker.deleteLater()
        self.update_progress.hide()
        self.update_cancel_button.hide()
        self.update_check_button.setEnabled(True)
        self.sidebar_update_button.setEnabled(True)
        self.update_download_button.setEnabled(bool(self.latest_release and self.latest_release.asset))
        self.choose_directory_button.setEnabled(not bool(os.environ.get("AUTODL_FRESH_DATA_DIR")))
        if self._quitting and not self.worker:
            QApplication.quit()

    def _open_download_folder(self):
        if self.downloaded_update:
            open_directory(self.downloaded_update.parent)

    def _log(self, text):
        line = f"{now():%Y-%m-%d %H:%M:%S}  {text}"
        self.logs.appendPlainText(line)
        self.store.log(line)
        self.status_text.setText(text[:110])

    def _login(self):
        self._start("login")

    def _save_login(self):
        if self.worker and self.worker.mode == "login":
            self.worker.save_login.set()
            self._log("正在确认登录状态并保存，请稍候…")

    def _request_rental(self, mode, options):
        self._start(mode, rental_options=options)

    def _confirm_rental(self, outcome, task):
        if self.worker and self.worker.mode in {"rental", "rental_verify"}:
            self.worker.rental_confirmation.request(outcome, task)
        elif not self.worker:
            self.rental_panel.confirm_result(outcome, task)

    def _start(self, mode, *, scheduled=False, rental_options=None):
        if self._quitting:
            return
        if self._directory_switch_pending:
            self._log("数据目录已切换，请重启应用后再执行操作。")
            return
        if self.worker:
            return
        if mode in {"refresh", "refresh_all", "resolve"}:
            from .rental import PENDING_PHASES
            if self.store.read("rental-task.json", {}).get("phase") in PENDING_PHASES:
                self._log("候租的创建结果尚未核验，请先到 GPU 候租页处理。")
                return
        if mode != "login" and not self.auth_valid:
            self._log("请先登录 AutoDL 并保存登录态。")
            self.nav.setCurrentRow(0)
            return
        self._scheduled_run = scheduled
        self.worker = BrowserWorker(replace(self.settings), self.store, mode, self)
        self.worker.rental_options = rental_options
        self.worker.rental_catalog.connect(self.rental_panel.catalog_ready)
        self.worker.rental_updated.connect(self._rental_updated)
        self.worker.log.connect(self._log)
        self.worker.updated.connect(self._receive_report)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.auth_expired.connect(self._auth_expired)
        self.worker.login_ready.connect(lambda: self.save_login_button.show())
        self.worker.logged_in.connect(self._logged_in)
        self.worker.finished.connect(self._worker_finished)
        self.rental_panel.set_busy(True, mode)
        for item in (self.login_button, self.preview_button, self.run_button, self.run_all_button, self.save_button, self.resolve_button):
            item.setEnabled(False)
        self.stop_button.show()
        self.stop_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.operation_label.setText("正在登录…" if mode == "login" else "正在处理 GPU 候租…"
                                     if mode.startswith("rental") else "正在检查全部分页…")
        self._log({"login": "打开手动登录窗口", "preview": "开始只读检查", "refresh": "开始检查并刷新",
                   "refresh_all": "开始全部刷新（不限剩余天数）", "resolve": "核对待处理实例是否已关机",
                   "rental_probe": "读取克隆页面 GPU 型号（不创建）", "rental": "开始 GPU 候租",
                   "rental_verify": "核验已创建实例（不重新下单）"}[mode])
        self.worker.start()

    def _rental_updated(self, task):
        self.rental_panel.show_task(task)
        self.operation_label.setText(task.get("message", "正在候租…"))
        self._update_status()
        uuid = task.get("target_uuid", "")
        if task.get("phase") == "completed" and uuid:
            self.rental_success_text.setText(("已由你确认克隆完成 · " if task.get("verification") == "manual"
                                              else "克隆已完成，数据盘已就绪 · ") + uuid)
        if task.get("manual_result") == "released" and self.rental_completed_uuid == uuid:
            self.rental_success_banner.hide()
        if task.get("phase") in {"accepted", "gpu_running", "completed"} and uuid and uuid not in self._notified_rentals:
            self._notified_rentals.add(uuid)
            self.rental_completed_uuid = uuid
            if task["phase"] != "completed":
                self.rental_success_text.setText("克隆实例已创建，正在等待完成 · " + uuid)
            self.rental_success_banner.show()
            self.tray.showMessage("克隆已完成" if task["phase"] == "completed" else "克隆实例已创建",
                                  task["message"][:240], QSystemTrayIcon.MessageIcon.Information, 15000)
            QApplication.beep()
            QApplication.alert(self, 10000)

    def _open_rental_result(self):
        self._show()
        self.nav.setCurrentRow(4)

    def _rental_log(self, message):
        self._log(message)
        self._update_status()

    def _auth_expired(self):
        self.auth_valid = False
        self._update_status()

    def _logged_in(self):
        self.auth_valid = True
        self._update_status()

    def _worker_finished(self):
        worker = self.worker
        self.worker = None
        if worker and worker.mode in {"rental", "rental_verify"}:
            from .rental import PENDING_PHASES
            task = self.store.read("rental-task.json", {})
            outcome = worker.rental_confirmation.result(task)
            if outcome and task.get("phase") in PENDING_PHASES:
                # Finish a confirmation that arrived as the worker was exiting.
                # No worker can overwrite the journal after this point.
                self.rental_panel.confirm_result(outcome, task)
        if worker:
            worker.deleteLater()
        for item in (self.login_button, self.preview_button, self.run_button, self.run_all_button, self.save_button, self.resolve_button):
            item.setEnabled(True)
        self.save_login_button.hide()
        self.stop_button.hide()
        self.progress.hide()
        self.rental_panel.set_busy(False)
        if self.report:
            self.rental_panel.set_instances(self.report.instances)
        self._update_status()
        if self._quitting and not self.update_worker:
            QApplication.quit()

    def _cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.stop_button.setEnabled(False)
            self.rental_panel.stop_button.setEnabled(False)
            self._log("已结束登录等待，普通浏览器可以继续手动使用。" if self.worker.mode == "login"
                      else "已请求停止 GPU 型号和报价读取。" if self.worker.mode == "rental_probe"
                      else "已请求停止候租；已创建的实例会继续等待克隆完成，随后关闭专用浏览器。" if self.worker.mode.startswith("rental")
                      else "已请求停止；若本应用已开机，将先完成该实例的关机与核验。")

    def _resolve_pending(self):
        pending = self.store.pending()
        if not pending or self.worker:
            return
        answer = QMessageBox.question(
            self, "人工核对待处理实例",
            f"请先在 AutoDL 网页核对实例 {pending['uuid']} 已关机，且没有正在进行的开机操作。\n\n"
            "确认后，本应用会重新读取关机状态并解除暂停，不执行开关机。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start("resolve")

    def _receive_report(self, report):
        if self.report is None and not report.instances and not report.scan_complete:
            for old in self.store.read("history.json", []):
                if old.get("instances"):
                    data = {**old, "instances": [Instance(**item) for item in old["instances"]]}
                    self.report = RunReport(**data)
                    break
        self.report = retain_snapshot(report, self.report)
        self.rental_panel.set_instances(self.report.instances)
        self._render_report()

    def _render_report(self):
        if not self.report:
            return
        report = self.report
        query = self.search.text().lower().strip()
        filtered = [item for item in report.instances if query in (item.uuid + item.name + item.region).lower()]
        count = len(filtered)
        size = self.settings.page_size
        pages = max(1, (count + size - 1) // size)
        self._current_page = min(max(1, self._current_page), pages)
        offset = (self._current_page - 1) * size
        instances = filtered[offset:offset + size]
        self.page_summary.setText(f"共 {count} 条 · {offset + 1}–{offset + len(instances)} 条" if count else "共 0 条")
        self.page_number.blockSignals(True)
        self.page_number.setRange(1, pages)
        self.page_number.setValue(self._current_page)
        self.page_number.blockSignals(False)
        self.page_total.setText(f"/ {pages} 页")
        self.previous_page.setEnabled(self._current_page > 1)
        self.next_page.setEnabled(self._current_page < pages)
        self.table.setRowCount(len(instances))
        self.table_stack.setCurrentIndex(0 if count else 1)
        if not count:
            self.empty_title.setText("没有匹配的实例" if query else "本次检查没有发现实例")
            self.empty_sub.setText("试试其他关键词。" if query else "请确认登录账号与实例列表；错误详情见执行记录。")
        for row, instance in enumerate(instances):
            outcome = report.outcomes.get(instance.uuid, instance.reason(
                self.settings.threshold_days, refresh_all=report.refresh_all))
            release = instance.release_text.replace("设置定时关机", "").replace("\n", " ").strip()
            values = [f"{instance.name or '未命名实例'}\n{instance.uuid}", instance.region,
                      instance.status + (" · 无卡" if instance.no_gpu else ""), release or "无法识别", outcome, str(instance.page)]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 4:
                    color = "#23855d" if outcome == "刷新成功" else "#ba7829" if outcome == "待刷新" else "#71847c"
                    item.setForeground(QColor(color))
                if column == 3 and outcome != "刷新成功" and instance.eligible(
                    self.settings.threshold_days, refresh_all=report.refresh_all
                ):
                    item.setForeground(QColor("#b36b35"))
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 68)
        self.metrics[0].setText(str(len(report.instances)))
        self.metrics[1].setText(str(sum(
            item.eligible(self.settings.threshold_days, refresh_all=report.refresh_all)
            and report.outcomes.get(item.uuid) != "刷新成功" for item in report.instances
        )))
        self.metrics[2].setText(str(report.successes))
        self._snapshot_hint()

    def _search_changed(self):
        self._current_page = 1
        self._render_report()

    def _change_page(self, page):
        self._current_page = page
        self._render_report()
        self.table.scrollToTop()

    def _page_size_changed(self):
        if self._directory_switch_pending:
            self.page_size.blockSignals(True)
            self.page_size.setCurrentIndex(self.page_size.findData(self.settings.page_size))
            self.page_size.blockSignals(False)
            return
        updated = replace(self.settings, page_size=self.page_size.currentData())
        try:
            self.store.save_settings(updated)
        except Exception as exc:
            self.page_size.blockSignals(True)
            self.page_size.setCurrentIndex(self.page_size.findData(self.settings.page_size))
            self.page_size.blockSignals(False)
            self._log(f"每页条数未能保存：{exc}")
            return
        self.settings = updated
        self._current_page = 1
        self._render_report()
        self.table.scrollToTop()

    def _snapshot_hint(self):
        prefix = ""
        if self.report:
            checked = datetime.fromisoformat(self.report.snapshot_at or self.report.started_at).strftime("%m-%d %H:%M")
            prefix = f"最近检查：{checked} · "
            if self.report.errors and not self.report.scan_complete:
                prefix += "本轮失败，保留上次快照 · "
        scope = "本轮全部刷新：不限剩余天数，已关机、按量计费。" if self.report and self.report.refresh_all else (
            f"刷新条件：已关机、按量计费、距离释放不足 {self.settings.threshold_days} 天。")
        self.operation_label.setText(prefix + scope)

    def _completed(self, report):
        self._receive_report(report)
        self._load_history()
        if report.errors:
            self._failed("；".join(report.errors))
        elif report.cancelled:
            self._log("本轮已停止，已接受的开机操作已优先处理。")
        else:
            mode = "只读检查" if report.preview else "全部刷新" if report.refresh_all else "检查刷新"
            self._log(f"{mode}完成：发现 {len(report.instances)} 个实例，刷新成功 {report.successes} 个。")
        if not report.preview and not report.errors and not report.cancelled:
            self.store.write("scheduler.json", {"last_attempt": now().isoformat()})

    def _failed(self, message):
        if self.worker and self.worker.mode.startswith("rental"):
            self.rental_panel.status.setText(message)
        self._log(f"需要处理：{message}")
        self.tray.showMessage("AutoDL Fresh 需要处理", message[:240], QSystemTrayIcon.MessageIcon.Warning, 15000)

    def _load_history(self):
        self.history = self.store.read("history.json", [])
        self.history_table.setRowCount(len(self.history))
        for row, report in enumerate(self.history):
            started = datetime.fromisoformat(report["started_at"])
            status = "需要处理" if report["errors"] else "已停止" if report["cancelled"] else "完成"
            successes = sum(value == "刷新成功" for value in report["outcomes"].values())
            count = str(len(report["instances"])) if report["instances"] or report.get("scan_complete") else "未完成"
            mode = "只读检查" if report["preview"] else "全部刷新" if report.get("refresh_all") else "检查刷新"
            for column, value in enumerate([started.strftime("%m-%d %H:%M"), mode, count, str(successes), status]):
                item = QTableWidgetItem(value)
                item.setToolTip("；".join(report["errors"]) or status)
                self.history_table.setItem(row, column, item)
        if self.report is None and self.history:
            self._display_history(0)

    def _display_history(self, row):
        data = dict(self.history[row])
        data["instances"] = [Instance(**item) for item in data["instances"]]
        self._receive_report(RunReport(**data))

    def _history_selected(self, row, column):
        if not self.worker:
            self._display_history(row)
            self.nav.setCurrentRow(0)
            self._log("正在显示历史快照；请重新检查以获取实时状态。")

    def _save_settings(self):
        if self.worker or self._directory_switch_pending:
            return
        if self.update_worker and self._pending_data_dir != self.store.root.resolve():
            self.save_feedback.setText("请先等待更新任务完成或取消下载，再切换数据目录。")
            return
        updated = replace(
            self.settings, schedule_enabled=self.schedule_check.isChecked(),
            daily_time=self.daily_time.time().toString("HH:mm"), threshold_days=self.threshold.value(),
            browser_channel=self.browser.currentData(),
            browser_visibility=self.browser_visibility.currentData(),
            headless=self.browser_visibility.currentData() == "background",
            browser_mode=self.browser_mode.currentData(),
            auto_check_updates=self.auto_update_check.isChecked(),
            timeout_seconds=self.timeout.value(), poll_seconds=self.poll.value(),
            minimize_to_tray=self.tray_check.isChecked(), start_with_windows=self.startup.isChecked(),
        )
        try:
            updated.validate()
            # Saving an enabled option after an upgrade must point to this exe,
            # rather than continuing to start the previous version.
            if updated.start_with_windows or updated.start_with_windows != self.settings.start_with_windows:
                set_windows_startup(updated.start_with_windows)
            self.store.save_settings(updated)
            self.settings = updated
            self.save_feedback.setText("已保存 · " + now().strftime("%H:%M:%S"))
            self._log("偏好设置已保存。")
            self._update_status()
            self._render_report()
            if self._pending_data_dir != self.store.root.resolve():
                self.timer.stop()
                try:
                    target = migrate_data(self.store.root, self._pending_data_dir)
                except Exception:
                    self.timer.start(15000)
                    raise
                self._directory_switch_pending = True
                self.save_feedback.setText(f"数据已复制到 {target}，正在重启…")
                self._restart_after_migration()
        except Exception as exc:
            self.save_feedback.setText(str(exc))

    def _choose_data_directory(self):
        if self.worker or self.update_worker or self._directory_switch_pending:
            return
        selected = QFileDialog.getExistingDirectory(self, "选择新的空数据目录", str(self.store.root.parent))
        if selected:
            self._pending_data_dir = Path(selected).resolve()
            self.data_directory_field.setText(str(self._pending_data_dir))
            self.data_directory_field.setToolTip(str(self._pending_data_dir))
            changed = self._pending_data_dir != self.store.root.resolve()
            self.save_button.setText("保存并重启" if changed else "保存设置")
            self.save_feedback.setText("将在保存时复制应用数据并重启。" if changed else "设置仅保存在本机。")

    def _restart_after_migration(self):
        try:
            restart_application()
        except Exception as exc:
            self.save_feedback.setText(f"新目录已保存，请手动退出并重启应用。{exc}")
            return
        self._quitting = True
        QApplication.quit()

    def _update_status(self):
        pending = self.store.pending()
        from .rental import PENDING_PHASES
        rental = self.store.read("rental-task.json", {})
        rental_pending = rental.get("phase") in PENDING_PHASES
        self.rental_pending_button.setVisible(rental_pending)
        self.resolve_button.setVisible(bool(pending))
        if pending:
            self.hero_title.setText("有一个实例需要优先核验")
            self.hero_sub.setText(f"{pending['uuid']} · 点击“检查并刷新”继续核验。请勿重复手动开机。")
        elif rental_pending:
            self.hero_title.setText("有一次 GPU 候租创建需要核验")
            self.hero_sub.setText("点击“查看候租”自动核验，或手动确认克隆完成 / 实例已释放，即可结束本次记录。")
        elif self.auth_valid:
            self.hero_title.setText("登录态已保存，可以开始检查")
            self.hero_sub.setText("只读检查可预览全部实例；启用每日计划后将按设定时间自动处理。")
        else:
            self.hero_title.setText("从连接你的 AutoDL 开始")
            self.hero_sub.setText("手动登录一次，保存登录态后即可检查所有实例。")
        self.side_status.setText("●  登录态已保存" if self.auth_valid else "○  需要登录 AutoDL")
        self.login_button.setText("重新登录" if self.auth_valid else "登录 AutoDL")
        self.metrics[3].setText(self.settings.daily_time if self.settings.schedule_enabled else "未启用")
        self._snapshot_hint()

    def _tick(self):
        if self._quitting or not self.auth_valid or self._directory_switch_pending:
            return
        state = self.store.read("scheduler.json", {})
        if self.worker:
            return
        if schedule_due(self.settings, state.get("last_attempt", ""), now()):
            # Record attempt before dispatch to avoid an error/relogin loop every 15 seconds.
            self.store.write("scheduler.json", {"last_attempt": now().isoformat()})
            self._start("refresh", scheduled=True)
            return

    def _quit(self):
        self._quitting = True
        self.timer.stop()
        if self.update_worker:
            self._cancel_update()
        if self.worker:
            self._cancel()
            self._show()
            self._log("正在安全结束任务，完成已提交操作的核验后退出。")
        elif self.update_worker:
            self._log("正在结束更新任务，完成下载清理后退出。")
        else:
            QApplication.quit()

    def closeEvent(self, event):
        if self.settings.minimize_to_tray and self.tray.isVisible() and not self._quitting:
            event.ignore()
            self.hide()
            if not self._shown_tray_hint:
                self.tray.showMessage("AutoDL Fresh", "应用仍在托盘运行，每日计划继续生效。可从托盘菜单退出。")
                self._shown_tray_hint = True
        elif self.worker or self.update_worker:
            event.ignore()
            self._quit()
        else:
            event.accept()
