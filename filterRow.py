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

