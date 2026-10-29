from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from .rental import GPU_COUNTS, PENDING_PHASES, RentalOptions, confirm_rental_result, money
from .quote_cache import QuoteCache
from .widgets import protect_settings_scroll


def text(value, name=None):
    label = QLabel(value)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        label.setObjectName(name)
    return label


class RentalPanel(QWidget):
    requested = Signal(str, object)
    stop_requested = Signal()
    logged = Signal(str)
    confirmation_requested = Signal(str, object)
    task_resolved = Signal(object)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.quote_cache = QuoteCache(store)
        self.busy = False
        self.mode = ""
        self._confirmation_pending = False
        self._last_task_text = None
        self.catalog_source = ""
        self.saved = store.read("rental-options.json", {})
        self.instances = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(text("AUTODL FRESH  /  WORKSPACE", "eyebrow"))
        outer.addWidget(text("GPU 候租", "title"))
        outer.addWidget(text("等待同地区的空闲 GPU，找到后克隆系统盘和数据盘，创建并开机一台新实例。", "muted"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        self.config = QGroupBox("源实例与租用条件")
        form = QFormLayout(self.config)
        form.setVerticalSpacing(12)
        sources = QHBoxLayout()
        self.source = QComboBox()
        self.source.setMinimumWidth(240)
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source.currentIndexChanged.connect(self._source_changed)
        sources.addWidget(self.source, 1)
        self.read_button = QPushButton("读取实例")
        self.read_button.clicked.connect(lambda: self.requested.emit("preview", None))
        sources.addWidget(self.read_button)
        form.addRow("已关机源实例", sources)
        self.source_hint = text("先读取实例，再选择需要克隆的源实例。", "muted")
        form.addRow(self.source_hint)
        self.probe_button = QPushButton("加载缓存型号与报价")
        self.probe_button.clicked.connect(self._probe)
        probes = QHBoxLayout()
        probes.addWidget(self.probe_button, 1)
        self.requote_button = QPushButton("重新核对报价")
        self.requote_button.clicked.connect(lambda: self._probe(force=True))
        probes.addWidget(self.requote_button)
        form.addRow(probes)
        self.quote_note = text("型号与参考报价直接从 JSON 缓存加载，不会自动核对。完整核对通常至少需要 2 分钟，只有点击“重新核对报价”才联网逐项读取；候租仍实时检查库存。", "muted")
        form.addRow(self.quote_note)
        self.quote_hint = text("随软件提供已核对的地区报价；选择源实例后自动加载该地区缓存。", "muted")
        form.addRow(self.quote_hint)
        self.gpus = QListWidget()
        self.gpus.setMinimumHeight(145)
        self.gpus.setMaximumHeight(210)
        form.addRow("可接受的 GPU\n从上到下为优先顺序", self.gpus)
        order = QHBoxLayout()
        for caption, delta in (("上移", -1), ("下移", 1)):
            button = QPushButton(caption)
            button.clicked.connect(lambda checked=False, d=delta: self._move(d))
            order.addWidget(button)
        self.same_button = QPushButton("仅原型号")
        self.same_button.clicked.connect(self._same_gpu)
        order.addWidget(self.same_button)
        order.addStretch()
        form.addRow(order)
        self.count = QComboBox()
        for count in GPU_COUNTS:
            self.count.addItem(f"{count} 卡", count)
        self.count.setCurrentIndex(max(0, self.count.findData(self.saved.get("gpu_count", 1))))
        form.addRow("租用卡数", self.count)
        form.addRow(text("型号后显示网页读取的每卡每小时参考报价，实际按所选主机报价计费；扩容数据盘另按网页日价计费。", "muted"))
        self.interval = QSpinBox()
        self.interval.setRange(1, 3600)
        self.interval.setSuffix(" 秒")
        self.interval.setValue(self.saved.get("interval_seconds", 1))
        form.addRow("刷新间隔", self.interval)
        self.duration = QSpinBox()
        self.duration.setRange(1, 1440)
        self.duration.setSuffix(" 分钟")
        self.duration.setValue(self.saved.get("max_minutes", 120))
        form.addRow("最长候租时间", self.duration)
        self.consent = QCheckBox("确认按条件自动租用；新实例持续计费，由我使用后关机")
        form.addRow(self.consent)
        form.addRow(text("每次只创建一台；源实例保留。候租由你手动启动，重启应用不会自动恢复下单。", "muted"))
        layout.addWidget(self.config)
        actions = QHBoxLayout()
        self.start_button = QPushButton("开始候租并自动开机")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._start)
        actions.addWidget(self.start_button)
        self.stop_button = QPushButton("停止候租")
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.stop_button.setEnabled(False)
        actions.addWidget(self.stop_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = text("尚未启动候租", "heroTitle")
        layout.addWidget(self.status)
        recovery = QHBoxLayout()
        self.verify_button = QPushButton("自动核验已创建实例")
        self.verify_button.clicked.connect(lambda: self.requested.emit("rental_verify", None))
        recovery.addWidget(self.verify_button)
        self.complete_button = QPushButton("确认克隆已完成")
        self.complete_button.clicked.connect(lambda: self._confirm("completed"))
        recovery.addWidget(self.complete_button)
        self.resolve_button = QPushButton("确认实例已释放")
        self.resolve_button.clicked.connect(self._resolve)
        recovery.addWidget(self.resolve_button)
        recovery.addStretch()
        layout.addLayout(recovery)
        self.recovery_hint = text("已在网页确认克隆完成，或实例已释放 / 未创建时，可直接手动确认，无需等待自动核验。"
                                  "确认后结束本次记录并关闭正在核验的专用浏览器；重新候租需手动开始。", "muted")
        layout.addWidget(self.recovery_hint)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(300)
        self.details.setMinimumHeight(100)
        layout.addWidget(self.details)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.wheel_filter = protect_settings_scroll(content)
        self.show_task(store.read("rental-task.json", {}), startup=True)

    def set_instances(self, instances):
        if self.busy:
            return
        self.instances = [x for x in instances if x.status == "已关机" and not x.blocked_reason]
        old = self.source.currentData() or self.saved.get("source_uuid")
        self.source.blockSignals(True)
        self.source.clear()
        for item in self.instances:
            self.source.addItem(f"{item.name or item.uuid} · {item.region}", item.uuid)
        index = self.source.findData(old)
        self.source.setCurrentIndex(max(0, index))
        self.source.blockSignals(False)
        if self.catalog_source != self.source.currentData():
            self._source_changed()

    def _source_changed(self):
        source = next((x for x in self.instances if x.uuid == self.source.currentData()), None)
        self.catalog_source = ""
        self.gpus.clear()
        self.consent.setChecked(False)
        if source:
            self.source_hint.setText(f"{source.uuid} · {source.region} · {source.gpu_name or '型号待读取'} × {source.gpu_count or '?'} 卡")
            count = (self.saved.get("gpu_count", source.gpu_count)
                     if self.saved.get("source_uuid") == source.uuid else source.gpu_count)
            if count in GPU_COUNTS:
                self.count.setCurrentIndex(self.count.findData(count))
            cached = self.quote_cache.get(source)
            if cached:
                self.catalog_ready(cached)
            else:
                self.quote_hint.setText("该地区暂无缓存；如需读取型号和报价，请手动点击“重新核对报价”（通常至少 2 分钟）。")

    def _probe(self, force=False):
        if not self.source.currentData():
            self.status.setText("请先读取并选择源实例")
            return
        source = next(x for x in self.instances if x.uuid == self.source.currentData())
        cache = self.quote_cache
        cached = cache.get(source)
        if cached and not force:
            self.catalog_ready(cached)
            return
        if not force:
            self.status.setText("该地区暂无缓存，请手动点击“重新核对报价”；完整读取通常至少需要 2 分钟。")
            return
        self.requested.emit("rental_probe", RentalOptions(source_uuid=self.source.currentData()))

    def catalog_ready(self, data):
        source = data["source"]
        if source["uuid"] != self.source.currentData():
            return
        preserve = self.catalog_source == source["uuid"] and self.gpus.count() > 0
        current_order = [self.gpus.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.gpus.count())]
        current_selected = self.options().gpu_names if preserve else []
        self.catalog_source = source["uuid"]
        for item in self.instances:
            if item.uuid == source["uuid"]:
                item.gpu_name, item.gpu_count = source["gpu_name"], source["gpu_count"]
        self.source_hint.setText(f"{source['uuid']} · {source['region']} · {source['gpu_name']} × {source['gpu_count']} 卡")
        same_saved_source = self.saved.get("source_uuid") == self.catalog_source
        selected = (current_selected if preserve else
                    self.saved.get("gpu_names", []) if same_saved_source else [source["gpu_name"]])
        self.gpus.clear()
        names = [n for n in (current_order if preserve else selected) if n in data["gpu_names"]]
        names += [n for n in data["gpu_names"] if n not in names]
        for name in names:
            price = data.get("gpu_prices", {}).get(name)
            suffix = data.get("price_errors", {}).get(name, "报价待读取")
            if price:
                low, high = money(price["min"]), money(price["max"])
                suffix = f"¥{low:.2f} / 卡 / 时" if low == high else f"¥{low:.2f}–{high:.2f} / 卡 / 时"
                if name in data.get("price_errors", {}):
                    suffix += "（上次报价）"
            item = QListWidgetItem(f"{name}  ·  {suffix}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(f"{name} · 已读取主机的参考报价；实际价格以租用时网页为准。")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked)
            self.gpus.addItem(item)
        if not preserve and not same_saved_source and source["gpu_count"] in GPU_COUNTS:
            self.count.setCurrentIndex(self.count.findData(source["gpu_count"]))
        checked = data.get("checked_at", "").replace("T", " ")[:16]
        origin = "随软件提供的报价" if data.get("quote_origin") == "bundled" else "本机缓存报价"
        self.quote_hint.setText(origin + " · " + ("地区报价上次核对：" + checked if checked else "地区报价尚未完成整轮核对")
                               + "。实际费用以创建时网页报价为准。")
        if not data.get("background"):
            self.status.setText(data.get("progress") or ("已加载本地区缓存报价，可点击“重新核对报价”立即更新。"
                                if data.get("from_cache") else "已读取本地区型号及参考报价，确认后可开始候租。"))

    def _same_gpu(self):
        source = next((x for x in self.instances if x.uuid == self.source.currentData()), None)
        for i in range(self.gpus.count()):
            item = self.gpus.item(i)
            item.setCheckState(Qt.CheckState.Checked if source and item.data(Qt.ItemDataRole.UserRole) == source.gpu_name
                               else Qt.CheckState.Unchecked)

    def _move(self, delta):
        row = self.gpus.currentRow()
        if 0 <= row + delta < self.gpus.count():
            item = self.gpus.takeItem(row)
            self.gpus.insertItem(row + delta, item)
            self.gpus.setCurrentRow(row + delta)

    def options(self):
        return RentalOptions(
            source_uuid=self.source.currentData() or "",
            gpu_names=[self.gpus.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.gpus.count())
                       if self.gpus.item(i).checkState() == Qt.CheckState.Checked],
            gpu_count=self.count.currentData(), interval_seconds=self.interval.value(),
            max_minutes=self.duration.value(),
        )

    def _start(self):
        try:
            if self.busy or self.catalog_source != self.source.currentData():
                raise ValueError("请先加载本地区缓存；暂无缓存时请手动点击“重新核对报价”")
            if self.store.read("rental-task.json", {}).get("phase") in PENDING_PHASES:
                raise ValueError("上次创建尚未确认，请自动核验或手动确认克隆完成 / 实例已释放")
            options = self.options()
            options.validate()
            if not self.consent.isChecked():
                raise ValueError("请先确认按条件自动租用及后续计费")
            self.saved = asdict(options)
            self.store.write("rental-options.json", self.saved)
            self.requested.emit("rental", options)
        except (ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))

    def set_busy(self, busy, mode=""):
        self.busy = busy
        if mode or not busy:
            self.mode = mode
        self.config.setEnabled(not busy)
        self.stop_button.setEnabled(busy and self.mode in {"rental", "rental_probe"})
        self.stop_button.setText("停止读取" if busy and self.mode == "rental_probe" else "停止候租")
        task = self.store.read("rental-task.json", {})
        pending = task.get("phase") in PENDING_PHASES
        target = bool(task.get("target_uuid"))
        self.start_button.setEnabled(not busy and not pending)
        self.verify_button.setEnabled(not busy and pending and target)
        can_confirm = pending and not self._confirmation_pending and (
            not busy or self.mode in {"rental", "rental_verify"} and target)
        self.complete_button.setEnabled(can_confirm and target)
        self.resolve_button.setEnabled(can_confirm)
        self.resolve_button.setText("确认实例已释放" if target else "确认未创建实例")
        self.recovery_hint.setVisible(pending)

    def show_task(self, task, *, startup=False):
        if task.get("phase") not in PENDING_PHASES:
            self._confirmation_pending = False
        if task.get("verification") == "manual":
            self.consent.setChecked(False)
        if task:
            message = task.get("message", "候租状态已保存")
            if startup and task.get("phase") == "monitoring":
                message = "上次候租已中断；重新开始后才会继续检查和租用。"
            self.status.setText(message)
            signature = (message, task.get("target_uuid", ""))
            if signature != self._last_task_text:
                self.details.appendPlainText(message)
                if task.get("target_uuid"):
                    self.details.appendPlainText("新实例：" + task["target_uuid"])
                self._last_task_text = signature
        self.set_busy(self.busy)

    def _resolve(self):
        task = self.store.read("rental-task.json", {})
        self._confirm("released" if task.get("target_uuid") else "not_created")

    def _confirm(self, outcome):
        task = self.store.read("rental-task.json", {})
        if task.get("phase") not in PENDING_PHASES or self._confirmation_pending:
            return
        if self.busy and (self.mode not in {"rental", "rental_verify"} or not task.get("target_uuid")):
            return
        uuid = task.get("target_uuid", "")
        if outcome == "completed" and not uuid:
            return
        title, description = {
            "completed": ("确认克隆已完成", "我已在 AutoDL 网页确认克隆和数据盘拷贝已完成，并接管此实例。"),
            "released": ("确认实例已释放", "我已在 AutoDL 网页释放此实例，无需继续核验。"),
            "not_created": ("确认未创建实例", "我已在 AutoDL 网页确认本次未创建实例，也没有排队或创建中的实例。"),
        }[outcome]
        result = QMessageBox.question(
            self, title, (f"新实例：{uuid}\n\n" if uuid else "") + description + "\n\n"
            "确认后结束本次记录，停止等待并关闭本次核验的专用浏览器。不会自动重新下单。是否确认？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        current = self.store.read("rental-task.json", {})
        if (current.get("phase") not in PENDING_PHASES or current.get("target_uuid") != uuid
                or current.get("started_at") != task.get("started_at")):
            self.show_task(current)
            return
        if self.busy:
            self._confirmation_pending = True
            self.set_busy(True)
            self.status.setText("已收到人工确认，正在结束核验并关闭专用浏览器…")
            self.confirmation_requested.emit(outcome, task)
            return
        self.confirm_result(outcome, task)

    def confirm_result(self, outcome, task):
        # The worker must own writes while active. Idle/restart recovery needs
        # neither a login nor a browser, and never starts a replacement order.
        result = confirm_rental_result(self.store, outcome, expected=task)
        self._confirmation_pending = False
        self.show_task(result)
        self.task_resolved.emit(result)
        self.logged.emit(result.get("message", "人工确认已结束"))
