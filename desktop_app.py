"""
Image Metadata Desktop App (PySide6)
-----------------------------------

• App desktop (Windows/macOS/Linux) com 2 ferramentas em abas:
  1) Metadados: drag & drop, abrir arquivo, caminho absoluto → JSON seguro.
  2) Hierarquia (visual): editor de árvore + gerenciador de **templates de hierarquia**:
     - Criar template por texto (uma linha por caminho; '/' separa níveis)
     - Salvar/Carregar templates em JSON
     - Aplicar template em múltiplas pastas selecionadas (sem duplicar nós)
     - Atalhos rápidos (3k/4k com jpg/png)

Instalação (cobertura ampla de formatos)
  pip install -U PySide6 pillow pillow-heif pillow-avif-plugin pillow-jxl-plugin \
      rawpy imagecodecs opencv-python imageio pyvips cairosvg Wand pdf2image

Executar
  python desktop_app.py

Empacotar (ex.: Windows)
  pip install pyinstaller
  pyinstaller --noconfirm --onefile --windowed --name ImgMetaApp desktop_app.py
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional, List
import os
import sys

from PIL import Image, ImageCms
from PIL import ImageFile
from PIL.ExifTags import TAGS, GPSTAGS
from PIL.TiffImagePlugin import IFDRational

# Tolerar arquivos parcialmente truncados/maiores
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Plugins opcionais (não falhar se ausentes)
try:
    from PIL.HeifImagePlugin import register_heif_opener  # HEIC/HEIF
    register_heif_opener()
except Exception:
    pass
try:
    import pillow_avif  # AVIF
except Exception:
    pass
try:
    import pillow_jxl  # JPEG XL
except Exception:
    pass

# ------------------ Utilitários (metadados) ------------------

def rational_to_float(v: Any):
    try:
        if isinstance(v, IFDRational):
            return float(v)
        if isinstance(v, tuple) and len(v) == 2 and v[1] not in (0, None):
            return v[0] / v[1]
    except Exception:
        return None
    try:
        return float(v)
    except Exception:
        return v


def dms_to_decimal(dms, ref: Optional[str]):
    try:
        d = rational_to_float(dms[0])
        m = rational_to_float(dms[1])
        s = rational_to_float(dms[2])
        if None in (d, m, s):
            return None
        sign = -1 if ref in ("S", "W") else 1
        return sign * (d + m/60 + s/3600)
    except Exception:
        return None


def to_jsonable(obj: Any) -> Any:
    """Converte qualquer estrutura para algo serializável por JSON."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, IFDRational):
        try:
            return float(obj)
        except Exception:
            return str(obj)
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    if isinstance(obj, tuple):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, list):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    return str(obj)


def parse_exif(img: Image.Image) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    try:
        exif = img.getexif()
        if not exif:
            return data
        for k, v in exif.items():
            tag = TAGS.get(k, k)
            data[tag] = v
        gps_raw = data.get("GPSInfo")
        if isinstance(gps_raw, dict):
            gps: Dict[str, Any] = {}
            for gk, gv in gps_raw.items():
                gps[GPSTAGS.get(gk, gk)] = gv
            lat = gps.get("GPSLatitude")
            lat_ref = gps.get("GPSLatitudeRef")
            lon = gps.get("GPSLongitude")
            lon_ref = gps.get("GPSLongitudeRef")
            gps_dec = {}
            if lat and lat_ref and lon and lon_ref:
                lat_d = dms_to_decimal(lat, lat_ref)
                lon_d = dms_to_decimal(lon, lon_ref)
                if lat_d is not None and lon_d is not None:
                    gps_dec = {"lat": lat_d, "lon": lon_d}
            data["GPS"] = {**gps, **({"decimal": gps_dec} if gps_dec else {})}
        return to_jsonable(data)
    except Exception:
        return to_jsonable(data)


def extract_icc_name(icc_bytes: Optional[bytes]) -> Optional[str]:
    if not icc_bytes:
        return None
    try:
        profile = ImageCms.ImageCmsProfile(bytes=icc_bytes)
        return ImageCms.getProfileName(profile)
    except Exception:
        return "ICC profile (descrição indisponível)"
    
def _app_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "ImgMetaApp"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ImgMetaApp"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return Path(base) / "ImgMetaApp"

def _templates_json_path() -> Path:
    return _app_data_dir() / "templates.json"


def image_metadata_dict(img: Image.Image) -> Dict[str, Any]:
    width, height = img.size
    info = dict(img.info)
    result: Dict[str, Any] = {
        "formato": img.format,
        "modo_cor": img.mode,
        "largura_px": width,
        "altura_px": height,
    }
    dpi = info.get("dpi")
    if dpi and isinstance(dpi, (tuple, list)) and len(dpi) == 2:
        result["dpi_x"], result["dpi_y"] = dpi
        result["unidade_densidade"] = "pixels por polegada (DPI)"
    else:
        jfif = info.get("jfif_density")
        jfif_unit = info.get("jfif_unit")
        if jfif and isinstance(jfif, (tuple, list)) and len(jfif) == 2:
            x, y = jfif
            if jfif_unit == 1:
                result["dpi_x"], result["dpi_y"] = x, y
                result["unidade_densidade"] = "pixels por polegada (JFIF)"
            elif jfif_unit == 2:
                result["dpi_x"], result["dpi_y"] = round(x * 2.54, 2), round(y * 2.54, 2)
                result["unidade_densidade"] = "pixels por polegada (de DPCM)"
    icc_name = extract_icc_name(info.get("icc_profile"))
    if icc_name:
        result["perfil_icc"] = icc_name
    exif = parse_exif(img)
    if exif:
        resumo_exif = {}
        for k in ("Make", "Model", "LensModel", "DateTimeOriginal", "Orientation"):
            if k in exif:
                resumo_exif[k] = exif[k]
        if "GPS" in exif and isinstance(exif["GPS"], dict):
            resumo_exif["GPS"] = exif["GPS"].get("decimal") or "presente"
        result["exif_resumo"] = to_jsonable(resumo_exif)
        result["exif_bruto"] = to_jsonable(exif)
    if info:
        result["info_container"] = to_jsonable(info)
    try:
        if getattr(img, "is_animated", False):
            result["animacao"] = {
                "qtd_frames": int(getattr(img, "n_frames", 0)),
                "formato_animado": True,
            }
    except Exception:
        pass
    return to_jsonable(result)


