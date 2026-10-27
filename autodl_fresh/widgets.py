from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtCore import QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QComboBox, QScrollArea, QWidget


class IntentionalWheelFilter(QObject):
    """Wheel edits require a click; passing over controls keeps scrolling the page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controls = {}
        self._armed = None

    def install(self, widget: QWidget):
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._controls[widget] = widget
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            self._controls[child] = widget
            child.installEventFilter(self)

    def eventFilter(self, watched, event):
        control = self._controls.get(watched)
        if control is None:
            return False
        if event.type() == QEvent.Type.MouseButtonPress:
            self._armed = control
        elif event.type() == QEvent.Type.Leave and watched is control:
            if not (isinstance(control, QComboBox) and control.view().isVisible()):
                self._armed = None
        elif event.type() == QEvent.Type.FocusOut and event.reason() in {
            Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
        }:
            self._armed = None
        elif event.type() == QEvent.Type.Wheel:
            popup_open = isinstance(control, QComboBox) and control.view().isVisible()
            if not popup_open and self._armed is not control:
                ancestor = control.parentWidget()
                while ancestor is not None and not isinstance(ancestor, QScrollArea):
                    ancestor = ancestor.parentWidget()
                if ancestor is not None:
                    viewport = ancestor.viewport()
                    forwarded = QWheelEvent(QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                                            event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                            event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                                            event.source())
                    QApplication.sendEvent(viewport, forwarded)
                event.ignore()
                return True
        return False


def protect_settings_scroll(root: QWidget, parent=None) -> IntentionalWheelFilter:
    filter_ = IntentionalWheelFilter(parent or root)
    for widget in root.findChildren(QWidget):
        if isinstance(widget, (QComboBox, QAbstractSpinBox)):
            filter_.install(widget)
    return filter_
