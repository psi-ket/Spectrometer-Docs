"""Collapsible documentation panel shown next to every measurement."""
from __future__ import annotations

from qtpy.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class HelpPanel(QWidget):
    def __init__(self, text: str, title: str = "Help / documentation", parent=None):
        super().__init__(parent)
        self.btn = QPushButton(f"▸ {title}")
        self.btn.setCheckable(True)
        self.btn.setStyleSheet("text-align: left;")
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(text.strip())
        self.text.setMinimumHeight(180)
        self.text.hide()
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.btn); lay.addWidget(self.text)
        self.btn.toggled.connect(self._toggle)
        self.title = title

    def _toggle(self, on: bool) -> None:
        self.text.setVisible(on)
        self.btn.setText(("▾ " if on else "▸ ") + self.title)

    def set_text(self, text: str) -> None:
        self.text.setPlainText(text.strip())
