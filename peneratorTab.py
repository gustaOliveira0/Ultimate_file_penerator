from PySide6.QtCore import Qt, QTimer, QObject, QEvent, QPoint, QRect
from PySide6.QtGui import QKeySequence, QShortcut
from typing import Optional, List, Dict, Any
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple, Set
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView, QLineEdit,
    QMessageBox, QStyle, QDialog
)

from PySide6.QtWidgets import (
    QLineEdit,
    QFileDialog,
    QDialog
)
def _choose_directory(parent, title="Escolher pasta"):
    dlg = QFileDialog(parent, title)
    dlg.setOption(QFileDialog.DontUseNativeDialog, True)
    dlg.setFileMode(QFileDialog.Directory)
    dlg.setOption(QFileDialog.ShowDirsOnly, True)
    if dlg.exec() == QDialog.Accepted:
        files = dlg.selectedFiles()
        return files[0] if files else ""
    return ""


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

