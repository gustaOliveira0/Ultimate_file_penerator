"""
Image Metadata Desktop App (PySide6)
-----------------------------------

• App desktop (Windows/macOS/Linux) com 3 ferramentas em abas:
  1) Metadados: drag & drop, abrir arquivo, caminho absoluto → JSON seguro.
  2) Hierarquia (visual): editor de árvore + gerenciador de **templates de hierarquia**:
     - Criar template por texto (uma linha por caminho; '/' separa níveis)
     - Salvar/Carregar templates em JSON (persistido em %APPDATA%/Library/.config)
     - Aplicar template em múltiplas pastas selecionadas (sem duplicar nós)
     - Atalhos rápidos (3k/4k com jpg/png)
     - Lazy loading do FS com setinha/expander

  3) File Penerator (NOVO):
     - Escolher diretório de origem (lazy, igual à Hierarquia)
     - Botão “Penerator” abre diálogo com filtros multi-seleção:
         ▸ Extensões (auto detectadas no diretório)
         ▸ Resoluções/buckets (4k/5k; auto detecta disponíveis)
     - Solicita pasta destino para **criar estrutura** combinando filtros (ex.: jpg/4k, png/5k, …)
     - Move as imagens da origem para dentro dos buckets correspondentes (ext/res)
     - Após mover, pergunta um **segundo destino opcional** para replicar SOMENTE a estrutura (sem arquivos)

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
from typing import Any, Dict, Optional, List, Tuple, Set
import os
import sys
import shutil

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

# ------------------ Utilitários (metadados/imagens) ------------------

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

def _filter_templates_json_path() -> Path:
    return _app_data_dir() / "filter_templates.json"


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
from PySide6.QtCore import Qt, QTimer, QObject, QEvent, QPoint, QRect
from PySide6.QtWidgets import QDialog, QFileDialog
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QPlainTextEdit, QMessageBox,
    QMenuBar, QTabWidget, QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QHeaderView, QGroupBox, QComboBox, QInputDialog,QGridLayout, QToolButton,
    QDialogButtonBox, QProgressDialog, QStyle, QListWidget, QListWidgetItem, QCheckBox
)

# ---------- Helpers de diálogos/centrais ----------
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


def install_dialog_normalizer(app: QApplication, width=500, height=300):
    norm = _DialogNormalizer(width, height)
    app.installEventFilter(norm)
    app._dialog_normalizer = norm  # evitar GC


def _center_dialog(widget, parent):
    try:
        parent_win = parent.window() if parent else None
        if parent_win and parent_win.isVisible() and parent_win.screen():
            screen_geo = parent_win.screen().availableGeometry()
        else:
            scr = QApplication.primaryScreen()
            screen_geo = scr.availableGeometry() if scr else None
        if screen_geo:
            geo = widget.frameGeometry()
            geo.setWidth(widget.width() or 500)
            geo.setHeight(widget.height() or 300)
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
        self.setWindowTitle("Renomear (replace)")
        lay = QVBoxLayout(self)

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

        if current_name:
            hint = QLabel(f"Nome atual: <b>{current_name}</b>")
            hint.setStyleSheet("color: #666;")
            lay.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def showEvent(self, e):
        super().showEvent(e)
        _center_dialog(self, self.parent())

    def values(self):
        return self.find_edit.text(), self.repl_edit.text()


# ======= Aba 2: Hierarquia (visual + templates) =======
class HierarchyTab(QWidget):
    """
    Múltiplas abas internas: cada aba possui sua própria árvore (QTreeWidget)
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
            # Ler hierarquia SEMPRE abre nova aba
            btn_load_fs = QPushButton("Abrir pasta…")
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
        self.multi_mark_mode = False
        self._build_ui()
        self._busy_timer: Optional[QTimer] = None
        self._progress: Optional[QProgressDialog] = None
        self._load_templates_from_disk()
        if not self.templates:
            self._register_quick_template_3k4k()
            self._save_templates_to_disk()
        self._add_empty_tab()

         # --- Templates: helpers de parsing e diálogos (ADD) ---
    def _parse_template_lines(self, text: str) -> List[List[str]]:
        """
        Converte texto (uma linha por caminho; '/' separa níveis) em lista de listas.
        Linhas em branco ou começando com '#' são ignoradas.
        """
        paths: List[List[str]] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("/") if p.strip()]
            if parts:
                paths.append(parts)
        return paths

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
            self, "Novo template",
            "Digite a hierarquia (uma linha por caminho; use '/' para níveis):",
            example
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

    def eventFilter(self, obj, ev):
        # Só nos interessa clique no viewport de uma tree nossa
        if ev.type() == QEvent.MouseButtonPress and obj in getattr(self, "_vp2page", {}):
            pg = self._vp2page[obj]
            tree = pg.tree
            pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
            idx = tree.indexAt(pos)
            if idx.isValid():
                row_rect = tree.visualRect(idx)
                depth = 0
                p = idx.parent()
                while p.isValid():
                    depth += 1
                    p = p.parent()
                x0 = row_rect.left() + depth * tree.indentation()
                indicator_width = 18
                indicator_rect = QRect(x0, row_rect.top(), indicator_width, row_rect.height())
                if indicator_rect.contains(pos):
                    tree.setExpanded(idx, not tree.isExpanded(idx))
                    return True
        return super().eventFilter(obj, ev)

    # ---------------- UI principal ----------------
    def _begin_busy(self, text: str = "Carregando…"):
        if self._busy_timer:
            try:
                self._busy_timer.stop()
            except Exception:
                pass
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
                _center_dialog(self._progress, self)
                QApplication.processEvents()

        self._busy_timer.timeout.connect(_show)
        self._busy_timer.start(300)

    def _end_busy(self):
        if self._busy_timer:
            try:
                self._busy_timer.stop()
            except Exception:
                pass
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
        if counter % every == 0:
            QApplication.processEvents()

    def _build_ui(self):
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close)
        v.addWidget(self.tabs)

        h_tabs = QHBoxLayout()
        btn_new_tab = QPushButton("+ Nova aba vazia")
        btn_new_tab.clicked.connect(self._add_empty_tab)
        btn_open_fs = QPushButton("Abrir pasta…")
        btn_open_fs.clicked.connect(self.load_hierarchy_dialog)
        h_tabs.addWidget(btn_new_tab)
        h_tabs.addWidget(btn_open_fs)
        h_tabs.addStretch(1)
        v.addLayout(h_tabs)

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

    def _cur_page(self) -> Optional['_Page']:
        w = self.tabs.currentWidget()
        return w if isinstance(w, HierarchyTab._Page) else None

    def load_hierarchy_dialog(self):
        base = _choose_directory(self, "Escolher pasta para abrir")
        if not base:
            return
        pg = self._cur_page()
        is_empty = bool(pg and pg.tree.topLevelItemCount() == 0)
        title = Path(base).name or "Hierarquia"
        if is_empty:
            self._begin_busy(f"Abrindo “{title}”…")
            try:
                pg.base_edit.setText(base)
                self._load_fs_into_page(pg, base)
                idx = self.tabs.indexOf(pg)
                if idx != -1:
                    self.tabs.setTabText(idx, title)
            finally:
                self._end_busy()
        else:
            self._add_tab_from_fs(base)

    def _load_fs_into_page(self, page: '_Page', base_dir: str):
        try:
            p = Path(base_dir)
            if not p.exists() or not p.is_dir():
                raise FileNotFoundError("Pasta inválida")
            root = self._make_dir_item(page, p.name, str(p))
            page.tree.addTopLevelItem(root)
            self._ensure_buttons(page, root)
            root.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))
            page.tree.expandItem(root)
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao abrir: {e}")

    def _make_fs_item(self, page: '_Page', name: str, full_path: str, is_dir: bool) -> QTreeWidgetItem:
        it = self._make_item(page, name, is_dir=is_dir)
        it.setData(0, self.ROLE_FULLPATH, full_path)
        it.setData(0, self.ROLE_IS_DIR, 1 if is_dir else 0)
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
                dirs, files = [], []
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=True):
                            dirs.append(e)
                        elif e.is_file(follow_symlinks=True):
                            files.append(e)
                    except Exception:
                        continue
            dirs.sort(key=lambda e: e.name.lower())
            files.sort(key=lambda e: e.name.lower())

            created_dirs = 0
            for idx, e in enumerate(dirs, 1):
                child = self._make_dir_item(page, e.name, e.path)
                parent_item.addChild(child)
                self._ensure_buttons(page, child)
                child.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))
                created_dirs += 1
                self._pump_events_periodically(idx, every=64)

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
        if it.childCount() == 1 and it.child(0).text(0) == self.DUMMY_MARK:
            it.takeChildren()
            pg = self._page_of_item(it)
            if not pg:
                return
            base = it.data(0, self.ROLE_FULLPATH)
            if not base:
                return
            self._begin_busy(f"Lendo “{Path(base).name}”…")
            try:
                self._load_children_for_item(pg, it, str(base))
            finally:
                self._end_busy()

    def _on_item_collapsed(self, it: QTreeWidgetItem):
        self._free_subtree_widgets(it)
        has_sub = bool(it.data(0, self.ROLE_HAS_SUBDIRS))
        it.takeChildren()
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

    # ----------------- Grupo de Templates -----------------
    def _build_templates_group(self) -> QGroupBox:
        g = QGroupBox("Templates de Hierarquia (compartilhados)")
        lay = QVBoxLayout(g)

        h1 = QHBoxLayout()
        self.cb_templates = QComboBox()
        self.cb_templates.setEditable(False)
        self.cb_templates.setSizeAdjustPolicy(QComboBox.AdjustToContents)

        btn_apply = QPushButton("Aplicar nos pais selecionados (aba ativa)")
        btn_apply.clicked.connect(self.apply_selected_template_to_selected_parents)

        h1.addWidget(self.cb_templates, stretch=1)
        h1.addWidget(btn_apply)
        lay.addLayout(h1)

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

        h3 = QHBoxLayout()
        h3.addStretch(1)
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
        b_plus.setVisible(is_dir)

        b_more = QPushButton("⋯")
        b_more.setFixedWidth(24)

        def persistent_menu():
            menu = QMenu(self)

            act_sib = menu.addAction("+ Irmão")
            act_child = menu.addAction("+ Filho")
            act_child.setEnabled(is_dir)
            act_rename = menu.addAction("Renomear")
            act_rename_replace = menu.addAction("Renomear (replace…)")

            act_sel_one = menu.addAction("Selecionar")
            act_sel_children = menu.addAction("Selecionar todas as filhas")
            act_sel_siblings = menu.addAction("Selecionar todas as irmãs")

            act_del = menu.addAction("Remover")

            chosen = menu.exec(b_more.mapToGlobal(QPoint(0, b_more.height())))
            if not chosen:
                return

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
                dlg.exec()
            elif chosen == act_sel_one:
                pg = self._page_of_item(item)
                pg.tree.clearSelection()
                item.setSelected(True)
            elif chosen == act_sel_children:
                pg = self._page_of_item(item)
                pg.tree.clearSelection()
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
        for idx in range(self.tabs.count()):
            pg = self.tabs.widget(idx)
            if isinstance(pg, HierarchyTab._Page):
                if it.treeWidget() is pg.tree:
                    return pg
        return self._cur_page()

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

    # -------------- Ações da ÁRVORE --------------
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

    # -------------- Serialização & criação no disco --------------
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

    def _find_child_by_name(self, parent_item: QTreeWidgetItem, name: str) -> Optional[QTreeWidgetItem]:
        for i in range(parent_item.childCount()):
            if parent_item.child(i).text(0) == name:
                return parent_item.child(i)
        return None

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

