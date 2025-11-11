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

