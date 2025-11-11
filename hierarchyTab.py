from pathlib import Path
from PySide6.QtWidgets import QMessageBox, QTreeWidgetItem, QWidget
from typing import Optional
from PySide6.QtCore import Qt
import os
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QAbstractItemView, QLineEdit,
    QMessageBox, QStyle, QDialog
)

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