class _FilterRow(QWidget):
    TYPES = ["Resolução (maior lado)", "Extensão", "Perfil de cor", "Data"]

    def __init__(self, parent=None, initial_type="extensão", initial_values=""):
        super().__init__(parent)

        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(4)

        self.type_cb = QComboBox()
        self.type_cb.addItems(self.TYPES)
        self.type_cb.setMinimumContentsLength(14)
        self.type_cb.setFixedHeight(28)

        # 🔧 Seleção do tipo, tolerante a variações (minúsculas, prefixos)
        wanted = (initial_type or "").strip().lower()
        if wanted:
            for t in self.TYPES:
                if t.lower().startswith(wanted):
                    self.type_cb.setCurrentText(t)
                    break

        self.values_edit = QLineEdit()
        self.values_edit.setPlaceholderText("valores separados por vírgula")
        self.values_edit.setFixedHeight(28)
        # 🔧 PREENCHE os valores iniciais vindos do template
        self.values_edit.setText(initial_values)

        self.btn_remove = QToolButton()
        self.btn_remove.setText("−")
        self.btn_remove.setToolTip("Remover filtro")
        self.btn_remove.setAutoRaise(True)
        self.btn_remove.setFixedSize(22, 22)

        self.type_cb.setStyleSheet("QComboBox{padding:2px 6px;}")
        self.values_edit.setStyleSheet("QLineEdit{padding:2px 6px;}")
        self.btn_remove.setStyleSheet("QToolButton{padding:0px;}")

        g.addWidget(self.type_cb,    0, 0)
        g.addWidget(self.values_edit,0, 1)
        g.addWidget(self.btn_remove, 0, 2)

        g.setColumnStretch(0, 0)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(2, 0)

    def spec(self) -> Tuple[str, List[str]]:
        ftype = self.type_cb.currentText().strip().lower()
        raw = self.values_edit.text()
        vals = [v.strip() for v in raw.split(",") if v.strip()]
        return ftype, vals


