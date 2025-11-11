
import sys
from dropArea import DropArea
from hierarchyTab import HierarchyTab
from peneratorTab import PeneratorTab
from dialogNormalizer import _DialogNormalizer
from mainWindow import MainWindow
from PySide6.QtWidgets import (
    QApplication
)
def main():
    app = QApplication(sys.argv)
    normalizer = _DialogNormalizer(500, 300)
    app.installEventFilter(normalizer)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()