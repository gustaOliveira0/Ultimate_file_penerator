from pathlib import Path
import json
from typing import Any, Dict
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QFileDialog,
    QMessageBox,
    QMenuBar,
    QDialog
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTabWidget,QLineEdit,
    QMessageBox, QDialog
)


from PySide6.QtGui import QAction

from PySide6.QtCore import Qt
from dropArea import DropArea
from hierarchyTab import HierarchyTab
from peneratorTab import PeneratorTab

def _choose_open_file(parent, title="Abrir arquivo", name_filter=""):
    dlg = QFileDialog(parent, title)
    dlg.setOption(QFileDialog.DontUseNativeDialog, True)
    if name_filter:
        dlg.setNameFilter(name_filter)
    dlg.setFileMode(QFileDialog.ExistingFile)
    if dlg.exec() == QDialog.Accepted:
        files = dlg.selectedFiles()
        return (files[0], name_filter) if files else ("", name_filter)
    return ("", name_filter)

def _choose_save_file(parent, title="Salvar como", name_filter=""):
    dlg = QFileDialog(parent, title)
    dlg.setOption(QFileDialog.DontUseNativeDialog, True)
    dlg.setAcceptMode(QFileDialog.AcceptSave)
    if name_filter:
        dlg.setNameFilter(name_filter)
    if dlg.exec() == QDialog.Accepted:
        files = dlg.selectedFiles()
        return (files[0], name_filter) if files else ("", name_filter)
    return ("", name_filter)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ultimate File Penerator")
        self.resize(1200, 900)

        # Abas
        self.tabs = QTabWidget()
        self.tab_meta = QWidget()
        self.tab_hier = HierarchyTab()
        self.tab_penerator = PeneratorTab()
        self.tabs.addTab(self.tab_meta, "Metadados")
        self.tabs.addTab(self.tab_hier, "Hierarquia")
        self.tabs.addTab(self.tab_penerator, "File Penerator")
        self.setCentralWidget(self.tabs)

        # --- Menu ---
        menubar = self.menuBar() if hasattr(self, 'menuBar') else QMenuBar(self)
        self.setMenuBar(menubar)
        m_arquivo = menubar.addMenu("Arquivo")
        m_ferr = menubar.addMenu("Ferramentas")

        act_exit = QAction("Sair", self)
        act_exit.triggered.connect(self.close)
        m_arquivo.addAction(act_exit)

        act_meta = QAction("Metadados de Imagem", self)
        act_meta.triggered.connect(lambda: self.tabs.setCurrentWidget(self.tab_meta))
        m_ferr.addAction(act_meta)

        act_hier = QAction("Criar Hierarquia de Pastas…", self)
        act_hier.triggered.connect(lambda: self.tabs.setCurrentWidget(self.tab_hier))
        m_ferr.addAction(act_hier)

        act_pen = QAction("File Penerator", self)
        act_pen.triggered.connect(lambda: self.tabs.setCurrentWidget(self.tab_penerator))
        m_ferr.addAction(act_pen)

        # --- Conteúdo da aba Metadados ---
        v = QVBoxLayout(self.tab_meta)
        self.drop = DropArea()
        v.addWidget(self.drop)

        h = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("/caminho/absoluto/para/imagem.jpg")
        btn_browse = QPushButton("Abrir…")
        btn_browse.clicked.connect(self.open_file_dialog)
        btn_analyze = QPushButton("Analisar caminho")
        btn_analyze.clicked.connect(self.analyze_from_input)
        h.addWidget(self.path_edit)
        h.addWidget(btn_browse)
        h.addWidget(btn_analyze)
        v.addLayout(h)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setPlaceholderText("JSON aparecerá aqui…")
        v.addWidget(self.out)

        h2 = QHBoxLayout()
        btn_save = QPushButton("Salvar JSON…")
        btn_save.clicked.connect(self.save_json)
        h2.addStretch(1)
        h2.addWidget(btn_save)
        v.addLayout(h2)

        # Atalho copiar JSON
        copy_action = QAction("Copiar JSON", self)
        copy_action.triggered.connect(self.copy_json)
        self.addAction(copy_action)

    # --- ações Metadados ---
    def json_dumps_sane(self, obj: Dict[str, Any]) -> str:
        try:
            return json.dumps(to_jsonable(obj), ensure_ascii=False, indent=2)
        except TypeError:
            return json.dumps(json.loads(json.dumps(str(obj))), ensure_ascii=False, indent=2)

    def set_json(self, obj: Dict[str, Any]):
        self.out.setPlainText(self.json_dumps_sane(obj))

    def open_file_dialog(self):
        fn, _ = _choose_open_file(self, "Selecionar imagem", "Imagens (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp)")
        if fn:
            self.path_edit.setText(fn)
            self.analyze_path(fn)

    def analyze_from_input(self):
        p = self.path_edit.text().strip()
        if p:
            self.analyze_path(p)

    def analyze_path(self, path: str):
        try:
            p = Path(path)
            if not p.exists() or not p.is_file():
                raise FileNotFoundError("Arquivo não encontrado")
            img = open_any_image(p)
            meta = image_metadata_dict(img)
            self.set_json({"arquivo": str(p), **meta})
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao processar: {e}")

    def save_json(self):
        try:
            text = self.out.toPlainText()
            if not text.strip():
                QMessageBox.information(self, "Salvar JSON", "Nada para salvar.")
                return
            fn, _ = _choose_save_file(self, "Salvar JSON", "JSON (*.json)")
            if fn:
                Path(fn).write_text(text, encoding="utf-8")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao salvar: {e}")

    def copy_json(self):
        text = self.out.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def keyPressEvent(self, event):
        # Ctrl/Cmd+S para salvar
        if (event.modifiers() & Qt.ControlModifier) and event.key() == Qt.Key_S:
            self.save_json()
        else:
            super().keyPressEvent(event)