class PeneratorDialog(QDialog):
    def _normalize_type_label(self, label: str) -> str:
        # Mapeia rótulos salvos (minúsculos, variações) para os oficiais do combo
        for t in _FilterRow.TYPES:
            if t.lower().startswith(label.strip().lower()):
                return t
        return _FilterRow.TYPES[0]

    def _collect_current_filters(self) -> List[Tuple[str, List[str]]]:
        specs: List[Tuple[str, List[str]]] = []
        for i in range(self.rows_box.count()):
            w = self.rows_box.itemAt(i).widget()
            if isinstance(w, _FilterRow):
                t, vals = w.spec()
                if vals:
                    specs.append((t, vals))
        return specs

    def _set_rows_from_spec(self, spec: List[Dict[str, Any]]):
        # Limpa linhas atuais
        while self.rows_box.count():
            item = self.rows_box.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        # Reconstrói
        for entry in spec:
            t = self._normalize_type_label(str(entry.get("type", "")))
            vals = ",".join(entry.get("values", []))
            self._add_row(t, vals)
        if self.rows_box.count() == 0:
            self._add_row("Extensão", "")
            self._add_row("Resolução (maior lado)", "")

    # -------- Persistência dos templates (JSON) --------
    def _load_filter_templates(self):
        self._filter_templates: Dict[str, List[Dict[str, Any]]] = {}
        try:
            fp = _filter_templates_json_path()
            if fp.exists():
                data = json.loads(fp.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # normaliza para [{type:str, values:[...]}]
                    fixed: Dict[str, List[Dict[str, Any]]] = {}
                    for name, arr in data.items():
                        if not isinstance(name, str) or not isinstance(arr, list):
                            continue
                        entries: List[Dict[str, Any]] = []
                        for e in arr:
                            if isinstance(e, dict):
                                typ = str(e.get("type", "extensão"))
                                vals = e.get("values", [])
                                if isinstance(vals, list):
                                    vals = [str(x) for x in vals]
                                else:
                                    vals = [str(vals)]
                                entries.append({"type": typ, "values": vals})
                            elif isinstance(e, (list, tuple)) and len(e) == 2:
                                typ = str(e[0])
                                vals = e[1] if isinstance(e[1], list) else [str(e[1])]
                                entries.append({"type": typ, "values": [str(v) for v in vals]})
                        if entries:
                            fixed[name] = entries
                    self._filter_templates = fixed
        except Exception:
            self._filter_templates = {}

    def _save_filter_templates(self):
        try:
            fp = _filter_templates_json_path()
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps(self._filter_templates, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _refresh_filter_templates_combo(self, select_name: Optional[str] = None):
        self.cb_filter_templates.blockSignals(True)
        cur = self.cb_filter_templates.currentText()
        self.cb_filter_templates.clear()
        names = sorted(self._filter_templates.keys())
        self.cb_filter_templates.addItems(names)
        if select_name and select_name in self._filter_templates:
            self.cb_filter_templates.setCurrentText(select_name)
        elif cur in self._filter_templates:
            self.cb_filter_templates.setCurrentText(cur)
        self.cb_filter_templates.blockSignals(False)

    def _apply_selected_filter_template(self):
        name = self.cb_filter_templates.currentText().strip()
        if not name or name not in self._filter_templates:
            QMessageBox.information(self, "Templates de filtros", "Selecione um template.")
            return
        self._set_rows_from_spec(self._filter_templates[name])

    def _save_current_filters_as_template(self):
        name, ok = QInputDialog.getText(self, "Salvar como template de filtros", "Nome do template:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._filter_templates:
            if QMessageBox.question(self, "Sobrescrever?", f"Já existe '{name}'. Substituir?") != QMessageBox.Yes:
                return
        # coleta → normaliza para [{type, values}]
        entries = []
        for t, vals in self._collect_current_filters():
            entries.append({"type": t, "values": vals})
        if not entries:
            QMessageBox.information(self, "Templates de filtros", "Não há filtros para salvar.")
            return
        self._filter_templates[name] = entries
        self._save_filter_templates()
        self._refresh_filter_templates_combo(select_name=name)
        QMessageBox.information(self, "Templates de filtros", "Template salvo.")

    def _edit_current_filter_template(self):
        name = self.cb_filter_templates.currentText().strip()
        if not name or name not in self._filter_templates:
            QMessageBox.information(self, "Editar template", "Selecione um template.")
            return
        # Representação textual simples: "tipo: v1,v2"
        lines = []
        for e in self._filter_templates[name]:
            lines.append(f"{e.get('type','')}: {','.join(e.get('values', []))}")
        text_init = "\n".join(lines)
        text, ok = QInputDialog.getMultiLineText(
            self, f"Editar template: {name}",
            "Um por linha (ex.: 'extensão: jpg,png'):\nTipos válidos: " + ", ".join(_FilterRow.TYPES),
            text_init
        )
        if not ok:
            return
        # parse
        new_entries: List[Dict[str, Any]] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                t, vals = line.split(":", 1)
                t = self._normalize_type_label(t.strip())
                vals_list = [v.strip() for v in vals.split(",") if v.strip()]
                if vals_list:
                    new_entries.append({"type": t, "values": vals_list})
        if not new_entries:
            QMessageBox.information(self, "Editar template", "Nenhuma linha válida.")
            return
        self._filter_templates[name] = new_entries
        self._save_filter_templates()
        QMessageBox.information(self, "Templates de filtros", "Template atualizado e salvo.")

    def _delete_current_filter_template(self):
        name = self.cb_filter_templates.currentText().strip()
        if not name or name not in self._filter_templates:
            return
        if QMessageBox.question(self, "Remover template", f"Remover '{name}'?") == QMessageBox.Yes:
            del self._filter_templates[name]
            self._save_filter_templates()
            self._refresh_filter_templates_combo()
            QMessageBox.information(self, "Templates de filtros", "Template removido.")
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("File Penerator — Filtros")

        # layout principal mais “justo”
        v = QVBoxLayout(self)
        tpl_bar = QHBoxLayout()
        tpl_bar.setContentsMargins(0, 0, 0, 0)
        tpl_bar.setSpacing(6)

        self.cb_filter_templates = QComboBox()
        self.cb_filter_templates.setMinimumContentsLength(20)

        btn_tpl_apply = QPushButton("Aplicar")
        btn_tpl_save  = QPushButton("Salvar como…")
        btn_tpl_edit  = QPushButton("Editar…")
        btn_tpl_del   = QPushButton("Remover")

        btn_tpl_apply.clicked.connect(self._apply_selected_filter_template)
        btn_tpl_save.clicked.connect(self._save_current_filters_as_template)
        btn_tpl_edit.clicked.connect(self._edit_current_filter_template)
        btn_tpl_del.clicked.connect(self._delete_current_filter_template)

        tpl_bar.addWidget(QLabel("Template:"))
        tpl_bar.addWidget(self.cb_filter_templates, 1)
        tpl_bar.addWidget(btn_tpl_apply)
        tpl_bar.addWidget(btn_tpl_save)
        tpl_bar.addWidget(btn_tpl_edit)
        tpl_bar.addWidget(btn_tpl_del)

        v.addLayout(tpl_bar)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        # Container de linhas com margens mínimas
        rows_wrap = QWidget(self)
        self.rows_box = QVBoxLayout(rows_wrap)
        self.rows_box.setContentsMargins(0, 0, 0, 0)
        self.rows_box.setSpacing(6)

        # linhas padrão
        self._add_row("extensão", "jpg,png")
        self._add_row("resolução (maior lado)", "")

        # barra de adicionar mais filtros (compacta)
        add_bar = QHBoxLayout()
        add_bar.setContentsMargins(0, 0, 0, 0)
        add_bar.setSpacing(6)

        self.btn_add = QPushButton("+ adicionar filtro")
        self.btn_add.setFixedHeight(26)
        self.btn_add.clicked.connect(lambda: self._add_row())

        add_bar.addWidget(self.btn_add)
        add_bar.addStretch(1)

        # rodapé de opções (compacto)
        self.cb_replicate = QCheckBox("Após mover, perguntar onde replicar somente a estrutura")
        self.cb_concat_suffix = QCheckBox("Concatenar filtros ao nome do arquivo")
        for cb in (self.cb_replicate, self.cb_concat_suffix):
            cb.setStyleSheet("QCheckBox{spacing:6px;}")

        # botões OK/Cancel pequenos
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        for b in btns.buttons():
            b.setFixedHeight(26)

        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        # monta
        v.addWidget(rows_wrap)
        v.addLayout(add_bar)
        v.addWidget(self.cb_replicate)
        v.addWidget(self.cb_concat_suffix)
        v.addWidget(btns)
        self._load_filter_templates()
        self._refresh_filter_templates_combo()
        _center_dialog(self, parent)

    def _add_row(self, initial_type="extensão", initial_values=""):
        row = _FilterRow(self, initial_type, initial_values)
        self.rows_box.addWidget(row)
        row.btn_remove.clicked.connect(lambda: self._remove_row(row))

    def _remove_row(self, row: _FilterRow):
        row.setParent(None)
        row.deleteLater()

    def chosen(self) -> Tuple[List[Tuple[str, List[str]]], bool, bool]:
        filters: List[Tuple[str, List[str]]] = []
        for i in range(self.rows_box.count()):
            w = self.rows_box.itemAt(i).widget()
            if isinstance(w, _FilterRow):
                t, vals = w.spec()
                if vals:
                    filters.append((t, vals))
        return filters, self.cb_replicate.isChecked(), self.cb_concat_suffix.isChecked()


class PeneratorTab(QWidget):
    """
    - Mostra FS de origem com lazy-load.
    - Botão 'Penerator' abre o diálogo de filtros dinâmicos (3 tipos).
    - Executa pipeline de filtros em ORDEM -> cria hierarquia aninhada no destino.
    - Atualiza a árvore da ORIGEM depois de mover.
    - Opções: replicar só a estrutura e concatenar rótulos no nome (exceto extensão).
    """
    ROLE_FULLPATH = Qt.UserRole + 1
    ROLE_HAS_SUBDIRS = Qt.UserRole + 2
    ROLE_IS_DIR = Qt.UserRole + 3
    DUMMY_MARK = "…"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy_timer: Optional[QTimer] = None
        self._progress: Optional[QProgressDialog] = None
        self._build_ui()

    # ---------- Busy/UI helpers ----------
    def _begin_busy(self, text: str = "Processando…"):
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
                _center_dialog(self._progress, self)
                QApplication.processEvents()

        self._busy_timer.timeout.connect(_show)
        self._busy_timer.start(300)

    def _end_busy(self):
        if self._busy_timer:
            try: self._busy_timer.stop()
            except Exception: pass
            self._busy_timer.deleteLater()
            self._busy_timer = None
        if self._progress:
            try: self._progress.close()
            except Exception: pass
            self._progress.deleteLater()
            self._progress = None

    def _pump(self, i: int, every: int = 64):
        if i % every == 0:
            QApplication.processEvents()

    # ---------- UI principal ----------
    def _build_ui(self):
        v = QVBoxLayout(self)

        # Header
        h = QHBoxLayout()
        self.base_edit = QLineEdit()
        self.base_edit.setPlaceholderText("Pasta de origem para penerator…")

        btn_open = QPushButton("Abrir pasta…")
        btn_open.clicked.connect(self._choose_and_open)

        btn_refresh = QPushButton("Atualizar")
        btn_refresh.clicked.connect(self._refresh_current_view)

        self.btn_penerator = QPushButton("Penerator")
        self.btn_penerator.setEnabled(False)
        self.btn_penerator.clicked.connect(self._run_penerator)

        h.addWidget(btn_open)
        h.addWidget(self.base_edit)
        h.addWidget(btn_refresh)
        h.addWidget(self.btn_penerator)
        v.addLayout(h)

        # Árvore com lazy-load
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Item", "Ações"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAnimated(True)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemCollapsed.connect(self._on_item_collapsed)
        v.addWidget(self.tree)

        # Clique no triângulo do expander mais amigável
        self.tree.viewport().installEventFilter(self)

    # ---------- Event filter (expander amigável) ----------
    def eventFilter(self, obj, ev):
        if obj is self.tree.viewport() and ev.type() == QEvent.MouseButtonPress:
            pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
            idx = self.tree.indexAt(pos)
            if idx.isValid():
                row_rect = self.tree.visualRect(idx)
                depth = 0
                p = idx.parent()
                while p.isValid():
                    depth += 1
                    p = p.parent()
                x0 = row_rect.left() + depth * self.tree.indentation()
                indicator_width = 18
                indicator_rect = QRect(x0, row_rect.top(), indicator_width, row_rect.height())
                if indicator_rect.contains(pos):
                    self.tree.setExpanded(idx, not self.tree.isExpanded(idx))
                    return True
        return super().eventFilter(obj, ev)

    # ---------- FS / Lazy-load ----------
    def _choose_and_open(self):
        base = _choose_directory(self, "Escolher pasta de origem")
        if not base:
            return
        self.base_edit.setText(base)
        self._load_root(base)
        self.btn_penerator.setEnabled(True)

    def _refresh_current_view(self):
        base = self.base_edit.text().strip()
        if base:
            self._load_root(base)

    def _load_root(self, base_dir: str):
        try:
            self.tree.clear()
            p = Path(base_dir)
            if not p.exists() or not p.is_dir():
                raise FileNotFoundError("Pasta inválida")
            root = self._make_dir_item(p.name, str(p))
            self.tree.addTopLevelItem(root)
            self._ensure_buttons(root)
            root.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))
            self.tree.expandItem(root)
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao abrir: {e}")

    def _make_item(self, name: str, is_dir: bool) -> QTreeWidgetItem:
        it = QTreeWidgetItem([name, ""])
        it.setFlags(it.flags() | Qt.ItemIsEditable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        it.setData(0, self.ROLE_IS_DIR, 1 if is_dir else 0)
        if is_dir:
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        try:
            icon = QApplication.style().standardIcon(QStyle.SP_DirIcon if is_dir else QStyle.SP_FileIcon)
            it.setIcon(0, icon)
        except Exception:
            pass
        return it

    def _make_dir_item(self, name: str, full_path: str) -> QTreeWidgetItem:
        it = self._make_item(name, True)
        it.setData(0, self.ROLE_FULLPATH, full_path)
        return it

    def _make_file_item(self, name: str, full_path: str) -> QTreeWidgetItem:
        it = self._make_item(name, False)
        it.setData(0, self.ROLE_FULLPATH, full_path)
        return it

    def _load_children(self, parent_item: QTreeWidgetItem, parent_path: str):
        try:
            with os.scandir(parent_path) as it:
                dirs, files = [], []
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=True):
                            dirs.append(e)
                        elif e.is_file(follow_symlinks=True):
                            files.append(e)
                    except Exception:
                        continue
            dirs.sort(key=lambda e: e.name.lower())
            files.sort(key=lambda e: e.name.lower())

            for i, d in enumerate(dirs, 1):
                ch = self._make_dir_item(d.name, d.path)
                parent_item.addChild(ch)
                self._ensure_buttons(ch)
                ch.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))
                self._pump(i)

            for i, f in enumerate(files, 1):
                ch = self._make_file_item(f.name, f.path)
                parent_item.addChild(ch)
                self._ensure_buttons(ch)
                self._pump(i)

            parent_item.setData(0, self.ROLE_HAS_SUBDIRS, 1 if dirs else 0)
        except Exception:
            parent_item.setData(0, self.ROLE_HAS_SUBDIRS, 0)

    def _on_item_expanded(self, it: QTreeWidgetItem):
        if it.childCount() == 1 and it.child(0).text(0) == self.DUMMY_MARK:
            it.takeChildren()
            base = it.data(0, self.ROLE_FULLPATH)
            if not base:
                return
            self._begin_busy(f"Lendo “{Path(base).name}”…")
            try:
                self._load_children(it, str(base))
            finally:
                self._end_busy()

    def _on_item_collapsed(self, it: QTreeWidgetItem):
        it.takeChildren()
        if bool(it.data(0, self.ROLE_HAS_SUBDIRS)):
            it.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))

    # ---------- Botões/Ações por item ----------
    def _ensure_buttons(self, item: QTreeWidgetItem):
        from PySide6.QtWidgets import QMenu, QWidget, QHBoxLayout
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)

        is_dir = bool(item.data(0, self.ROLE_IS_DIR))

        b_plus = QPushButton("+")
        b_plus.setFixedWidth(24)
        b_plus.clicked.connect(lambda: self.add_child(item))
        b_plus.setVisible(is_dir)

        b_more = QPushButton("⋯")
        b_more.setFixedWidth(24)

        def persistent_menu():
            menu = QMenu(self)
            act_sib = menu.addAction("+ Irmão")
            act_child = menu.addAction("+ Filho")
            act_child.setEnabled(is_dir)
            act_rename = menu.addAction("Renomear")
            act_del = menu.addAction("Remover (da árvore)")

            chosen = menu.exec(b_more.mapToGlobal(QPoint(0, b_more.height())))
            if not chosen:
                return
            if chosen == act_sib:
                self.add_sibling(item)
            elif chosen == act_child and is_dir:
                self.add_child(item)
            elif chosen == act_rename:
                idx = self.tree.indexFromItem(item, 0)
                self.tree.edit(idx)
            elif chosen == act_del:
                self.remove_item(item)

        b_more.clicked.connect(persistent_menu)

        h.addWidget(b_plus)
        h.addWidget(b_more)
        h.addStretch(1)
        self.tree.setItemWidget(item, 1, w)

    def add_child(self, ref: Optional[QTreeWidgetItem] = None):
        if ref is None:
            sel = self.tree.selectedItems()
            if not sel:
                return
            ref = sel[0]
        if not bool(ref.data(0, self.ROLE_IS_DIR)):
            QMessageBox.information(self, "Item é arquivo", "Arquivos não podem ter filhos. Selecione uma pasta.")
            return
        child = self._make_item("subpasta", True)
        ref.addChild(child)
        self._ensure_buttons(child)
        self.tree.expandItem(ref)
        self.tree.edit(self.tree.indexFromItem(child, 0))

    def add_sibling(self, ref: Optional[QTreeWidgetItem] = None):
        if ref is None:
            sel = self.tree.selectedItems()
            if not sel:
                return
            ref = sel[0]
        parent = ref.parent()
        sib = self._make_item("nova_pasta", True)
        if parent is None:
            self.tree.addTopLevelItem(sib)
        else:
            parent.addChild(sib)
        self._ensure_buttons(sib)
        self.tree.edit(self.tree.indexFromItem(sib, 0))

    def remove_item(self, it: QTreeWidgetItem):
        parent = it.parent()
        idx = (parent.indexOfChild(it) if parent else self.tree.indexOfTopLevelItem(it))
        if parent:
            parent.takeChild(idx)
        else:
            self.tree.takeTopLevelItem(idx)

    # ---------- Utilidades de imagem ----------
    @staticmethod
    def _is_image(path: Path) -> bool:
        return path.suffix.lower() in {
            ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif", ".jxl"
        }

    @staticmethod
    def _normalize_exif_datetime(dt: Any) -> Optional[str]:
        """
        Converte strings comuns de data do EXIF/XMP para 'YYYY-MM-DD'.
        Aceita:
        - 'YYYY:MM:DD HH:MM:SS'
        - 'YYYY-MM-DD' (com ou sem hora)
        - ISO 8601 parcial: 'YYYY-MM-DDTHH:MM:SS[.mmm]Z' etc.
        """
        try:
            s = str(dt).strip()
            if not s:
                return None

            # 1) EXIF clássico: 'YYYY:MM:DD HH:MM:SS'
            if len(s) >= 10 and s[4] in (":", "-") and s[7] in (":", "-"):
                y = s[0:4]
                m = s[5:7]
                d = s[8:10]
                if y.isdigit() and m.isdigit() and d.isdigit():
                    return f"{y}-{m}-{d}"

            # 2) ISO/XMP: tentar por regex
            import re
            m = re.search(r"(\d{4})[-:](\d{2})[-:](\d{2})", s)
            if m:
                y, mo, d = m.group(1), m.group(2), m.group(3)
                return f"{y}-{mo}-{d}"
        except Exception:
            pass
        return None

    @staticmethod
    def _get_largest_side_and_icc(path: Path) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """
        Retorna (maior_lado:int|None, icc_name:str|None, exif_date:'YYYY-MM-DD'|None)
        exif_date pode vir de: DateTimeOriginal, DateTimeDigitized, DateTime, ou XMP (CreateDate/ModifyDate).
        """
        exif_date = None
        icc_name = None
        largest = None
        try:
            with Image.open(str(path)) as im:
                w, h = im.size
                largest = max(w, h)
                icc_name = extract_icc_name(im.info.get("icc_profile"))

                # --- EXIF ---
                try:
                    exif = im.getexif()
                    if exif:
                        from PIL.ExifTags import TAGS
                        # Preferência: DateTimeOriginal (36867), depois Digitized (36868), depois DateTime (306)
                        preferred = {"DateTimeOriginal", "DateTimeDigitized", "DateTime"}
                        found: dict[str, Any] = {}
                        for k, v in exif.items():
                            tag = str(TAGS.get(k, k))
                            if tag in preferred:
                                found[tag] = v
                        for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                            if key in found:
                                exif_date = PeneratorTab._normalize_exif_datetime(found[key])
                                if exif_date:
                                    break
                except Exception:
                    pass

                # --- XMP (fallback) ---
                if not exif_date:
                    try:
                        xmp_bytes = im.info.get("xmp")
                        if xmp_bytes:
                            xmp_str = xmp_bytes.decode("utf-8", errors="ignore") if isinstance(xmp_bytes, (bytes, bytearray)) else str(xmp_bytes)
                            # procurar CreateDate/ModifyDate no XMP
                            import re
                            m = re.search(r"(CreateDate|ModifyDate)>([^<]+)<", xmp_str)
                            if m:
                                exif_date = PeneratorTab._normalize_exif_datetime(m.group(2))
                    except Exception:
                        pass
        except Exception:
            pass

        return largest, icc_name, exif_date


    def _scan_images(self, root: Path) -> Tuple[List[Path], Dict[Path, Tuple[Optional[int], Optional[str], Optional[str]]]]:
        files: List[Path] = []
        props: Dict[Path, Tuple[Optional[int], Optional[str], Optional[str]]] = {}
        idx = 0
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                p = Path(dirpath) / name
                if not self._is_image(p):
                    continue
                files.append(p)
                props[p] = self._get_largest_side_and_icc(p)
                idx += 1
                self._pump(idx, every=128)
        return files, props

    @staticmethod
    def _unique_target(dest_dir: Path, filename: str) -> Path:
        base = dest_dir / filename
        if not base.exists():
            return base
        stem = base.stem
        suffix = base.suffix
        i = 1
        while True:
            cand = dest_dir / f"{stem}_{i}{suffix}"
            if not cand.exists():
                return cand
            i += 1

    def _replicate_structure(self, source_root: Path, target_root: Path):
        for dirpath, dirnames, _ in os.walk(source_root):
            rel = Path(dirpath).relative_to(source_root)
            tgt = target_root / rel
            try:
                tgt.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    # ---------- Helpers de rótulo/sufixo ----------
    @staticmethod
    def _suffix_from_resolution_label(label: str) -> str:
        """
        Transforma '4000' -> '4k' se múltiplo de 1000; senão '4000px'.
        """
        try:
            n = int(label)
            return f"{n//1000}k" if n % 1000 == 0 else f"{n}px"
        except Exception:
            return label

    @staticmethod
    def _normalize_input_date_token(token: str) -> List[str]:
        """
        Normaliza um token digitado pelo usuário para formas comparáveis.
        Retorna lista de representações aceitas (com e sem '-').
        Exemplos:
        '2024'      -> ['2024']
        '2024-09'   -> ['2024-09', '202409']
        '202409'    -> ['2024-09', '202409']
        '2025-09-24'-> ['2025-09-24', '20250924']
        '20250924'  -> ['2025-09-24', '20250924']
        """
        s = token.strip()
        if not s:
            return []
        s_digits = "".join(ch for ch in s if ch.isdigit())
        out = set()

        def add_year(y):
            if len(y) == 4:
                out.add(y)

        def add_year_month(y, m):
            if len(y) == 4 and len(m) == 2:
                out.add(f"{y}-{m}")
                out.add(f"{y}{m}")

        def add_full(y, m, d):
            if len(y) == 4 and len(m) == 2 and len(d) == 2:
                out.add(f"{y}-{m}-{d}")
                out.add(f"{y}{m}{d}")

        if len(s_digits) == 4:
            add_year(s_digits)
        elif len(s_digits) == 6:
            y, mo = s_digits[:4], s_digits[4:]
            add_year_month(y, mo)
        elif len(s_digits) == 8:
            y, mo, d = s_digits[:4], s_digits[4:6], s_digits[6:]
            add_full(y, mo, d)
        else:
            # Tentar interpretar com hifens se vier 'YYYY-MM' ou 'YYYY-MM-DD'
            parts = s.replace("/", "-").split("-")
            if len(parts) == 2 and all(parts):
                y, mo = parts[0], parts[1]
                if len(y) == 4 and len(mo) == 2:
                    add_year_month(y, mo)
            elif len(parts) == 3 and all(parts):
                y, mo, d = parts[0], parts[1], parts[2]
                if len(y) == 4 and len(mo) == 2 and len(d) == 2:
                    add_full(y, mo, d)

        return list(out)

    @staticmethod
    def _date_candidates_for_path(exif_ymd: Optional[str], mtime_epoch: float) -> Set[str]:
        """
        Gera candidatos de comparação a partir de EXIF (YYYY-MM-DD) e mtime do arquivo.
        Inclui formas 'YYYY', 'YYYY-MM'/'YYYYMM', 'YYYY-MM-DD'/'YYYYMMDD'.
        """
        cands: Set[str] = set()
        # EXIF
        try:
            if exif_ymd:
                y, m, d = exif_ymd.split("-")
                cands.add(y)
                cands.add(f"{y}-{m}"); cands.add(f"{y}{m}")
                cands.add(f"{y}-{m}-{d}"); cands.add(f"{y}{m}{d}")
        except Exception:
            pass
        # mtime
        try:
            import datetime as _dt
            dt = _dt.datetime.fromtimestamp(mtime_epoch)
            y, m, d = f"{dt.year:04d}", f"{dt.month:02d}", f"{dt.day:02d}"
            cands.add(y)
            cands.add(f"{y}-{m}"); cands.add(f"{y}{m}")
            cands.add(f"{y}-{m}-{d}"); cands.add(f"{y}{m}{d}")
        except Exception:
            pass
        return cands
    # ---------- Execução do Penerator ----------
    def _run_penerator(self):
        base = self.base_edit.text().strip()
        if not base:
            QMessageBox.information(self, "Penerator", "Escolha primeiro a pasta de origem.")
            return
        root = Path(base)
        if not root.exists() or not root.is_dir():
            QMessageBox.warning(self, "Penerator", "Pasta de origem inválida.")
            return

        # 1) Filtros dinâmicos
        dlg = PeneratorDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        filters, ask_replicate, concat_suffix = dlg.chosen()
        if not filters:
            QMessageBox.information(self, "Penerator", "Adicione ao menos um filtro com valores.")
            return

        # 2) Escanear imagens
        self._begin_busy("Escaneando imagens…")
        try:
            files, props = self._scan_images(root)
        finally:
            self._end_busy()
        if not files:
            QMessageBox.information(self, "Penerator", "Nenhuma imagem encontrada nesta pasta.")
            return

        # 3) Destino principal
        dest = _choose_directory(self, "Escolher pasta DESTINO (hierarquia pelos filtros)")
        if not dest:
            return
        dest_root = Path(dest)
        dest_root.mkdir(parents=True, exist_ok=True)

        # 4) Função de match por tipo
        def match_and_label(p: Path, info: Tuple[Optional[int], Optional[str], Optional[str]], ftype: str, vals: List[str]) -> Optional[str]:
            """
            Se o arquivo p 'passa' no filtro (ftype, vals), retorna o rótulo (string) a ser usado
            como nome de pasta/sufixo para esse nível. Caso contrário, None.

            info = (largest:int|None, icc_name:str|None, exif_ymd:'YYYY-MM-DD'|None)
            """
            ftype = ftype.lower()

            # extrai de forma segura (compatível com futuras mudanças)
            largest = info[0] if len(info) > 0 else None
            icc_name = info[1] if len(info) > 1 else None
            exif_ymd = info[2] if len(info) > 2 else None

            if ftype.startswith("resolução"):  # maior lado
                try:
                    targets = {int(v) for v in vals}
                except Exception:
                    return None
                if largest is None:
                    return None
                return str(largest) if largest in targets else None

            if ftype == "extensão":
                ext = p.suffix.lower().lstrip(".")
                wanted = {v.lower().lstrip(".") for v in vals}
                return ext if ext in wanted else None

            if ftype.startswith("perfil"):  # perfil de cor
                if not icc_name:
                    return None
                icc_lower = icc_name.lower()
                for v in vals:
                    if v.lower() in icc_lower:
                        # usa o próprio token digitado como rótulo
                        return v
                return None

            if ftype.startswith("data"):  # data (EXIF/arquivo)
                try:
                    mtime = p.stat().st_mtime
                except Exception:
                    mtime = 0.0
                file_date_cands = self._date_candidates_for_path(exif_ymd, mtime)
                for v in vals:
                    for norm in self._normalize_input_date_token(v):
                        if norm in file_date_cands:
                            return v  # rótulo = token digitado
                return None

            return None

        # 5) Aplicar pipeline (ordem = profundidade da hierarquia)
        moved = 0
        skipped = 0

        self._begin_busy("Organizando arquivos conforme filtros…")
        try:
            for i, p in enumerate(files, 1):
                info = props.get(p, (None, None))
                labels: List[str] = []
                ok = True
                for ftype, vals in filters:
                    lab = match_and_label(p, info, ftype, vals)
                    if lab is None:
                        ok = False
                        break
                    labels.append(lab)

                if not ok:
                    skipped += 1
                    self._pump(i, every=64)
                    continue

                # monta destino aninhado
                cur_dir = dest_root
                for lab in labels:
                    safe = lab.replace("/", "-")
                    cur_dir = cur_dir / safe
                try:
                    cur_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass

                # nome do arquivo (concatenado opcional)
                orig_stem = p.stem
                orig_suffix = p.suffix  # inclui o ponto

                new_stem = orig_stem
                if concat_suffix:
                    # Para cada filtro NÃO-EXTENSÃO, derivar um sufixo curto
                    suffix_tokens: List[str] = []
                    for (ftype, _vals), lab in zip(filters, labels):
                        ftype = ftype.lower()
                        if ftype == "extensão":
                            continue  # nunca concatenar extensão
                        if ftype.startswith("resolução"):
                            suffix_tokens.append(self._suffix_from_resolution_label(lab))
                        elif ftype.startswith("perfil"):
                            suffix_tokens.append(self._suffix_from_profile_label(lab))
                        elif ftype.startswith("data"):
                            comp = "".join(ch for ch in lab if ch.isdigit())
                            if len(comp) in (4, 6, 8):
                                suffix_tokens.append(f"d{comp}")
                            else:
                                import re
                                suffix_tokens.append("d" + re.sub(r"[^0-9a-zA-Z]+", "", lab))

                    if suffix_tokens:
                        new_stem = f"{orig_stem}_{'_'.join(suffix_tokens)}"

                target_name = f"{new_stem}{orig_suffix}"
                target = self._unique_target(cur_dir, target_name)

                try:
                    shutil.move(str(p), str(target))
                    moved += 1
                except Exception:
                    skipped += 1

                self._pump(i, every=32)
        finally:
            self._end_busy()

        QMessageBox.information(
            self,
            "Penerator",
            f"Arquivos movidos: {moved}\nIgnorados (não passaram nos filtros ou falha): {skipped}"
        )

        # 6) Atualiza a árvore da ORIGEM
        self._refresh_current_view()

        # 7) Replicar estrutura (opcional)
        if moved and ask_replicate:
            apply_dir = _choose_directory(self, "Aplicar (replicar) SOMENTE a ESTRUTURA em…")
            if apply_dir:
                try:
                    self._begin_busy("Replicando estrutura…")
                    self._replicate_structure(dest_root, Path(apply_dir))
                finally:
                    self._end_busy()
                QMessageBox.information(self, "Penerator", "Estrutura replicada com sucesso.")

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


def main():
    app = QApplication(sys.argv)
    install_dialog_normalizer(app, 500, 300)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