# Abridor universal

def open_any_image(source: Any) -> Image.Image:
    # 1) Pillow
    try:
        return Image.open(source if not isinstance(source, (bytes, bytearray)) else BytesIO(source))
    except Exception:
        pass
    # 2) RAW
    try:
        import rawpy, numpy as np  # type: ignore
        if isinstance(source, (str, Path)) and Path(source).is_file():
            with rawpy.imread(str(source)) as raw:
                rgb = raw.postprocess(output_bps=8)
            return Image.fromarray(rgb)
    except Exception:
        pass
    # 3) OpenCV
    try:
        import cv2, numpy as np  # type: ignore
        if isinstance(source, (bytes, bytearray)):
            arr = np.frombuffer(source, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
        elif isinstance(source, (str, Path)):
            bgr = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
        else:
            bgr = None
        if bgr is not None:
            if bgr.ndim == 3 and bgr.shape[2] == 3:
                bgr = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            elif bgr.ndim == 3 and bgr.shape[2] == 4:
                bgr = cv2.cvtColor(bgr, cv2.COLOR_BGRA2RGBA)
            return Image.fromarray(bgr)
    except Exception:
        pass
    # 4) imageio
    try:
        import imageio.v3 as iio  # type: ignore
        img_np = iio.imread(source if isinstance(source, (bytes, bytearray)) else str(source))
        if img_np is not None:
            try:
                import numpy as np
                if img_np.dtype != np.uint8:
                    img_np = (img_np / (img_np.max() or 1) * 255).astype("uint8")
            except Exception:
                pass
            return Image.fromarray(img_np)
    except Exception:
        pass
    # 5) pyvips
    try:
        import pyvips  # type: ignore
        if isinstance(source, (bytes, bytearray)):
            vimg = pyvips.Image.new_from_buffer(bytes(source), "", access="sequential")
        else:
            vimg = pyvips.Image.new_from_file(str(source), access="sequential")
        np_arr = vimg.numpy()
        return Image.fromarray(np_arr)
    except Exception:
        pass
    # 6) SVG -> PNG
    try:
        import cairosvg  # type: ignore
        if isinstance(source, (str, Path)) and str(source).lower().endswith(".svg"):
            png_bytes = cairosvg.svg2png(url=str(source))
            return Image.open(BytesIO(png_bytes))
        if isinstance(source, (bytes, bytearray)):
            png_bytes = cairosvg.svg2png(bytestring=source)
            return Image.open(BytesIO(png_bytes))
    except Exception:
        pass
    # 7) PDF -> primeira página
    try:
        from wand.image import Image as WandImage  # type: ignore
        w = WandImage(filename=str(source) if not isinstance(source, (bytes, bytearray)) else None,
                      blob=source if isinstance(source, (bytes, bytearray)) else None, resolution=200)
        if len(w.sequence) > 0:
            w = WandImage(image=w.sequence[0])
        img_bytes = w.make_blob(format='png')
        return Image.open(BytesIO(img_bytes))
    except Exception:
        pass
    try:
        from pdf2image import convert_from_bytes, convert_from_path  # type: ignore
        pages = convert_from_bytes(source) if isinstance(source, (bytes, bytearray)) else convert_from_path(str(source))
        if pages:
            return pages[0]
    except Exception:
        pass
    raise OSError("Formato não suportado ou dependências faltando para este tipo de arquivo.")


# ------------------ UI (PySide6) ------------------
from PySide6.QtCore import Qt, QTimer, QObject, QEvent, QPoint, QRect  # <<-- acrescente QObject, QEvent
from PySide6.QtWidgets import QDialog, QFileDialog
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QPlainTextEdit, QMessageBox,
    QMenuBar, QTabWidget, QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QHeaderView, QGroupBox, QComboBox, QInputDialog,
    QDialog, QDialogButtonBox, QProgressDialog, QStyle   # <--- AQUI
)

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

def _choose_directory(parent, title="Escolher pasta"):
    dlg = QFileDialog(parent, title)
    dlg.setOption(QFileDialog.DontUseNativeDialog, True)
    dlg.setFileMode(QFileDialog.Directory)
    dlg.setOption(QFileDialog.ShowDirsOnly, True)
    if dlg.exec() == QDialog.Accepted:
        files = dlg.selectedFiles()
        return files[0] if files else ""
    return ""

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
                # centralizar na tela do pai (ou primária)
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
            # aplica depois do show para garantir geometria correta
            QTimer.singleShot(0, apply)
        return super().eventFilter(obj, ev)

def install_dialog_normalizer(app: QApplication, width=200, height=100):
    norm = _DialogNormalizer(width, height)
    app.installEventFilter(norm)
    # evitar garbage collection
    app._dialog_normalizer = norm

def _center_dialog(widget, parent):
    try:
        # Centro no monitor da janela principal (se houver), senão no monitor primário
        parent_win = parent.window() if parent else None
        if parent_win and parent_win.isVisible() and parent_win.screen():
            screen_geo = parent_win.screen().availableGeometry()
        else:
            scr = QApplication.primaryScreen()
            screen_geo = scr.availableGeometry() if scr else None
        if screen_geo:
            geo = widget.frameGeometry()
            geo.setWidth(200)
            geo.setHeight(100)
            geo.moveCenter(screen_geo.center())
            widget.move(geo.topLeft())
    except Exception:
        pass

def _centered_messagebox(parent, icon, title, text,
                         buttons=QMessageBox.Ok,
                         defaultButton=QMessageBox.NoButton):
    m = QMessageBox(parent)
    m.setIcon(icon)
    m.setWindowTitle(title)
    m.setText(text)
    m.setStandardButtons(buttons)
    if defaultButton != QMessageBox.NoButton:
        m.setDefaultButton(defaultButton)
    m.setWindowModality(Qt.ApplicationModal)
    m.setFixedSize(200, 100)   # força 200x100
    _center_dialog(m, parent)
    return m.exec()

