from PySide6.QtCore import Qt, QTimer, QObject, QEvent, QPoint, QRect
from PySide6.QtWidgets import QDialog


class _DialogNormalizer(QObject):
    def __init__(self, width=500, height=300):
        super().__init__()
        self.w = width
        self.h = height

    def eventFilter(self, obj, ev):
        # Vale para QUALQUER QDialog (QMessageBox, QFileDialog, QInputDialog, QProgressDialog, etc.)
        if isinstance(obj, QDialog) and ev.type() in (QEvent.Show, QEvent.ShowToParent):
            def apply():
                # tamanho fixo
                try:
                    obj.setFixedSize(self.w, self.h)
                except Exception:
                    pass
                # centralizar
                try:
                    parent = obj.parent()
                    win = parent.window() if parent else None
                    screen = (win.screen() if (win and win.isVisible()) else QApplication.primaryScreen())
                    geo = screen.availableGeometry() if screen else None
                    if geo:
                        fg = obj.frameGeometry()
                        fg.setWidth(self.w)
                        fg.setHeight(self.h)
                        fg.moveCenter(geo.center())
                        obj.move(fg.topLeft())
                except Exception:
                    pass
            QTimer.singleShot(0, apply)
        return super().eventFilter(obj, ev)
