from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from autodl_fresh.widgets import protect_settings_scroll


def wheel(app, widget, delta=-120):
    point = widget.rect().center()
    event = QWheelEvent(QPointF(point), QPointF(widget.mapToGlobal(point)), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(widget, event)
    app.processEvents()


def click_to_arm(app, widget):
    QTest.mouseMove(widget, widget.rect().center())
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())
    if isinstance(widget, QComboBox):
        QTest.keyClick(widget.view(), Qt.Key.Key_Escape)
        widget.window().activateWindow()
        widget.setFocus()
    app.processEvents()


def test_page_wheel_does_not_change_controls_until_clicked():
    app = QApplication.instance() or QApplication([])
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    combo = QComboBox()
    combo.addItems(["first", "second", "third"])
    spin = QSpinBox()
    spin.setRange(0, 20)
    spin.setValue(10)
    layout.addWidget(combo)
    layout.addWidget(spin)
    layout.addStretch()
    content.setMinimumHeight(1800)
    scroll.setWidget(content)
    scroll.resize(400, 300)
    protection = protect_settings_scroll(content)
    scroll.show()
    app.processEvents()
    try:
        wheel(app, combo)
        assert combo.currentIndex() == 0
        assert scroll.verticalScrollBar().value() > 0
        scroll.verticalScrollBar().setValue(0)
        click_to_arm(app, spin.lineEdit())
        wheel(app, spin)
        assert spin.value() == 9
        app.sendEvent(spin, QEvent(QEvent.Type.Leave))
        wheel(app, spin)
        assert spin.value() == 9
        assert scroll.verticalScrollBar().value() > 0
        scroll.verticalScrollBar().setValue(0)
        click_to_arm(app, combo)
        wheel(app, combo)
        assert combo.currentIndex() == 1
        app.sendEvent(combo, QEvent(QEvent.Type.Leave))
        wheel(app, combo)
        assert combo.currentIndex() == 1
        assert protection.parent() is content
    finally:
        scroll.close()
        app.processEvents()