def _patch_qmessagebox_centered():
    def information(parent, title, text,
                    buttons=QMessageBox.Ok,
                    defaultButton=QMessageBox.NoButton):
        return _centered_messagebox(parent, QMessageBox.Information, title, text, buttons, defaultButton)

    def warning(parent, title, text,
                buttons=QMessageBox.Ok,
                defaultButton=QMessageBox.NoButton):
        return _centered_messagebox(parent, QMessageBox.Warning, title, text, buttons, defaultButton)

    def critical(parent, title, text,
                 buttons=QMessageBox.Ok,
                 defaultButton=QMessageBox.NoButton):
        return _centered_messagebox(parent, QMessageBox.Critical, title, text, buttons, defaultButton)

    def question(parent, title, text,
                 buttons=QMessageBox.Yes | QMessageBox.No,
                 defaultButton=QMessageBox.NoButton):
        return _centered_messagebox(parent, QMessageBox.Question, title, text, buttons, defaultButton)

    # Monkey-patch
    QMessageBox.information = staticmethod(information)
    QMessageBox.warning     = staticmethod(warning)
    QMessageBox.critical    = staticmethod(critical)
    QMessageBox.question    = staticmethod(question)

_patch_qmessagebox_centered()
# ======= Aba 1: Metadados =======
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

