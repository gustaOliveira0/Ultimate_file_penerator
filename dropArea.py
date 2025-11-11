
from PySide6.QtWidgets import QLabel
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtCore import Qt


class DropArea(QLabel):
    def __init__(self):
        super().__init__("Arraste uma imagem aqui…")
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setStyleSheet("border: 2px dashed #888; border-radius: 12px; padding: 24px;")

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e: QDropEvent):
        if e.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in e.mimeData().urls()]
            if paths:
                win = self.window()
                if hasattr(win, 'analyze_path'):
                    win.analyze_path(paths[0])

