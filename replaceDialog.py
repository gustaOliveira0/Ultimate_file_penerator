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
   