class ReplaceDialog(QDialog):
    def __init__(self, parent=None, current_name: str = ""):
        super().__init__(parent)
        # ... seu código ...
        self.setFixedSize(200, 100)  # opcional: mesmo padrão 200x100

  
    def __init__(self, parent=None, current_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Renomear (replace)")
        lay = QVBoxLayout(self)

        # Linha com [procurar]  →  [substituir]
        hl = QHBoxLayout()
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("procurar (ex.: shot_10)")
        arrow = QLabel("→")
        arrow.setStyleSheet("font-weight: bold; padding: 0 8px;")
        self.repl_edit = QLineEdit()
        self.repl_edit.setPlaceholderText("substituir por (ex.: pzd_1)")

        hl.addWidget(self.find_edit)
        hl.addWidget(arrow)
        hl.addWidget(self.repl_edit)
        lay.addLayout(hl)

        # Dica com nome atual
        if current_name:
            hint = QLabel(f"Nome atual: <b>{current_name}</b>")
            hint.setStyleSheet("color: #666;")
            lay.addWidget(hint)

        # Botões OK/Cancelar
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.setFixedSize(200, 100)

    def showEvent(self, e):
        super().showEvent(e)
        _center_dialog(self, self.parent())
    def values(self):
        return self.find_edit.text(), self.repl_edit.text()

# ======= Aba 2: Hierarquia (visual + templates) =======
# ======= Aba 2: Hierarquia (multi-abas + templates) =======
class HierarchyTab(QWidget):
    """
    Agora com múltiplas abas: cada aba possui sua própria árvore (QTreeWidget)
    e campo de 'Pasta base'. Templates são compartilhados entre as abas.
    """
    ROLE_FULLPATH = Qt.UserRole + 1
    ROLE_HAS_SUBDIRS = Qt.UserRole + 2
    ROLE_IS_DIR = Qt.UserRole + 3

    DUMMY_MARK = "…"

    # -------- Página interna (uma aba) --------
    class _Page(QWidget):
        def __init__(self, owner: 'HierarchyTab', base_path: Optional[str] = None):
            super().__init__(owner)
            self.owner = owner
            self._build_ui()
            if base_path:
                self.base_edit.setText(base_path)
                self.owner._load_fs_into_page(self, base_path)

        
        def _build_ui(self):
            v = QVBoxLayout(self)

            # Pasta base (da aba)
            h_base = QHBoxLayout()
            self.base_edit = QLineEdit()
            self.base_edit.setPlaceholderText("Pasta base desta aba…")
            btn_pick = QPushButton("Escolher…")
            btn_pick.clicked.connect(lambda: self.owner.pick_base())  # usa página ativa
            # Ler hierarquia SEMPRE abre nova aba (exigência do usuário)
            btn_load_fs = QPushButton("Abrir pasta...")
            btn_load_fs.clicked.connect(lambda: self.owner.load_hierarchy_dialog())
            h_base.addWidget(btn_load_fs)
            h_base.addWidget(self.base_edit)
            h_base.addWidget(btn_pick)
            v.addLayout(h_base)

            # Árvore desta aba
            self.tree = QTreeWidget()
            self.tree.setColumnCount(2)
            self.tree.setHeaderLabels(["Item", "Ações"])
            self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
            self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            self.tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
            self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.tree.setAnimated(True)
            self.tree.itemExpanded.connect(self.owner._on_item_expanded)
            self.tree.itemCollapsed.connect(self.owner._on_item_collapsed)
            v.addWidget(self.tree)

            self.tree.viewport().installEventFilter(self.owner)
            self.owner._vp2page[self.tree.viewport()] = self

            # Controles da árvore
            h_top = QHBoxLayout()
            btn_add_root = QPushButton("+ Raiz")
            btn_add_root.clicked.connect(lambda: self.owner.add_root())
            btn_add_sibling = QPushButton("+ Irmão")
            btn_add_sibling.clicked.connect(lambda: self.owner.add_sibling())
            btn_add_child = QPushButton("+ Filho")
            btn_add_child.clicked.connect(lambda: self.owner.add_child())
            btn_remove = QPushButton("Remover")
            btn_remove.clicked.connect(lambda: self.owner.remove_selected())
            h_top.addWidget(btn_add_root)
            h_top.addWidget(btn_add_sibling)
            h_top.addWidget(btn_add_child)
            h_top.addWidget(btn_remove)
            h_top.addStretch(1)
            v.addLayout(h_top)

            # Criar no disco (pasta base desta aba)
            h_build = QHBoxLayout()
            btn_build = QPushButton("Criar hierarquia no disco")
            btn_build.clicked.connect(lambda: self.owner.build_on_disk())
            h_build.addStretch(1)
            h_build.addWidget(btn_build)
            v.addLayout(h_build)

    # -------- HierarchyTab (contêiner de abas + templates) --------
    def __init__(self, parent=None):
        super().__init__(parent)
        self._vp2page = {}
        self.templates: Dict[str, List[List[str]]] = {}
        self.multi_mark_mode = False  # manutenção de API
        self._build_ui()
        self._busy_timer = None
        self._progress = None
        self._load_templates_from_disk()
        if not self.templates:
            self._register_quick_template_3k4k()
            self._save_templates_to_disk()

        # começa com uma aba vazia
        self._add_empty_tab()

    def eventFilter(self, obj, ev):
        # Só nos interessa clique no viewport de uma tree nossa
        if ev.type() == QEvent.MouseButtonPress and obj in getattr(self, "_vp2page", {}):
            pg = self._vp2page[obj]
            tree = pg.tree
            pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
            idx = tree.indexAt(pos)
            if idx.isValid():
                row_rect = tree.visualRect(idx)
                # profundidade para calcular a coluna do indicador (expander)
                depth = 0
                p = idx.parent()
                while p.isValid():
                    depth += 1
                    p = p.parent()
                x0 = row_rect.left() + depth * tree.indentation()
                indicator_width = 18  # largura razoável do triângulo
                indicator_rect = QRect(x0, row_rect.top(), indicator_width, row_rect.height())

                # Se clicou na área do expander: alterna expandido e NÃO seleciona
                if indicator_rect.contains(pos):
                    tree.setExpanded(idx, not tree.isExpanded(idx))
                    return True  # consumiu o evento → evita seleção

        return super().eventFilter(obj, ev)

    # ---------------- UI principal ----------------
    def _begin_busy(self, text: str = "Carregando…"):
        # só exibe se demorar (>300ms)
        from PySide6.QtCore import QTimer
        if self._busy_timer:
            try: self._busy_timer.stop()
            except Exception: pass
            self._busy_timer = None

        self._busy_timer = QTimer(self)
        self._busy_timer.setSingleShot(True)

        def _show():
            if self._progress is None:
                self._progress = QProgressDialog(text, None, 0, 0, self)
                self._progress.setWindowTitle("Aguarde")
                self._progress.setCancelButton(None)
                self._progress.setWindowModality(Qt.ApplicationModal)
                self._progress.setMinimumWidth(360)
                self._progress.show()
                self._progress.setFixedSize(200, 100)  # se quiser padronizar o tamanho
                _center_dialog(self._progress, self)
                QApplication.processEvents()

        self._busy_timer.timeout.connect(_show)
        self._busy_timer.start(300)  # só aparece após 300ms

    def _end_busy(self):
        if self._busy_timer:
            try: self._busy_timer.stop()
            except Exception: pass
            self._busy_timer.deleteLater()
            self._busy_timer = None
        if self._progress:
            try:
                self._progress.close()
            except Exception:
                pass
            self._progress.deleteLater()
            self._progress = None

    def _pump_events_periodically(self, counter: int, every: int = 64):
        """Chame durante loops longos para manter o loading animado."""
        if counter % every == 0:
            QApplication.processEvents()
    def _build_ui(self):
        v = QVBoxLayout(self)

        # Abas
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close)
        v.addWidget(self.tabs)

        # Barra de controles de abas
        h_tabs = QHBoxLayout()
        btn_new_tab = QPushButton("+ Nova aba vazia")
        btn_new_tab.clicked.connect(self._add_empty_tab)
        btn_open_fs = QPushButton("Abrir pasta...")
        btn_open_fs.clicked.connect(self.load_hierarchy_dialog)
        h_tabs.addWidget(btn_new_tab)
        h_tabs.addWidget(btn_open_fs)
        h_tabs.addStretch(1)
        v.addLayout(h_tabs)

        # Templates compartilhados
        v.addWidget(self._build_templates_group())

    def _on_tab_close(self, idx: int):
        w = self.tabs.widget(idx)
        if w:
            w.deleteLater()
        self.tabs.removeTab(idx)

    def _add_empty_tab(self):
        page = HierarchyTab._Page(self)
        idx = self.tabs.addTab(page, "Hierarquia")
        self.tabs.setCurrentIndex(idx)

    def _add_tab_from_fs(self, base_dir: str):
        title = Path(base_dir).name or "Hierarquia"
        self._begin_busy(f"Abrindo “{title}”…")
        try:
            page = HierarchyTab._Page(self, base_path=base_dir)
            idx = self.tabs.addTab(page, title)
            self.tabs.setCurrentIndex(idx)
        finally:
            self._end_busy()

    # Página (aba) ativa
    def _cur_page(self) -> Optional['_Page']:
        w = self.tabs.currentWidget()
        return w if isinstance(w, HierarchyTab._Page) else None

    # ----------------- Diálogos e FS -----------------
    def load_hierarchy_dialog(self):
        base = _choose_directory(self, "Escolher pasta para abrir")
        if not base:
            return

        pg = self._cur_page()
        # “Vazia” = nenhuma raiz na árvore
        is_empty = bool(pg and pg.tree.topLevelItemCount() == 0)

        title = Path(base).name or "Hierarquia"
        if is_empty:
            # abre na aba atual
            self._begin_busy(f"Abrindo “{title}”…")
            try:
                pg.base_edit.setText(base)
                self._load_fs_into_page(pg, base)
                # renomeia o título da aba atual
                idx = self.tabs.indexOf(pg)
                if idx != -1:
                    self.tabs.setTabText(idx, title)
            finally:
                self._end_busy()
        else:
            # já tem coisa: abre em uma nova aba
            self._add_tab_from_fs(base)


    def _load_fs_into_page(self, page: '_Page', base_dir: str):
        try:
            p = Path(base_dir)
            if not p.exists() or not p.is_dir():
                raise FileNotFoundError("Pasta inválida")

            # raiz com lazy
            root = self._make_dir_item(page, p.name, str(p))
            page.tree.addTopLevelItem(root)
            self._ensure_buttons(page, root)
            # adiciona um filho dummy para mostrar a setinha
            root.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))

            page.tree.expandItem(root)  # se quiser já abrir a raiz
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao abrir: {e}")

    def _make_fs_item(self, page: '_Page', name: str, full_path: str, is_dir: bool) -> QTreeWidgetItem:
        it = self._make_item(page, name, is_dir=is_dir)
        it.setData(0, self.ROLE_FULLPATH, full_path)
        it.setData(0, self.ROLE_IS_DIR, 1 if is_dir else 0)  # redundante, mas ok
        try:
            icon = QApplication.style().standardIcon(QStyle.SP_DirIcon if is_dir else QStyle.SP_FileIcon)
            it.setIcon(0, icon)
        except Exception:
            pass
        return it

    def _make_dir_item(self, page: '_Page', name: str, full_path: str) -> QTreeWidgetItem:
        return self._make_fs_item(page, name, full_path, True)

    def _make_file_item(self, page: '_Page', name: str, full_path: str) -> QTreeWidgetItem:
        return self._make_fs_item(page, name, full_path, False)

    def _load_children_for_item(self, page: '_Page', parent_item: QTreeWidgetItem, parent_path: str):
        try:
            with os.scandir(parent_path) as it:
                dirs = []
                files = []
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=True):      # ← True
                            dirs.append(e)
                        elif e.is_file(follow_symlinks=True):   # ← True
                            files.append(e)
                    except Exception:
                        continue

            dirs.sort(key=lambda e: e.name.lower())
            files.sort(key=lambda e: e.name.lower())

            created_dirs = 0

            # Pastas primeiro (com dummy para lazy)
            for idx, e in enumerate(dirs, 1):
                child = self._make_dir_item(page, e.name, e.path)
                parent_item.addChild(child)
                self._ensure_buttons(page, child)
                child.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))
                created_dirs += 1
                self._pump_events_periodically(idx, every=64)

            # Depois arquivos (sem dummy)
            for idx, e in enumerate(files, 1):
                child = self._make_file_item(page, e.name, e.path)
                parent_item.addChild(child)
                self._ensure_buttons(page, child)
                self._pump_events_periodically(idx, every=64)

            parent_item.setData(0, self.ROLE_HAS_SUBDIRS, 1 if created_dirs > 0 else 0)

        except PermissionError:
            parent_item.setData(0, self.ROLE_HAS_SUBDIRS, 0)
        except Exception:
            parent_item.setData(0, self.ROLE_HAS_SUBDIRS, 0)

    
    def _on_item_expanded(self, it: QTreeWidgetItem):
        # Se o primeiro filho é um dummy, carregue de verdade
        if it.childCount() == 1 and it.child(0).text(0) == self.DUMMY_MARK:
            it.takeChildren()
            pg = self._page_of_item(it)
            if not pg:
                return
            base = it.data(0, self.ROLE_FULLPATH)
            if not base:
                return
            # spinner (mostra só se demorar > 300ms)
            self._begin_busy(f"Lendo “{Path(base).name}”…")
            try:
                self._load_children_for_item(pg, it, str(base))
            finally:
                self._end_busy()

    def _on_item_collapsed(self, it: QTreeWidgetItem):
        # Ao colapsar, libere memória dos widgets e filhos
        self._free_subtree_widgets(it)
        has_sub = bool(it.data(0, self.ROLE_HAS_SUBDIRS))
        it.takeChildren()
        # recoloca um dummy só se tem subpastas conhecidas
        if has_sub:
            it.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))

    def _free_subtree_widgets(self, item: QTreeWidgetItem):
        pg = self._page_of_item(item)
        if not pg:
            return
        stack = [item]
        while stack:
            cur = stack.pop()
            w = pg.tree.itemWidget(cur, 1)
            if w:
                pg.tree.removeItemWidget(cur, 1)
                w.deleteLater()
            for i in range(cur.childCount()):
                stack.append(cur.child(i))


    def _populate_children_from_dir(self, page: '_Page', parent_item: QTreeWidgetItem, parent_path: Path):
        try:
            subdirs = sorted([d for d in parent_path.iterdir() if d.is_dir()], key=lambda d: d.name.lower())
        except Exception:
            subdirs = []

        for i, d in enumerate(subdirs, 1):
            child_item = self._make_item(page, d.name, is_dir=True)
            parent_item.addChild(child_item)

            # ✅ dê botões de ação para TODA pasta criada
            self._ensure_buttons(page, child_item)

            # recursão
            self._populate_children_from_dir(page, child_item, d)

            # mantém o spinner fluindo (opcional)
            self._pump_events_periodically(i, every=64)


    # ----------------- Grupo de Templates -----------------
    def _build_templates_group(self) -> QGroupBox:
        g = QGroupBox("Templates de Hierarquia (compartilhados)")
        lay = QVBoxLayout(g)

        # Linha 1
        h1 = QHBoxLayout()
        self.cb_templates = QComboBox()
        self.cb_templates.setEditable(False)
        self.cb_templates.setSizeAdjustPolicy(QComboBox.AdjustToContents)

        btn_apply = QPushButton("Aplicar nos pais selecionados (aba ativa)")
        btn_apply.clicked.connect(self.apply_selected_template_to_selected_parents)

        h1.addWidget(self.cb_templates, stretch=1)
        h1.addWidget(btn_apply)
        lay.addLayout(h1)

        # Linha 2
        h2 = QHBoxLayout()
        btn_new = QPushButton("Novo template…")
        btn_new.clicked.connect(self.create_template_dialog)
        btn_edit = QPushButton("Editar template…")
        btn_edit.clicked.connect(self.edit_current_template_dialog)
        btn_del = QPushButton("Remover template")
        btn_del.clicked.connect(self.delete_current_template)
        h2.addWidget(btn_new)
        h2.addWidget(btn_edit)
        h2.addWidget(btn_del)
        h2.addStretch(1)
        lay.addLayout(h2)

        # Linha 3
        h3 = QHBoxLayout()
        # btn_import = QPushButton("Importar templates (JSON)…")
        # btn_import.clicked.connect(self.import_templates_json)
        # btn_export = QPushButton("Exportar templates (JSON)…")
        # btn_export.clicked.connect(self.export_templates_json)
        # btn_adhoc = QPushButton("Aplicar hierarquia ad-hoc na aba ativa…")
        # btn_adhoc.clicked.connect(self.apply_template_adhoc_dialog)
        # h3.addWidget(btn_import)
        # h3.addWidget(btn_export)
        h3.addStretch(1)
        # h3.addWidget(btn_adhoc)
        lay.addLayout(h3)

        return g

    # ----------------- Helpers de árvore (multi-aba) -----------------
    def _make_item(self, page: '_Page', name: str, is_dir: bool = True) -> QTreeWidgetItem:
        it = QTreeWidgetItem([name, ""])
        it.setFlags(it.flags() | Qt.ItemIsEditable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        it.setData(0, self.ROLE_IS_DIR, 1 if is_dir else 0)
        try:
            icon = QApplication.style().standardIcon(QStyle.SP_DirIcon if is_dir else QStyle.SP_FileIcon)
            it.setIcon(0, icon)
        except Exception:
            pass
        # 👇 força o triângulo/expander a aparecer mesmo quando selecionado ou antes do lazy-load
        if is_dir:
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)

        it.setData(0, Qt.UserRole, id(page))
        return it



    def _ensure_buttons(self, page: '_Page', item: QTreeWidgetItem):
        from PySide6.QtWidgets import QMenu, QWidget, QHBoxLayout
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)

        is_dir = bool(item.data(0, self.ROLE_IS_DIR))

        b_plus = QPushButton("+")
        b_plus.setFixedWidth(24)
        b_plus.clicked.connect(lambda: self.add_child(item))
        b_plus.setVisible(is_dir)  # <-- só aparece para pastas

        b_more = QPushButton("⋯")
        b_more.setFixedWidth(24)

        def persistent_menu():
            menu = QMenu(self)

            act_sib = menu.addAction("+ Irmão")
            act_child = menu.addAction("+ Filho")
            act_child.setEnabled(is_dir)  # arquivo não pode ter filho
            act_rename = menu.addAction("Renomear")
            act_rename_replace = menu.addAction("Renomear (replace…)")

            act_sel_one = menu.addAction("Selecionar")
            act_sel_children = menu.addAction("Selecionar todas as filhas")
            act_sel_siblings = menu.addAction("Selecionar todas as irmãs")

            act_del = menu.addAction("Remover")

            # Abre ANCORADO no botão "⋯"
            chosen = menu.exec(b_more.mapToGlobal(QPoint(0, b_more.height())))
            if not chosen:
                return  # clicou fora → fecha e não reabre

            if chosen == act_sib:
                self.add_sibling(item)
            elif chosen == act_child and is_dir:
                self.add_child(item)
            elif chosen == act_rename:
                pg = self._page_of_item(item)
                idx = pg.tree.indexFromItem(item, 0)
                pg.tree.edit(idx)
            elif chosen == act_rename_replace:
                pg = self._page_of_item(item)
                targets = pg.tree.selectedItems() or [item]
                old_names_preview = targets[0].text(0) if targets else ""
                dlg = ReplaceDialog(self, current_name=old_names_preview)
                dlg.exec()  # menu já está fechado; só executa o diálogo
            elif chosen == act_sel_one:
                pg = self._page_of_item(item)
                pg.tree.clearSelection()
                item.setSelected(True)
            elif chosen == act_sel_children:
                pg = self._page_of_item(item)
                pg.tree.clearSelection()

                # Seleciona recursivamente TODAS as descendentes (todos os níveis)
                def _select_recursive(node: QTreeWidgetItem):
                    for i in range(node.childCount()):
                        ch = node.child(i)
                        ch.setSelected(True)
                        _select_recursive(ch)

                _select_recursive(item)

                if item.childCount() == 0:
                    item.setSelected(True)
            elif chosen == act_sel_siblings:
                pg = self._page_of_item(item)
                pg.tree.clearSelection()
                for node in self._iter_siblings(item):
                    node.setSelected(True)
            elif chosen == act_del:
                self.remove_item(item)


        b_more.clicked.connect(persistent_menu)

        h.addWidget(b_plus)
        h.addWidget(b_more)
        h.addStretch(1)
        page.tree.setItemWidget(item, 1, w)


    def _page_of_item(self, it: QTreeWidgetItem) -> '_Page':
        # Encontrar a página a partir do item (via UserRole)
        for idx in range(self.tabs.count()):
            pg = self.tabs.widget(idx)
            if isinstance(pg, HierarchyTab._Page):
                # confere se item pertence à tree desta página
                if it.treeWidget() is pg.tree:
                    return pg
        # fallback: página atual
        return self._cur_page()

    def _find_child_by_name(self, parent_item: QTreeWidgetItem, name: str) -> Optional[QTreeWidgetItem]:
        for i in range(parent_item.childCount()):
            if parent_item.child(i).text(0) == name:
                return parent_item.child(i)
        return None

    def _ensure_path_under(self, parent_item: QTreeWidgetItem, parts: List[str]) -> QTreeWidgetItem:
        current = parent_item
        pg = self._page_of_item(parent_item)
        for part in parts:
            s = part.strip()
            if not s:
                continue
            existing = self._find_child_by_name(current, s)
            if existing is None:
                new_item = self._make_item(pg, s, is_dir=True)
                current.addChild(new_item)
                self._ensure_buttons(pg, new_item)
                current = new_item
            else:
                current = existing
        return current

    def _iter_siblings(self, it: QTreeWidgetItem):
        parent = it.parent()
        pg = self._page_of_item(it)
        if parent is None:
            for i in range(pg.tree.topLevelItemCount()):
                sib = pg.tree.topLevelItem(i)
                if sib is not it:
                    yield sib
        else:
            for i in range(parent.childCount()):
                sib = parent.child(i)
                if sib is not it:
                    yield sib

    def _iter_descendants(self, it: QTreeWidgetItem):
        stack = [it]
        for i in range(it.childCount()):
            stack.append(it.child(i))
        while stack:
            cur = stack.pop()
            if cur is not it:
                yield cur
            for j in range(cur.childCount()):
                stack.append(cur.child(j))

    def _iter_all_items(self, page: Optional['_Page'] = None) -> List[QTreeWidgetItem]:
        pg = page or self._cur_page()
        if not pg:
            return []
        result: List[QTreeWidgetItem] = []
        def walk(node: QTreeWidgetItem):
            result.append(node)
            for i in range(node.childCount()):
                walk(node.child(i))
        for i in range(pg.tree.topLevelItemCount()):
            walk(pg.tree.topLevelItem(i))
        return result

    # -------------- Operações de seleção e rename --------------
    def _replace_in_items(self, page: '_Page', items: List[QTreeWidgetItem], find_txt: str, repl_txt: str, only_first: bool = False) -> int:
        changed = 0
        if not find_txt:
            return 0
        for it in items:
            old = it.text(0)
            new = old.replace(find_txt, repl_txt, 1) if only_first else old.replace(find_txt, repl_txt)
            if new != old and new.strip():
                it.setText(0, new)
                changed += 1
        return changed

    def _mark(self, it: QTreeWidgetItem):
        it.setSelected(True)

    def _clear_selection(self):
        pg = self._cur_page()
        if pg:
            pg.tree.clearSelection()

    # -------------- Modo marcar múltiplos (API preservada) --------------
    def _set_checkable_for_all(self, enable: bool):
        pg = self._cur_page()
        if not pg:
            return
        for it in self._iter_all_items(pg):
            flags = it.flags()
            if enable:
                it.setFlags(flags | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Unchecked)
            else:
                it.setFlags(flags & ~Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Unchecked)

    def toggle_multi_mark_mode(self, enabled: bool):
        self.multi_mark_mode = enabled
        self._set_checkable_for_all(enabled)
        # Se esses botões existirem no seu layout, atualiza o estado;
        # mantido apenas para compatibilidade com chamadas externas.
        if hasattr(self, "btn_clear_checks"):
            self.btn_clear_checks.setEnabled(enabled)
        if hasattr(self, "btn_multi"):
            self.btn_multi.setText("Sair do modo marcar" if enabled else "Marcar vários")

    def clear_all_checks(self):
        if not self.multi_mark_mode:
            return
        pg = self._cur_page()
        if not pg:
            return
        for it in self._iter_all_items(pg):
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(0, Qt.Unchecked)

    def iter_checked_items(self) -> List[QTreeWidgetItem]:
        pg = self._cur_page()
        if not pg:
            return []
        return [it for it in self._iter_all_items(pg)
                if (it.flags() & Qt.ItemIsUserCheckable) and it.checkState(0) == Qt.Checked]

    # -------------- Ações da ÁRVORE (sempre na aba ativa) --------------
    def add_root(self):
        pg = self._cur_page()
        if not pg:
            return
        item = self._make_item(pg, "nova_pasta", is_dir=True)
        pg.tree.addTopLevelItem(item)
        self._ensure_buttons(pg, item)
        pg.tree.edit(pg.tree.indexFromItem(item, 0))

    def add_child(self, ref: Optional[QTreeWidgetItem] = None):
        pg = self._cur_page()
        if not pg:
            return
        if ref is None:
            sel = pg.tree.selectedItems()
            if not sel:
                return
            ref = sel[0]

        # <-- impede filho em arquivo
        if not bool(ref.data(0, self.ROLE_IS_DIR)):
            QMessageBox.information(self, "Item é arquivo", "Arquivos não podem ter filhos. Selecione uma pasta.")
            return

        child = self._make_item(pg, "subpasta", is_dir=True)
        ref.addChild(child)
        self._ensure_buttons(pg, child)
        pg.tree.expandItem(ref)
        pg.tree.edit(pg.tree.indexFromItem(child, 0))


    def add_sibling(self, ref: Optional[QTreeWidgetItem] = None):
        pg = self._cur_page()
        if not pg:
            return
        if ref is None:
            sel = pg.tree.selectedItems()
            if not sel:
                return
            ref = sel[0]
        parent = ref.parent()
        sib = self._make_item(pg, "nova_pasta")
        if parent is None:
            pg.tree.addTopLevelItem(sib)
        else:
            parent.addChild(sib)
        self._ensure_buttons(pg, sib)
        pg.tree.edit(pg.tree.indexFromItem(sib, 0))

    def remove_selected(self):
        pg = self._cur_page()
        if not pg:
            return
        for it in pg.tree.selectedItems():
            self.remove_item(it)

    def remove_item(self, it: QTreeWidgetItem):
        parent = it.parent()
        pg = self._page_of_item(it)
        idx = (parent.indexOfChild(it) if parent else pg.tree.indexOfTopLevelItem(it))
        if parent:
            parent.takeChild(idx)
        else:
            pg.tree.takeTopLevelItem(idx)

    # -------------- Serialização & criação no disco (aba ativa) --------------
    def _collect_paths(self) -> List[List[str]]:
        pg = self._cur_page()
        if not pg:
            return []
        paths: List[List[str]] = []
        def walk(node: QTreeWidgetItem, stack: List[str]):
            n = node.text(0).strip()
            if not n:
                return
            stack.append(n)
            paths.append(list(stack))
            for i in range(node.childCount()):
                walk(node.child(i), stack)
            stack.pop()
        for i in range(pg.tree.topLevelItemCount()):
            walk(pg.tree.topLevelItem(i), [])
        return paths

    def build_on_disk(self):
        pg = self._cur_page()
        if not pg:
            return
        base = pg.base_edit.text().strip()
        if not base:
            QMessageBox.warning(self, "Atenção", "Escolha a pasta base desta aba.")
            return
        try:
            pbase = Path(base)
            if not pbase.exists() or not pbase.is_dir():
                raise FileNotFoundError("Pasta base inválida")
            paths = self._collect_paths()
            if not paths:
                QMessageBox.information(self, "Criar", "Nada para criar.")
                return
            for parts in paths:
                Path(pbase.joinpath(*parts)).mkdir(parents=True, exist_ok=True)
            QMessageBox.information(self, "Concluído", f"Criadas/confirmadas {len(paths)} pastas.")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao criar: {e}")

    # -------------- Base da aba ativa --------------
    def pick_base(self):
        pg = self._cur_page()
        if not pg:
            return
        base = _choose_directory(self, "Escolher pasta base")
        if base:
            pg.base_edit.setText(base)

    # -------------- Templates (compartilhados) --------------
    def _refresh_templates_combo(self, select_name: Optional[str] = None):
        self.cb_templates.blockSignals(True)
        current = self.cb_templates.currentText()
        self.cb_templates.clear()
        names = sorted(self.templates.keys())
        self.cb_templates.addItems(names)
        if select_name and select_name in self.templates:
            self.cb_templates.setCurrentText(select_name)
        elif current in self.templates:
            self.cb_templates.setCurrentText(current)
        self.cb_templates.blockSignals(False)

    def _register_quick_template_3k4k(self):
        name = "Quick 3k/4k (jpg/png)"
        spec = [["3k", "jpg"], ["3k", "png"], ["4k", "jpg"], ["4k", "png"]]
        self.templates[name] = spec
        self._refresh_templates_combo(select_name=name)

    def _load_templates_from_disk(self):
        try:
            fp = _templates_json_path()
            if fp.exists():
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    fixed = {}
                    for k, v in data.items():
                        if isinstance(k, str) and isinstance(v, list) and all(isinstance(p, list) for p in v):
                            fixed[k] = [[str(x) for x in p] for p in v]
                    self.templates.update(fixed)
        except Exception:
            pass
        finally:
            self._refresh_templates_combo()

    def _save_templates_to_disk(self):
        try:
            fp = _templates_json_path()
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps(self.templates, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _parse_template_lines(self, text: str) -> List[List[str]]:
        paths: List[List[str]] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("/") if p.strip()]
            if parts:
                paths.append(parts)
        return paths

    def apply_selected_template_to_selected_parents(self):
        name = self.cb_templates.currentText()
        if not name or name not in self.templates:
            QMessageBox.information(self, "Aplicar template", "Selecione um template.")
            return
        pg = self._cur_page()
        if not pg:
            return
        sel = [it for it in pg.tree.selectedItems() if bool(it.data(0, self.ROLE_IS_DIR))]
        if not sel:
            QMessageBox.information(self, "Aplicar template", "Selecione uma ou mais PASTAS na árvore da aba ativa.")
            return
        for parent in sel:
            for parts in self.templates[name]:
                self._ensure_path_under(parent, parts)
            pg.tree.expandItem(parent)

    def apply_template_adhoc_dialog(self):
        pg = self._cur_page()
        if not pg:
            return
        example = "3k/jpg\n3k/png\n4k/jpg\n4k/png"
        text, ok = QInputDialog.getMultiLineText(
            self, "Aplicar hierarquia ad-hoc (aba ativa)",
            "Cole a hierarquia (uma linha por caminho; use '/' para níveis):",
            example
        )
        if not ok:
            return
        path_specs = self._parse_template_lines(text)
        if not path_specs:
            QMessageBox.information(self, "Aplicar hierarquia", "Nenhum caminho válido informado.")
            return
        sel = pg.tree.selectedItems()
        if not sel:
            QMessageBox.information(self, "Aplicar hierarquia", "Selecione um ou mais pais na árvore da aba ativa.")
            return
        for parent in sel:
            for parts in path_specs:
                self._ensure_path_under(parent, parts)
            pg.tree.expandItem(parent)

    def create_template_dialog(self):
        name, ok = QInputDialog.getText(self, "Novo template", "Nome do template:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.templates:
            QMessageBox.warning(self, "Template", "Já existe um template com esse nome.")
            return
        example = "3k/jpg\n3k/png\n4k/jpg\n4k/png"
        text, ok2 = QInputDialog.getMultiLineText(
            self, "Novo template", "Digite a hierarquia (uma linha por caminho; use '/' para níveis):", example
        )
        if not ok2:
            return
        paths = self._parse_template_lines(text)
        if not paths:
            QMessageBox.information(self, "Template", "Nenhum caminho válido informado.")
            return
        self.templates[name] = paths
        self._refresh_templates_combo(select_name=name)
        self._save_templates_to_disk()
        QMessageBox.information(self, "Template", "Template criado e salvo.")

    def edit_current_template_dialog(self):
        name = self.cb_templates.currentText()
        if not name or name not in self.templates:
            QMessageBox.information(self, "Editar template", "Selecione um template.")
            return
        current_lines = "\n".join("/".join(p) for p in self.templates[name])
        new_text, ok = QInputDialog.getMultiLineText(
            self, f"Editar template: {name}",
            "Edite a hierarquia (uma linha por caminho; use '/' para níveis):",
            current_lines
        )
        if not ok:
            return
        new_paths = self._parse_template_lines(new_text)
        if not new_paths:
            QMessageBox.information(self, "Editar template", "Nenhum caminho válido informado.")
            return
        self.templates[name] = new_paths
        self._save_templates_to_disk()
        QMessageBox.information(self, "Editar template", "Template atualizado e salvo.")

    def delete_current_template(self):
        name = self.cb_templates.currentText()
        if not name or name not in self.templates:
            return
        if QMessageBox.question(self, "Remover template", f"Remover '{name}'?") == QMessageBox.Yes:
            del self.templates[name]
            self._refresh_templates_combo()
            self._save_templates_to_disk()
            QMessageBox.information(self, "Template", "Template removido.")

    def import_templates_json(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Importar templates (JSON)", filter="JSON (*.json)")
        if not fn:
            return
        try:
            data = json.loads(Path(fn).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Estrutura JSON inválida (esperado objeto).")
            count = 0
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, list) and all(isinstance(p, list) for p in v):
                    self.templates[k] = [[str(x) for x in p] for p in v]
                    count += 1
            self._refresh_templates_combo()
            self._save_templates_to_disk()
            QMessageBox.information(self, "Importar templates", f"Importados {count} templates (salvos).")
        except Exception as e:
            QMessageBox.critical(self, "Importar templates", f"Falha: {e}")

    def export_templates_json(self):
        if not self.templates:
            QMessageBox.information(self, "Exportar templates", "Não há templates para exportar.")
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Exportar templates (JSON)", filter="JSON (*.json)")
        if not fn:
            return
        try:
            Path(fn).write_text(json.dumps(self.templates, ensure_ascii=False, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Exportar templates", "Exportado com sucesso.")
        except Exception as e:
            QMessageBox.critical(self, "Exportar templates", f"Falha: {e}")


# ======= Janela Principal =======
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ultimate File Penerator")
        self.resize(1100, 900)

        # Abas
        self.tabs = QTabWidget()
        self.tab_meta = QWidget()
        self.tab_hier = HierarchyTab()
        self.tabs.addTab(self.tab_meta, "Metadados")
        self.tabs.addTab(self.tab_hier, "Hierarquia")
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


def main():
    import sys
    app = QApplication(sys.argv)
    install_dialog_normalizer(app, 500, 300)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
