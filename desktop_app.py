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
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView, QLineEdit,
    QMessageBox, QStyle, QDialog
)


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
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
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


# -------- Substitua a classe ReplaceDialog (pode permanecer como estava, mas deixei idêntica e com .values) ----
class _ReplaceDialog(QDialog):
    def __init__(self, parent=None, initial_find: str = "", initial_repl: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Renomear (replace)")
        self.setModal(True)

        v = QVBoxLayout(self)

        row = QHBoxLayout()
        self.ed_find = QLineEdit(initial_find)
        self.ed_find.setPlaceholderText("Texto a buscar…")
        arrow = QLabel("→")
        arrow.setAlignment(Qt.AlignCenter)
        self.ed_repl = QLineEdit(initial_repl)
        self.ed_repl.setPlaceholderText("Substituir por… (pode ser vazio)")

        row.addWidget(self.ed_find, 1)
        row.addWidget(arrow)
        row.addWidget(self.ed_repl, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        v.addLayout(row)
        v.addWidget(btns)

        # Enter confirma
        self.ed_repl.returnPressed.connect(self.accept)
        self.ed_find.setFocus()

    def values(self) -> tuple[str, str]:
        return self.ed_find.text(), self.ed_repl.text()
    
from pathlib import Path
from PySide6.QtWidgets import QMessageBox, QTreeWidgetItem

def apply_replace_to_targets(owner, targets: list, find_text: str, repl_text: str) -> list:
    """
    Aplica replace em todos os QTreeWidgetItem de `targets`.
    - owner: objeto que possui .tree (QTreeWidget) e ROLE_FULLPATH constante.
    - Retorna: lista de action dicts com info necessária para undo.
    """
    actions = []
    if not find_text:
        QMessageBox.information(owner, "Replace", "Texto a buscar vazio — nada feito.")
        return actions

    for item in targets:
        try:
            old_name = item.text(0)
            if find_text not in old_name:
                continue

            # snapshot da subárvore: guardar ROLE_FULLPATH antigos para todos os nós da subárvore
            subtree_snapshot = []
            def _collect_subtree(node):
                subtree_snapshot.append((node, node.data(0, getattr(owner, "ROLE_FULLPATH", None))))
                for i in range(node.childCount()):
                    _collect_subtree(node.child(i))
            _collect_subtree(item)

            new_name_base = old_name.replace(find_text, repl_text)

            parent = item.parent()
            sibling_names = set()
            if parent is None:
                for i in range(owner.tree.topLevelItemCount()):
                    sib = owner.tree.topLevelItem(i)
                    if sib is not item:
                        sibling_names.add(sib.text(0))
            else:
                for i in range(parent.childCount()):
                    sib = parent.child(i)
                    if sib is not item:
                        sibling_names.add(sib.text(0))

            candidate = new_name_base
            idx = 1
            while candidate in sibling_names:
                candidate = f"{new_name_base}_{idx}"
                idx += 1
            new_name = candidate

            full = item.data(0, getattr(owner, "ROLE_FULLPATH", None))
            old_path = None
            if full:
                try:
                    old_path = Path(str(full))
                except Exception:
                    old_path = None

            # preparar action base (para undo)
            action = {
                "type": "rename",
                "item_ref": item,          # guardamos a referência do item (válida enquanto o nó existir)
                "old_name": old_name,
                "new_name": new_name,
                "old_path": str(old_path) if old_path else None,
                "new_path": None,
                "subtree_snapshot": [(id(n), v) for (n, v) in subtree_snapshot],  # ids e fp antigos
            }

            # se existe no disco -> renomear no disco e atualizar ROLE_FULLPATH dos filhos
            final_new = None
            if old_path and old_path.exists():
                try:
                    new_path = old_path.with_name(new_name)
                    final_new = new_path
                    i = 1
                    while final_new.exists():
                        final_new = new_path.with_name(f"{new_path.stem}_{i}{new_path.suffix}")
                        i += 1
                    os.rename(str(old_path), str(final_new))
                    # atualizar UI
                    item.setText(0, final_new.name)
                    item.setData(0, getattr(owner, "ROLE_FULLPATH", 0), str(final_new))

                    # atualizar filhos ROLE_FULLPATH (relativo ao old_path)
                    def _update_subtree(node, old_pref: Path, new_pref: Path):
                        for j in range(node.childCount()):
                            ch = node.child(j)
                            ch_fp = ch.data(0, getattr(owner, "ROLE_FULLPATH", None))
                            if ch_fp:
                                try:
                                    ch_path = Path(str(ch_fp))
                                    rel = ch_path.relative_to(old_pref)
                                    ch.setData(0, getattr(owner, "ROLE_FULLPATH", 0), str(new_pref / rel))
                                except Exception:
                                    pass
                            _update_subtree(ch, old_pref, new_pref)
                    try:
                        _update_subtree(item, old_path, final_new)
                    except Exception:
                        pass

                    action["new_path"] = str(final_new)
                except Exception:
                    # se falhar o rename no disco, tentar apenas renomear na árvore
                    item.setText(0, new_name)
                    if old_path:
                        try:
                            item.setData(0, getattr(owner, "ROLE_FULLPATH", 0), str(old_path.with_name(new_name)))
                        except Exception:
                            pass
            else:
                # não existe no disco -> só alterar UI
                item.setText(0, new_name)
                if old_path:
                    try:
                        item.setData(0, getattr(owner, "ROLE_FULLPATH", 0), str(old_path.with_name(new_name)))
                    except Exception:
                        pass

            actions.append(action)

        except Exception:
            # ignorar item problemático e continuar
            continue

    if actions:
        QMessageBox.information(owner, "Replace", f"Renomeados: {len(actions)} (veja undo para reverter).")
    return actions

# ---- Helpers que o módulo original provavelmente tinha em utilidades ----
def _choose_directory(parent: QWidget, title: str) -> Optional[str]:
    
    from PySide6.QtWidgets import QFileDialog
    d = QFileDialog.getExistingDirectory(parent, title)
    return d if d else None


class HierarchyTab(QWidget):
    """
    Hierarquia reativa:
    - Abrir pasta (lazy-load)
    - Criar / Renomear / Excluir pastas REATIVAMENTE (operações no disco imediatamente)
    - Botões '+ / ⋯' por nó e atalhos rápidos
    - Reverter a última operação (create/delete/rename)
    /*************  ✨ Windsurf Command ⭐  *************/
    """
    """
/*******  3482aeea-9fde-4621-bcf3-ee9da6804f80  *******/- REMOVIDO: todo o suporte a templates
    """

    ROLE_FULLPATH = Qt.UserRole + 1
    ROLE_HAS_SUBDIRS = Qt.UserRole + 2
    ROLE_IS_DIR = Qt.UserRole + 3

    DUMMY_MARK = "…"  # usado para lazy-load

    def __init__(self, parent=None):
        super().__init__(parent)
        self._vp2page: Dict[object, 'HierarchyTab._Page'] = {}
        self._last_op: Optional[Dict[str, Any]] = None  # armazena somente a ÚLTIMA operação
        self._build_ui()
        self._add_empty_tab()

    # ----------------- UI / tabs -----------------
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

        # Botão global de UNDO (reverter a última operação)
        btn_undo = QPushButton("Reverter última ação")
        btn_undo.clicked.connect(self.undo_last_operation)

        h_tabs.addWidget(btn_new_tab)
        h_tabs.addWidget(btn_open_fs)
        h_tabs.addStretch(1)
        h_tabs.addWidget(btn_undo)
        v.addLayout(h_tabs)
    def _ensure_lazy_placeholder(self, it: QTreeWidgetItem):
        """Garante que o item de pasta tenha um placeholder '…' para lazy-load."""
        if not bool(it.data(0, self.ROLE_IS_DIR)):
            return
        # evita duplicar dummy
        if it.childCount() == 1 and it.child(0).text(0) == self.DUMMY_MARK:
            return
        it.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))

    def _on_tab_close(self, idx: int):
        w = self.tabs.widget(idx)
        if w:
            w.deleteLater()
        self.tabs.removeTab(idx)

    def _add_empty_tab(self):
        page = self._Page(self)
        idx = self.tabs.addTab(page, "Hierarquia")
        self.tabs.setCurrentIndex(idx)

    # ----------------- Page (cada aba) -----------------
    class _Page(QWidget):
        def __init__(self, owner: 'HierarchyTab', base_path: Optional[str] = None):
            super().__init__(owner)
            self.owner = owner
            self._build_ui()
            if base_path:
                self.base_edit.setText(base_path)
                self.owner._load_root_into_page(self, base_path)

        def _build_ui(self):
            v = QVBoxLayout(self)
            h_base = QHBoxLayout()
            self.base_edit = QLineEdit()
            self.base_edit.setPlaceholderText("Pasta base desta aba…")
            btn_pick = QPushButton("Escolher…")
            btn_pick.clicked.connect(lambda: self.owner.pick_base())
            btn_load_fs = QPushButton("Abrir pasta…")
            btn_load_fs.clicked.connect(lambda: self.owner.load_hierarchy_dialog())
            h_base.addWidget(btn_load_fs)
            h_base.addWidget(self.base_edit)
            h_base.addWidget(btn_pick)
            v.addLayout(h_base)

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
            # Renomear reativo
            self.tree.itemChanged.connect(self.owner._on_item_changed)
            v.addWidget(self.tree)

            self.tree.viewport().installEventFilter(self.owner)
            self.owner._vp2page[self.tree.viewport()] = self

            # Controles rápidos da árvore (adicionar / remover)
            h_ops = QHBoxLayout()
            btn_add_root = QPushButton("+ Raiz")
            btn_add_root.clicked.connect(lambda: self.owner.add_root())
            btn_add_child = QPushButton("+ Filho")
            btn_add_child.clicked.connect(lambda: self.owner.add_child())
            btn_add_sibling = QPushButton("+ Irmão")
            btn_add_sibling.clicked.connect(lambda: self.owner.add_sibling())
            btn_remove = QPushButton("Remover")
            btn_remove.clicked.connect(lambda: self.owner.remove_selected())
            h_ops.addWidget(btn_add_root)
            h_ops.addWidget(btn_add_child)
            h_ops.addWidget(btn_add_sibling)
            h_ops.addWidget(btn_remove)
            h_ops.addStretch(1)
            v.addLayout(h_ops)

    def _cur_page(self) -> Optional['_Page']:
        w = self.tabs.currentWidget()
        return w if isinstance(w, HierarchyTab._Page) else None

    # ----------------- FS load / lazy -----------------
    def load_hierarchy_dialog(self):
        base = _choose_directory(self, "Escolher pasta para abrir")
        if not base:
            return
        pg = self._cur_page()
        # se página atual vazia -> carregar nela, senão criar nova aba
        if pg and pg.tree.topLevelItemCount() == 0:
            pg.base_edit.setText(base)
            self._load_root_into_page(pg, base)
            idx = self.tabs.indexOf(pg)
            if idx != -1:
                self.tabs.setTabText(idx, Path(base).name or "Hierarquia")
        else:
            # nova aba
            page = HierarchyTab._Page(self, base)
            idx = self.tabs.addTab(page, Path(base).name or "Hierarquia")
            self.tabs.setCurrentIndex(idx)

    def pick_base(self):
        pg = self._cur_page()
        if not pg:
            return
        base = _choose_directory(self, "Escolher pasta base")
        if base:
            pg.base_edit.setText(base)

    def _load_root_into_page(self, page: '_Page', base_dir: str):
        try:
            p = Path(base_dir)
            if not p.exists() or not p.is_dir():
                raise FileNotFoundError("Pasta inválida")
            page.tree.clear()
            root = self._make_dir_item(page, p.name or str(p), str(p))
            page.tree.addTopLevelItem(root)
            self._ensure_buttons(page, root)
            # adicionar placeholder para lazy
            root.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))
            page.tree.expandItem(root)
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Falha ao abrir: {e}")

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

            # pastas
            for d in dirs:
                ch = self._make_dir_item(page, d.name, d.path)
                parent_item.addChild(ch)
                self._ensure_buttons(page, ch)
                # sempre deixa dummy para permitir expandir e lazy-carregar conteúdo (dirs e/ou arquivos)
                self._ensure_lazy_placeholder(ch)

            # arquivos
            for f in files:
                ch = self._make_file_item(page, f.name, f.path)
                parent_item.addChild(ch)
                self._ensure_buttons(page, ch)

        except Exception:
            # não precisamos mais mexer em ROLE_HAS_SUBDIRS
            pass

    def _on_item_expanded(self, it: QTreeWidgetItem):
        pg = self._page_of_item(it)
        if not pg:
            return
        base = it.data(0, self.ROLE_FULLPATH)
        if not base:
            return

        should_lazy = (it.childCount() == 0) or (it.childCount() == 1 and it.child(0).text(0) == self.DUMMY_MARK)
        if not should_lazy:
            return

        # limpa dummy (se havia) e carrega
        it.takeChildren()
        self._begin_busy(f"Lendo “{Path(str(base)).name}”…")
        try:
            self._load_children_for_item(pg, it, str(base))
        finally:
            self._end_busy()

    def _on_item_collapsed(self, it: QTreeWidgetItem):
        pg = self._page_of_item(it)
        if not pg:
            return
        self._free_subtree_widgets(it)

        it.takeChildren()
        if bool(it.data(0, self.ROLE_IS_DIR)):
            self._ensure_lazy_placeholder(it)

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

    # ----------------- Criar / Renomear / Remover REATIVO (DISCO) -----------------
    def _get_disk_parent_path_for_item(self, parent_item: Optional[QTreeWidgetItem], page: '_Page') -> Optional[Path]:
        """
        Decide onde criar no disco:
         - se parent_item tem ROLE_FULLPATH -> usa ele,
         - se parent_item é None (top-level) -> usa page.base_edit (se preenchido),
         - senão sob ancestor com ROLE_FULLPATH, fallback para base da aba.
        """
        try:
            if parent_item is None:
                base_text = page.base_edit.text().strip()
                return Path(base_text) if base_text else None
            fp = parent_item.data(0, self.ROLE_FULLPATH)
            if fp:
                return Path(str(fp))
            # se parent_item top-level sem ROLE_FULLPATH -> usar base
            if parent_item.parent() is None:
                base_text = page.base_edit.text().strip()
                return Path(base_text) if base_text else None
            cur = parent_item.parent()
            while cur is not None:
                fp = cur.data(0, self.ROLE_FULLPATH)
                if fp:
                    return Path(str(fp))
                cur = cur.parent()
            base_text = page.base_edit.text().strip()
            return Path(base_text) if base_text else None
        except Exception:
            return None

    def _create_folder_on_disk(self, parent_path: Optional[Path], desired_name: str) -> Optional[Path]:
        """
        Cria uma pasta no disco com nome desired_name sob parent_path.
        Regras de colisão: se já existe arquivo com mesmo nome -> cria 'nome_dir', 'nome_dir_1', etc.
        Retorna Path criado/confirmado ou None se falhar.
        """
        if parent_path is None:
            return None
        try:
            parent_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        name = str(desired_name).strip()
        if not name:
            return None
        candidate = parent_path / name
        if candidate.exists() and candidate.is_dir():
            return candidate
        if candidate.exists() and candidate.is_file():
            base_dir = f"{name}_dir"
            alt = parent_path / base_dir
            i = 1
            while alt.exists():
                alt = parent_path / f"{base_dir}_{i}"
                i += 1
            try:
                alt.mkdir(parents=True, exist_ok=False)
                return alt
            except Exception:
                return None
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except Exception:
            i = 1
            while i <= 1000:
                alt = parent_path / f"{name}_{i}"
                try:
                    if not alt.exists():
                        alt.mkdir(parents=True, exist_ok=False)
                        return alt
                except Exception:
                    pass
                i += 1
            return None

    def add_root(self):
        pg = self._cur_page()
        if not pg:
            return
        desired_name = "nova_pasta"
        # UI
        item = self._make_item(pg, desired_name, is_dir=True)
        pg.tree.addTopLevelItem(item)
        self._ensure_buttons(pg, item)
        created_path = None
        parent_disk = self._get_disk_parent_path_for_item(None, pg)
        if parent_disk is None:
            QMessageBox.information(self, "Pasta base ausente",
                                    "Defina 'Pasta base desta aba' para que a pasta seja criada no disco. Item criado apenas na UI.")
        else:
            created = self._create_folder_on_disk(parent_disk, desired_name)
            if created:
                item.setData(0, self.ROLE_FULLPATH, str(created))
                created_path = str(created)
        # registrar última operação (create)
        self._last_op = {"type": "create", "path": created_path, "item": item}
        # editar inline imediatamente
        pg.tree.edit(pg.tree.indexFromItem(item, 0))

    def add_child(self, ref: Optional[QTreeWidgetItem] = None):
        pg = self._cur_page()
        if not pg:
            return
        if ref is None:
            sel = pg.tree.selectedItems()
            if not sel:
                QMessageBox.information(self, "Adicionar Filho", "Selecione uma pasta onde criar o filho.")
                return
            ref = sel[0]
        if not bool(ref.data(0, self.ROLE_IS_DIR)):
            QMessageBox.information(self, "Item é arquivo", "Arquivos não podem ter filhos. Selecione uma pasta.")
            return

        desired_name = "subpasta"
        parent_disk = self._get_disk_parent_path_for_item(ref, pg)
        created_path = None
        if parent_disk:
            created = self._create_folder_on_disk(parent_disk, desired_name)
            if created:
                created_path = str(created)
                child = self._make_item(pg, desired_name, is_dir=True)
                child.setData(0, self.ROLE_FULLPATH, created_path)
        else:
            child = self._make_item(pg, desired_name, is_dir=True)

        ref.addChild(child)
        self._ensure_buttons(pg, child)
        ref.setExpanded(True)
        # registrar última operação (create)
        self._last_op = {"type": "create", "path": created_path, "item": child}
        pg.tree.edit(pg.tree.indexFromItem(child, 0))

    def add_sibling(self, ref: Optional[QTreeWidgetItem] = None):
        pg = self._cur_page()
        if not pg:
            return
        if ref is None:
            sel = pg.tree.selectedItems()
            if not sel:
                QMessageBox.information(self, "Adicionar Irmão", "Selecione um item para inserir um irmão ao lado.")
                return
            ref = sel[0]
        parent = ref.parent()
        desired_name = "nova_pasta"
        sib = self._make_item(pg, desired_name, is_dir=True)
        created_path = None

        if parent is None:
            try:
                insert_at = pg.tree.indexOfTopLevelItem(ref) + 1
                pg.tree.insertTopLevelItem(insert_at, sib)
            except Exception:
                pg.tree.addTopLevelItem(sib)
            parent_disk = self._get_disk_parent_path_for_item(None, pg)
            if parent_disk:
                created = self._create_folder_on_disk(parent_disk, desired_name)
                if created:
                    sib.setData(0, self.ROLE_FULLPATH, str(created))
                    created_path = str(created)
            else:
                QMessageBox.information(self, "Pasta base ausente",
                                        "Defina 'Pasta base desta aba' para que a pasta seja criada no disco.")
        else:
            try:
                idx_ref = parent.indexOfChild(ref)
                insert_at = idx_ref + 1
                parent.insertChild(insert_at, sib)
            except Exception:
                parent.addChild(sib)
            parent_disk = self._get_disk_parent_path_for_item(parent, pg)
            if parent_disk:
                created = self._create_folder_on_disk(parent_disk, desired_name)
                if created:
                    sib.setData(0, self.ROLE_FULLPATH, str(created))
                    created_path = str(created)
            else:
                QMessageBox.information(self, "Pasta base ausente",
                                        "Não foi possível determinar pasta no disco para este nó. Item criado apenas na UI.")
        self._ensure_buttons(pg, sib)
        # registrar última operação (create)
        self._last_op = {"type": "create", "path": created_path, "item": sib}
        pg.tree.edit(pg.tree.indexFromItem(sib, 0))

    def remove_selected(self):
        pg = self._cur_page()
        if not pg:
            return
        sel = pg.tree.selectedItems()
        if not sel:
            return
        # para múltiplos, a última operação ficará registrada com base no último removido
        for it in sel:
            self.remove_item(it)

    def _trash_dir(self) -> Path:
        p = Path.home() / ".hierarchytab_trash"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _unique_in_dir(self, base_dir: Path, name: str) -> Path:
        cand = base_dir / name
        if not cand.exists():
            return cand
        stem = Path(name).stem
        suf = Path(name).suffix
        i = 1
        while True:
            alt = base_dir / f"{stem}_{i}{suf}"
            if not alt.exists():
                return alt
            i += 1

    def remove_item(self, it: QTreeWidgetItem):
        parent = it.parent()
        pg = self._page_of_item(it)
        if not pg:
            return

        full = it.data(0, self.ROLE_FULLPATH)
        is_dir = bool(it.data(0, self.ROLE_IS_DIR))

        # info para reverter UI (posição)
        if parent:
            idx_in_parent = parent.indexOfChild(it)
        else:
            idx_in_parent = pg.tree.indexOfTopLevelItem(it)

        # mover para "lixo" para permitir UNDO
        if full:
            p = Path(str(full))
            if p.exists():
                if is_dir:
                    txt = f"Remover a pasta no disco?\n\n{p}\n\n(Se a pasta não estiver vazia ela será movida para a lixeira interna para possível reversão.)"
                else:
                    txt = f"Remover o arquivo no disco?\n\n{p}\n\n(Ele será movido para a lixeira interna para possível reversão.)"
                resp = QMessageBox.question(self, "Confirmar remoção", txt, QMessageBox.Yes | QMessageBox.No)
                if resp != QMessageBox.Yes:
                    return
                try:
                    trash = self._trash_dir()
                    dst = self._unique_in_dir(trash, p.name)
                    shutil.move(str(p), str(dst))
                    # registrar última operação (delete)
                    self._last_op = {
                        "type": "delete",
                        "orig_path": str(p),
                        "temp_path": str(dst),
                        "is_dir": is_dir,
                        "parent_item": parent,
                        "idx": idx_in_parent,
                    }
                except Exception as e:
                    QMessageBox.critical(self, "Erro", f"Falha ao mover para lixeira: {e}")
                    return
        else:
            # sem caminho no disco: apenas remover da UI; ainda assim permitir undo
            self._last_op = {
                "type": "delete_ui_only",
                "parent_item": parent,
                "idx": idx_in_parent,
                "snapshot_name": it.text(0),
                "was_dir": is_dir,
            }

        # remover do UI
        if parent:
            parent.takeChild(idx_in_parent)
        else:
            pg.tree.takeTopLevelItem(idx_in_parent)

    # ---------- Item Changed: renomear no disco se necessário ----------
    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        # apenas coluna 0 -> nome
        try:
            if column != 0:
                return
            new_name = item.text(0).strip()
            if not new_name:
                return
            fp = item.data(0, self.ROLE_FULLPATH)
            if not fp:
                return

            old_path = Path(str(fp))
            if old_path.name == new_name:
                return

            try:
                new_path = old_path.with_name(new_name)
            except Exception:
                QMessageBox.warning(self, "Renomear", "Nome inválido para o sistema de arquivos.")
                item.setText(0, old_path.name)
                return

            # evitar sobrescrever
            if new_path.exists():
                base = new_path.stem
                suf = new_path.suffix
                i = 1
                cand = new_path
                while cand.exists():
                    cand = new_path.with_name(f"{base}_{i}{suf}")
                    i += 1
                new_path = cand

            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            try:
                shutil.move(str(old_path), str(new_path))
            except Exception as e:
                QMessageBox.critical(self, "Renomear", f"Falha ao renomear no disco: {e}")
                item.setText(0, old_path.name)
                return

            # atualizar ROLE_FULLPATH deste nó e dos filhos (prefix replacement)
            item.setData(0, self.ROLE_FULLPATH, str(new_path))

            def _update_children(node: QTreeWidgetItem, old_pref: Path, new_pref: Path):
                for j in range(node.childCount()):
                    ch = node.child(j)
                    ch_fp = ch.data(0, self.ROLE_FULLPATH)
                    if ch_fp:
                        try:
                            ch_path = Path(str(ch_fp))
                            rel = ch_path.relative_to(old_pref)
                            ch.setData(0, self.ROLE_FULLPATH, str(new_pref / rel))
                        except Exception:
                            pass
                    _update_children(ch, old_pref, new_pref)

            try:
                _update_children(item, old_path, new_path)
            except Exception:
                pass

            # registrar última operação (rename)
            self._last_op = {
                "type": "rename",
                "old_path": str(old_path),
                "new_path": str(new_path),
                "item": item,
                "old_name": old_path.name,
                "new_name": new_path.name,
            }
        except Exception:
            pass

    # ----------------- Widgets/helpers -----------------
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
        return it

    def _make_dir_item(self, page: '_Page', name: str, full_path: str) -> QTreeWidgetItem:
        it = self._make_item(page, name, is_dir=True)
        it.setData(0, self.ROLE_FULLPATH, full_path)
        return it

    def _make_file_item(self, page: '_Page', name: str, full_path: str) -> QTreeWidgetItem:
        it = self._make_item(page, name, is_dir=False)
        it.setData(0, self.ROLE_FULLPATH, full_path)
        return it

    def _ensure_buttons(self, page: '_Page', item: QTreeWidgetItem):
        """Gera/rega os botões de ação do nó (idempotente)."""
        from PySide6.QtWidgets import QMenu, QWidget, QHBoxLayout

        # ⚠️ Se já existe um widget ali, remova-o com segurança para evitar duplicatas.
        old = page.tree.itemWidget(item, 1)
        if old:
            page.tree.removeItemWidget(item, 1)
            try:
                old.deleteLater()
            except Exception:
                pass

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
                idx = page.tree.indexFromItem(item, 0)
                page.tree.edit(idx)
            elif chosen == act_sel_one:
                page.tree.clearSelection(); item.setSelected(True)
            elif chosen == act_sel_children:
                page.tree.clearSelection()
                def _select_recursive(node):
                    for i in range(node.childCount()):
                        ch = node.child(i); ch.setSelected(True); _select_recursive(ch)
                _select_recursive(item)
                if item.childCount() == 0:
                    item.setSelected(True)
            elif chosen == act_sel_siblings:
                page.tree.clearSelection()
                parent = item.parent()
                if parent is None:
                    for i in range(page.tree.topLevelItemCount()):
                        sib = page.tree.topLevelItem(i)
                        if sib is not item: sib.setSelected(True)
                else:
                    for i in range(parent.childCount()):
                        sib = parent.child(i)
                        if sib is not item: sib.setSelected(True)
            elif chosen == act_del:
                self.remove_item(item)

        b_more.clicked.connect(persistent_menu)
        h.addWidget(b_plus); h.addWidget(b_more); h.addStretch(1)
        page.tree.setItemWidget(item, 1, w)


    def _page_of_item(self, it: QTreeWidgetItem) -> Optional['_Page']:
        for idx in range(self.tabs.count()):
            pg = self.tabs.widget(idx)
            if isinstance(pg, HierarchyTab._Page):
                if it.treeWidget() is pg.tree:
                    return pg
        return self._cur_page()

    # ----------------- Undo da Última Operação -----------------
    def undo_last_operation(self):
        op = self._last_op
        if not op:
            QMessageBox.information(self, "Reverter", "Nenhuma operação para reverter.")
            return

        typ = op.get("type")
        try:
            if typ == "create":
                # apagar a pasta/arquivo criado e remover item da UI
                path = op.get("path")
                item = op.get("item")
                if path:
                    p = Path(path)
                    if p.exists():
                        try:
                            if p.is_dir():
                                shutil.rmtree(str(p))
                            else:
                                p.unlink()
                        except Exception as e:
                            QMessageBox.critical(self, "Reverter", f"Falha ao apagar criação: {e}")
                            return
                if item:
                    pg = self._page_of_item(item)
                    if pg:
                        parent = item.parent()
                        if parent:
                            parent.takeChild(parent.indexOfChild(item))
                        else:
                            idx = pg.tree.indexOfTopLevelItem(item)
                            if idx >= 0:
                                pg.tree.takeTopLevelItem(idx)

            elif typ == "delete":
                # mover de volta do lixo e recarregar pai
                orig = Path(op["orig_path"])
                temp = Path(op["temp_path"])
                parent_item = op.get("parent_item")
                idx = op.get("idx", 0)
                try:
                    orig.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                try:
                    shutil.move(str(temp), str(orig))
                except Exception as e:
                    QMessageBox.critical(self, "Reverter", f"Falha ao restaurar da lixeira: {e}")
                    return
                # refrescar nó pai no UI
                if parent_item:
                    self._refresh_node(parent_item)
                else:
                    # top-level: recarregar a aba pela base
                    pg = self._cur_page()
                    if pg and pg.base_edit.text().strip():
                        self._load_root_into_page(pg, pg.base_edit.text().strip())

            elif typ == "delete_ui_only":
                # recriar item apenas na UI (sem toque no disco)
                parent_item = op.get("parent_item")
                idx = op.get("idx", -1)
                name = op.get("snapshot_name", "item")
                was_dir = op.get("was_dir", True)
                pg = self._cur_page()
                if not pg:
                    return
                new_it = self._make_item(pg, name, is_dir=was_dir)
                self._ensure_buttons(pg, new_it)
                if parent_item:
                    if idx >= 0:
                        parent_item.insertChild(idx, new_it)
                    else:
                        parent_item.addChild(new_it)
                else:
                    if idx >= 0:
                        pg.tree.insertTopLevelItem(idx, new_it)
                    else:
                        pg.tree.addTopLevelItem(new_it)

            elif typ == "rename":
                # voltar nome antigo
                old = Path(op["old_path"])
                new = Path(op["new_path"])
                item = op.get("item")
                try:
                    shutil.move(str(new), str(old))
                except Exception as e:
                    QMessageBox.critical(self, "Reverter", f"Falha ao desfazer renomeio: {e}")
                    return
                if item:
                    item.setText(0, old.name)
                    item.setData(0, self.ROLE_FULLPATH, str(old))

                    # atualizar filhos (prefix replacement)
                    def _update_children(node: QTreeWidgetItem, old_pref: Path, new_pref: Path):
                        for j in range(node.childCount()):
                            ch = node.child(j)
                            ch_fp = ch.data(0, self.ROLE_FULLPATH)
                            if ch_fp:
                                try:
                                    ch_path = Path(str(ch_fp))
                                    rel = ch_path.relative_to(new_pref)
                                    ch.setData(0, self.ROLE_FULLPATH, str(old_pref / rel))
                                except Exception:
                                    pass
                            _update_children(ch, old_pref, new_pref)

                    try:
                        _update_children(item, old, new)
                    except Exception:
                        pass

            else:
                QMessageBox.information(self, "Reverter", "Tipo de operação desconhecido.")
                return
        finally:
            # limpa o histórico (apenas uma operação)
            self._last_op = None

    def _refresh_node(self, it: QTreeWidgetItem):
        """Colapsa/expande o nó para forçar recarregar (lazy)."""
        pg = self._page_of_item(it)
        if not pg:
            return
        base = it.data(0, self.ROLE_FULLPATH)
        if not base:
            return
        # liberar widgets e filhos, recolocar dummy e expandir
        self._free_subtree_widgets(it)
        it.takeChildren()
        it.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))
        pg.tree.collapseItem(it)
        pg.tree.expandItem(it)

    # ----------------- Busy no-op (evita crashes se não existir) -----------------
    def _begin_busy(self, _msg: str = ""):
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        except Exception:
            pass

    def _end_busy(self):
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
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
        
    def _ensure_lazy_placeholder(self, it: QTreeWidgetItem):
        if not bool(it.data(0, self.ROLE_IS_DIR)):
            return
        if it.childCount() == 1 and it.child(0).text(0) == self.DUMMY_MARK:
            return
        it.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))
    def _delete_current_filter_template(self):
        name = self.cb_filter_templates.currentText().strip()
        if not name or name not in self._filter_templates:
            return
        if QMessageBox.question(self, "Remover template", f"Remover '{name}'?") == QMessageBox.Yes:
            del self._filter_templates[name]
            self._save_filter_templates()
            self._refresh_filter_templates_combo()
            QMessageBox.information(self, "Templates de filtros", "Template removido.")

    def _free_subtree_widgets(self, item: QTreeWidgetItem):
        """Libera widgets apenas dos DESCENDENTES de 'item' (mantém o do próprio item)."""
        pg = self._page_of_item(item)
        if not pg:
            return
        stack = []
        # empilhe apenas os filhos (não o próprio 'item')
        for i in range(item.childCount()):
            stack.append(item.child(i))
        while stack:
            cur = stack.pop()
            w = pg.tree.itemWidget(cur, 1)
            if w:
                pg.tree.removeItemWidget(cur, 1)
                try:
                    w.deleteLater()
                except Exception:
                    pass
            for i in range(cur.childCount()):
                stack.append(cur.child(i))
    def _on_item_expanded(self, it: QTreeWidgetItem):
        # carregar quando vazio ou quando há apenas o dummy
        pg = self._page_of_item(it)
        if not pg:
            return
        base = it.data(0, self.ROLE_FULLPATH)
        if not base:
            return

        should_lazy = (it.childCount() == 0) or (it.childCount() == 1 and it.child(0).text(0) == self.DUMMY_MARK)
        if not should_lazy:
            # ainda assim, garanta que o botão de ações está presente
            self._ensure_buttons(pg, it)
            return

        it.takeChildren()
        self._begin_busy(f"Lendo “{Path(str(base)).name}”…")
        try:
            self._load_children_for_item(pg, it, str(base))
        finally:
            self._end_busy()

        # reassegura que o nó pai mantém seus botões funcionando
        self._ensure_buttons(pg, it)

    def _on_item_collapsed(self, it: QTreeWidgetItem):
        pg = self._page_of_item(it)
        if not pg:
            return
        # libera apenas widgets dos descendentes (mantém o do próprio nó!)
        self._free_subtree_widgets(it)
        # remove filhos e recoloca dummy para manter a "setinha"
        it.takeChildren()
        if bool(it.data(0, self.ROLE_IS_DIR)):
            it.addChild(QTreeWidgetItem([self.DUMMY_MARK, ""]))
        # e garante que o botão de ações do nó colapsado continue lá
        self._ensure_buttons(pg, it)

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
        # NOVO: opção copiar em vez de mover
        self.cb_copy = QCheckBox("Copiar (manter arquivos originais)")
        self.cb_copy.setToolTip("Se marcado, os arquivos serão copiados para o destino em vez de movidos.")
        for cb in (self.cb_replicate, self.cb_concat_suffix, self.cb_copy):
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
        v.addWidget(self.cb_copy)
        v.addWidget(btns)
        self._load_filter_templates()
        self._refresh_filter_templates_combo()
        _center_dialog(self, parent)

    def _add_row(self, initial_type="extensão", initial_values=""):
        row = _FilterRow(self, initial_type, initial_values)
        self.rows_box.addWidget(row)

        # garanta que o botão de ações do row está conectado toda vez que a linha é criada
        if hasattr(row, "btn_actions"):
            try:
                row.btn_actions.clicked.disconnect()
            except Exception:
                pass
            row.btn_actions.clicked.connect(lambda: row.open_actions_menu())

        row.btn_remove.clicked.connect(lambda: self._remove_row(row))

    def _remove_row(self, row: _FilterRow):
        row.setParent(None)
        row.deleteLater()

    def chosen(self) -> Tuple[List[Tuple[str, List[str]]], bool, bool, bool]:
        """
        Retorna: (filters, ask_replicate:bool, concat_suffix:bool, use_copy:bool)
        """
        filters: List[Tuple[str, List[str]]] = []
        for i in range(self.rows_box.count()):
            w = self.rows_box.itemAt(i).widget()
            if isinstance(w, _FilterRow):
                t, vals = w.spec()
                if vals:
                    filters.append((t, vals))
        return filters, self.cb_replicate.isChecked(), self.cb_concat_suffix.isChecked(), self.cb_copy.isChecked()


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
        self._undo_stack: List[Dict[str, Any]] = []
        self._undo_limit = 10
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

        # botão DESFAZER do Penerator
        self.btn_undo = QPushButton("Desfazer Penerator")
        self.btn_undo.setEnabled(False)
        self.btn_undo.setToolTip("Desfaz a última operação do Penerator (Ctrl+Z)")
        self.btn_undo.clicked.connect(self.undo_last_penerator)

        h.addWidget(btn_open)
        h.addWidget(self.base_edit)
        h.addWidget(btn_refresh)
        h.addWidget(self.btn_penerator)
        h.addWidget(self.btn_undo)
        v.addLayout(h)

        # atalho Ctrl+Z para desfazer o último run do Penerator
        try:
            shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
            shortcut.activated.connect(self.undo_last_penerator)
        except Exception:
            pass

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
    def replace_selected_names(self, initiator_item: Optional[QTreeWidgetItem] = None):
        """
        Executa find/replace no basename dos itens selecionados (ou no 'initiator_item' se não houver seleção).
        Usa um ÚNICO diálogo (_ReplaceDialog) com dois campos. Não há mais prompts secundários.
        """
        # --- coleta de alvos ---
        selected = self.tree.selectedItems()
        targets = selected if selected else ([initiator_item] if initiator_item is not None else [])
        if not targets:
            QMessageBox.information(self, "Replace", "Nenhum item selecionado.")
            return

        # --- diálogo único com dois campos ---
        dlg = _ReplaceDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        find_text, repl_text = dlg.values()

        # proteção mínima: evitar find vazio (substituir "" vira caos)
        if (find_text or "") == "":
            QMessageBox.information(self, "Replace", "Texto a buscar está vazio — operação cancelada.")
            return
        repl_text = repl_text or ""

        # helpers locais
        def unique_sibling_path(base_path: Path) -> Path:
            if not base_path.exists():
                return base_path
            stem, suf = base_path.stem, base_path.suffix
            i = 1
            cand = base_path.with_name(f"{stem}_{i}{suf}")
            while cand.exists():
                i += 1
                cand = base_path.with_name(f"{stem}_{i}{suf}")
            return cand

        def update_children_fullpaths(node: QTreeWidgetItem, old_root: Path, new_root: Path):
            for j in range(node.childCount()):
                ch = node.child(j)
                ch_fp = ch.data(0, self.ROLE_FULLPATH)
                if ch_fp:
                    try:
                        ch_path = Path(str(ch_fp))
                        rel = ch_path.relative_to(old_root)
                        ch.setData(0, self.ROLE_FULLPATH, str(new_root / rel))
                    except Exception:
                        pass
                update_children_fullpaths(ch, old_root, new_root)

        changed = 0
        failed = 0

        for it in targets:
            try:
                old_fp = it.data(0, self.ROLE_FULLPATH)
                old_name = it.text(0)
                new_name = old_name.replace(find_text, repl_text)

                # se nada mudou, segue para o próximo (sem pré-checagem obrigatória)
                if new_name == old_name:
                    continue

                if not old_fp:
                    # apenas UI
                    it.setText(0, new_name)
                    changed += 1
                    continue

                old_path = Path(str(old_fp))
                new_path = unique_sibling_path(old_path.with_name(new_name))
                try:
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass

                try:
                    shutil.move(str(old_path), str(new_path))
                except Exception as e:
                    QMessageBox.critical(self, "Replace", f"Falha ao renomear '{old_path.name}': {e}")
                    failed += 1
                    continue

                # atualiza UI e ROLE_FULLPATH
                it.setText(0, new_path.name)
                it.setData(0, self.ROLE_FULLPATH, str(new_path))

                if bool(it.data(0, self.ROLE_IS_DIR)):
                    try:
                        update_children_fullpaths(it, old_path, new_path)
                    except Exception:
                        pass

                changed += 1

            except Exception:
                failed += 1
                continue

        # feedback
        if failed == 0 and changed == 0:
            QMessageBox.information(self, "Replace", "Nenhum nome foi alterado.")
        elif failed == 0:
            QMessageBox.information(self, "Replace", f"Renomeados: {changed}")
        else:
            QMessageBox.warning(self, "Replace", f"Renomeados: {changed}\nFalhas: {failed}")

    def _ensure_buttons(self, item: QTreeWidgetItem):
        """Gera/rega os botões de ação do nó (idempotente) e inclui 'Replace…'."""
        from PySide6.QtWidgets import QMenu, QWidget, QHBoxLayout

        # Remover widget antigo (se houver) para evitar duplicação
        old = self.tree.itemWidget(item, 1)
        if old:
            self.tree.removeItemWidget(item, 1)
            try:
                old.deleteLater()
            except Exception:
                pass

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
            act_replace = menu.addAction("Renomear (replace…)")  # << NOVO
            menu.addSeparator()
            act_sel_one = menu.addAction("Selecionar")
            act_sel_children = menu.addAction("Selecionar todas as filhas")
            act_sel_siblings = menu.addAction("Selecionar todas as irmãs")
            menu.addSeparator()
            act_del = menu.addAction("Remover")

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
            elif chosen == act_replace:
                # aplica replace aos itens selecionados; se não houver seleção, usa o 'item'
                self.replace_selected_names(initiator_item=item)
            elif chosen == act_sel_one:
                self.tree.clearSelection()
                item.setSelected(True)
            elif chosen == act_sel_children:
                self.tree.clearSelection()
                def _select_recursive(node):
                    for i in range(node.childCount()):
                        ch = node.child(i)
                        ch.setSelected(True)
                        _select_recursive(ch)
                _select_recursive(item)
                if item.childCount() == 0:
                    item.setSelected(True)
            elif chosen == act_sel_siblings:
                self.tree.clearSelection()
                parent = item.parent()
                if parent is None:
                    for i in range(self.tree.topLevelItemCount()):
                        sib = self.tree.topLevelItem(i)
                        if sib is not item:
                            sib.setSelected(True)
                else:
                    for i in range(parent.childCount()):
                        sib = parent.child(i)
                        if sib is not item:
                            sib.setSelected(True)
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
        """
        Remove item da árvore e tenta apagar do disco se houver ROLE_FULLPATH.
        Mantém confirmação e opção de remoção recursiva para pastas.
        """
        full = it.data(0, self.ROLE_FULLPATH)
        is_dir = bool(it.data(0, self.ROLE_IS_DIR))
        # se temos path no disco -> confirmar e tentar apagar
        if full:
            p = Path(str(full))
            if p.exists():
                if is_dir:
                    txt = f"Remover a pasta no disco?\n\n{p}\n\n(Se a pasta não estiver vazia você poderá optar por remoção recursiva.)"
                else:
                    txt = f"Remover o arquivo no disco?\n\n{p}"
                resp = QMessageBox.question(self, "Confirmar remoção", txt, QMessageBox.Yes | QMessageBox.No)
                if resp != QMessageBox.Yes:
                    return  # usuário cancelou -> não mexer na UI

                removed_ok = False
                try:
                    if is_dir:
                        try:
                            p.rmdir()
                            removed_ok = True
                        except OSError:
                            # não vazia
                            force = QMessageBox.question(
                                self, "Pasta não vazia",
                                "A pasta não está vazia. Deseja apagar recursivamente TODO o conteúdo?",
                                QMessageBox.Yes | QMessageBox.No
                            )
                            if force == QMessageBox.Yes:
                                try:
                                    shutil.rmtree(str(p))
                                    removed_ok = True
                                except Exception as e:
                                    QMessageBox.critical(self, "Erro", f"Falha ao apagar recursivamente: {e}")
                                    removed_ok = False
                            else:
                                removed_ok = False
                    else:
                        try:
                            p.unlink()
                            removed_ok = True
                        except Exception as e:
                            QMessageBox.critical(self, "Erro", f"Falha ao remover arquivo: {e}")
                            removed_ok = False
                except Exception as e:
                    QMessageBox.critical(self, "Erro", f"Erro ao remover: {e}")
                    removed_ok = False

                if not removed_ok:
                    # não apagou no disco (erro ou cancelamento) -> abortar remoção na UI
                    return

            # se o path não existe mais (ou foi apagado), podemos remover o nó
        # caso sem ROLE_FULLPATH ou após remoção bem-sucedida:
        parent = it.parent()
        if parent:
            parent.takeChild(parent.indexOfChild(it))
        else:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))

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
    def undo_last_penerator(self):
        """
        Desfaz o último run do Penerator (move de volta ou remove cópias).
        Remove também as pastas criadas por esse run sempre que for seguro:
        - Só remove uma pasta se TODOS os arquivos contidos nela (recursivamente)
          forem arquivos criados pelo run (estão em actions[*]['dst']) OU
          forem arquivos ignóraveis do sistema (ex.: .DS_Store, Thumbs.db).
        """
        import os, stat

        if not getattr(self, "_undo_stack", None):
            QMessageBox.information(self, "Desfazer Penerator", "Nada a desfazer.")
            return

        entry = self._undo_stack.pop()
        actions = entry.get("actions", [])
        created_dirs = entry.get("created_dirs", [])

        # normalizar paths -> usar abspath + normpath
        def norm(p: str) -> str:
            try:
                return os.path.normpath(os.path.abspath(str(p)))
            except Exception:
                return os.path.normpath(str(p))

        created_files = set()
        for act in actions:
            dst = act.get("dst")
            if dst:
                created_files.add(norm(dst))

        # arquivos do SO que podemos remover sem culpa
        IGNORABLE_FILENAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}

        reverted = 0
        failed = 0

        # 1) Reverter ações (arquivos) em ordem inversa
        for act in reversed(actions):
            op = act.get("op")
            src_raw = act.get("src")
            dst_raw = act.get("dst")
            src = Path(src_raw) if src_raw else None
            dst = Path(dst_raw) if dst_raw else None
            try:
                if op == "move":
                    # mover de volta: dst -> src
                    if dst and dst.exists():
                        target_restore = src if src else None
                        if target_restore:
                            # se src existe, criar nome alternativo para nao sobrescrever
                            if target_restore.exists():
                                base = target_restore.stem
                                suf = target_restore.suffix
                                i = 1
                                cand = target_restore.with_name(f"{base}_restored_{i}{suf}")
                                while cand.exists():
                                    i += 1
                                    cand = target_restore.with_name(f"{base}_restored_{i}{suf}")
                                target_restore = cand
                            try:
                                target_restore.parent.mkdir(parents=True, exist_ok=True)
                            except Exception:
                                pass
                            try:
                                # ajustar permissões se necessário
                                try:
                                    os.chmod(str(dst), stat.S_IWRITE | stat.S_IREAD)
                                except Exception:
                                    pass
                                shutil.move(str(dst), str(target_restore))
                                reverted += 1
                            except Exception:
                                failed += 1
                        else:
                            failed += 1
                    else:
                        failed += 1
                elif op == "copy":
                    # deletar o arquivo copiado (dst)
                    if dst and dst.exists():
                        try:
                            try:
                                os.chmod(str(dst), stat.S_IWRITE | stat.S_IREAD)
                            except Exception:
                                pass
                            dst.unlink()
                            reverted += 1
                        except Exception:
                            failed += 1
                    else:
                        # já não existe
                        pass
                else:
                    failed += 1
            except Exception:
                failed += 1
                continue

        # 2) Agora, tentativa robusta de remover diretórios criados por este run
        removed_dirs = 0
        failed_dirs = 0
        remaining_dirs = []

        try:
            # normalizar e ordenar por profundidade descendente
            norm_dirs = [norm(d) for d in created_dirs]
            norm_dirs = sorted(set(norm_dirs), key=lambda s: len(Path(s).parts), reverse=True)

            for d in norm_dirs:
                try:
                    p = Path(d)
                    if not p.exists() or not p.is_dir():
                        # já removido -> ok
                        continue

                    # coletar todos os arquivos recursivamente dentro de p
                    all_files = [f for f in p.rglob("*") if f.is_file()]

                    # normalizar lista de arquivos (strings)
                    all_files_norm = [norm(str(f)) for f in all_files]

                    # Se houver arquivos, verificar se todos são "seguramente removíveis":
                    # - estão em created_files OR
                    # - têm nomes ignóraveis (ex: .DS_Store)
                    removable = True
                    for af, af_norm in zip(all_files, all_files_norm):
                        name = af.name
                        if af_norm in created_files:
                            continue
                        if name in IGNORABLE_FILENAMES:
                            # aceitável, vamos apagar depois
                            continue
                        # Se encontramos um arquivo que NÃO pertence ao run e não é ignóravel => abortar remoção
                        removable = False
                        break

                    if not removable:
                        remaining_dirs.append(d)
                        failed_dirs += 1
                        continue

                    # Apagar arquivos: primeiro remover arquivos criados ou ignoráveis
                    for af, af_norm in zip(all_files, all_files_norm):
                        try:
                            # ignorar caso já removido
                            if not af.exists():
                                continue
                            # se af não estiver em created_files e for ignóravel -> apagar
                            if af_norm in created_files or af.name in IGNORABLE_FILENAMES:
                                try:
                                    os.chmod(str(af), stat.S_IWRITE | stat.S_IREAD)
                                except Exception:
                                    pass
                                af.unlink()
                            else:
                                # não deveria chegar aqui (já checado), mas pular
                                pass
                        except Exception:
                            # se falha ao apagar arquivo, marcar e abortar remoção desta pasta
                            removable = False
                            break

                    if not removable:
                        remaining_dirs.append(d)
                        failed_dirs += 1
                        continue

                    # Remover subdiretórios vazios (do mais profundo ao raso)
                    # listar subdirs ordenados por profundidade decrescente
                    subdirs = sorted([x for x in p.rglob("*") if x.is_dir()], key=lambda x: len(x.parts), reverse=True)
                    for sd in subdirs:
                        try:
                            sd.rmdir()
                        except Exception:
                            # se não vazia, ignora
                            pass

                    # Remover a própria p
                    try:
                        p.rmdir()
                        removed_dirs += 1
                    except Exception:
                        # pode não estar vazia ou permissão falhou
                        remaining_dirs.append(d)
                        failed_dirs += 1

                except Exception:
                    failed_dirs += 1
                    remaining_dirs.append(d)
                    continue
        except Exception:
            # falha inesperada na limpeza de diretórios: reportar sem quebrar
            pass

        # desabilitar botão se nada mais para desfazer
        if not self._undo_stack:
            try:
                self.btn_undo.setEnabled(False)
            except Exception:
                pass

        # mensagem final (resumida)
        summary = (
            f"Arquivos revertidos: {reverted}\n"
            f"Falhas ao reverter arquivos: {failed}\n\n"
            f"Pastas removidas: {removed_dirs}\n"
            f"Pastas não removidas: {failed_dirs}\n"
        )
        if remaining_dirs:
            # mostrar até 10 diretórios restantes para diagnóstico
            show = remaining_dirs[:10]
            summary += "\nPastas restantes (ex.):\n" + "\n".join(show)
            if len(remaining_dirs) > 10:
                summary += f"\n... e mais {len(remaining_dirs)-10} dirs."

        QMessageBox.information(self, "Desfazer Penerator", summary)

        self._refresh_current_view()

        
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
        filters, ask_replicate, concat_suffix, use_copy = dlg.chosen()
        if not filters:
            QMessageBox.information(self, "Penerator", "Adicione ao menos um filtro com valores.")
            return

        # Inicializa run_actions desde o começo (evita NameError)
        run_actions: List[Dict[str, Any]] = []
        run_created_dirs: Set[str] = set() 

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
            ftype = ftype.lower()
            largest = info[0] if len(info) > 0 else None
            icc_name = info[1] if len(info) > 1 else None
            exif_ymd = info[2] if len(info) > 2 else None

            if ftype.startswith("resolução"):
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

            if ftype.startswith("perfil"):
                if not icc_name:
                    return None
                icc_lower = icc_name.lower()
                for v in vals:
                    if v.lower() in icc_lower:
                        return v
                return None

            if ftype.startswith("data"):
                try:
                    mtime = p.stat().st_mtime
                except Exception:
                    mtime = 0.0
                file_date_cands = self._date_candidates_for_path(exif_ymd, mtime)
                for v in vals:
                    for norm in self._normalize_input_date_token(v):
                        if norm in file_date_cands:
                            return v
                return None

            return None

        # 5) Aplicar pipeline (ordem = profundidade da hierarquia)
        moved = 0
        skipped = 0

        import re
        self._begin_busy("Organizando arquivos conforme filtros…")
        try:
            for i, p in enumerate(files, 1):
                info = props.get(p, (None, None, None))
                cur_dir = dest_root
                matched_labels: List[Tuple[str, str]] = []

                # aplicar cada filtro sequencialmente; se casar => desce para esse nível
                for ftype, vals in filters:
                    lab = match_and_label(p, info, ftype, vals)
                    if lab is not None:
                        safe = str(lab).replace("/", "-")
                        cur_dir = cur_dir / safe
                        matched_labels.append((ftype, lab))

                # se não casou em nenhum filtro -> ignorar (comportamento atual)
                if not matched_labels:
                    skipped += 1
                    self._pump(i, every=64)
                    continue

                try:
                    # montar lista de ancestrais entre dest_root (excluído) e cur_dir (incluído)
                    to_check = []
                    tmp = cur_dir
                    # evita loop infinito: limita profundidade razoável (ex: 128)
                    depth_guard = 0
                    while tmp != dest_root and depth_guard < 512:
                        to_check.append(tmp)
                        tmp = tmp.parent
                        depth_guard += 1
                    # checar da raiz para o destino (ordem de criação)
                    for anc in reversed(to_check):
                        if not anc.exists():
                            run_created_dirs.add(str(anc))
                except Exception:
                    # se algo falhar, não bloquear o processamento
                    pass

                try:
                    cur_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass

                # nome do arquivo (concatenado opcional)
                orig_stem = p.stem
                orig_suffix = p.suffix  # inclui o ponto

                new_stem = orig_stem
                if concat_suffix:
                    suffix_tokens: List[str] = []
                    for ftype, lab in matched_labels:
                        ft_lower = ftype.lower()
                        if ft_lower == "extensão":
                            continue  # não concatenar extensão
                        if ft_lower.startswith("resolução"):
                            try:
                                suffix_tokens.append(self._suffix_from_resolution_label(str(lab)))
                            except Exception:
                                suffix_tokens.append(re.sub(r"[^0-9a-zA-Z]+", "", str(lab)))
                        elif ft_lower.startswith("perfil"):
                            suffix_tokens.append(re.sub(r"[^0-9a-zA-Z]+", "", str(lab)))
                        elif ft_lower.startswith("data"):
                            comp = "".join(ch for ch in str(lab) if ch.isdigit())
                            if len(comp) in (4, 6, 8):
                                suffix_tokens.append(f"d{comp}")
                            else:
                                suffix_tokens.append("d" + re.sub(r"[^0-9a-zA-Z]+", "", str(lab)))
                        else:
                            suffix_tokens.append(re.sub(r"[^0-9a-zA-Z]+", "", str(lab)))

                    if suffix_tokens:
                        new_stem = f"{orig_stem}_{'_'.join(suffix_tokens)}"

                target_name = f"{new_stem}{orig_suffix}"
                target = self._unique_target(cur_dir, target_name)

                try:
                    if use_copy:
                        shutil.copy2(str(p), str(target))
                        run_actions.append({"op": "copy", "src": str(p), "dst": str(target)})
                    else:
                        shutil.move(str(p), str(target))
                        run_actions.append({"op": "move", "src": str(p), "dst": str(target)})
                    moved += 1
                except Exception:
                    skipped += 1

                self._pump(i, every=32)
        finally:
            self._end_busy()

        action_word = "copiados" if use_copy else "movidos"
        QMessageBox.information(
            self,
            "Penerator",
            f"Arquivos {action_word}: {moved}\nIgnorados (não passaram em nenhum filtro ou falha): {skipped}"
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

        # 8) Empurra a run para a pilha de undo (se houver ações)
        try:
            if run_actions:
                if not hasattr(self, "_undo_stack"):
                    self._undo_stack = []
                    self._undo_limit = getattr(self, "_undo_limit", 10)
                run_entry = {
                    "type": "penerator_run",
                    "actions": run_actions,
                    "created_dirs": sorted(list(run_created_dirs))  # lista persistível
                }
                self._undo_stack.append(run_entry)
                # limitar tamanho
                if len(self._undo_stack) > getattr(self, "_undo_limit", 10):
                    self._undo_stack.pop(0)
                # habilitar botão (se existir)
                if hasattr(self, "btn_undo"):
                    self.btn_undo.setEnabled(True)
        except Exception:
            # não quebrar o fluxo principal por causa do undo
            pass


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
