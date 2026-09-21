"""Logs tab with level filter and export."""
from __future__ import annotations

import time

from qtpy.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogsTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.level = QComboBox(); self.level.addItems(LEVELS); self.level.setCurrentText("INFO")
        self.filter = QLineEdit(); self.filter.setPlaceholderText("filter text (logger or message)")
        b_export = QPushButton("Export logs…"); b_export.clicked.connect(self.export)
        b_clear = QPushButton("Clear view"); b_clear.clicked.connect(lambda: self.view.clear())
        top = QHBoxLayout(); top.addWidget(QLabel("Min level")); top.addWidget(self.level); top.addWidget(self.filter, 1); top.addWidget(b_export); top.addWidget(b_clear)
        self.view = QPlainTextEdit(); self.view.setReadOnly(True); self.view.setMaximumBlockCount(5000); self.view.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        lay = QVBoxLayout(self); lay.addLayout(top); lay.addWidget(self.view)
        self.view.setPlainText(controller.logs_text())
        controller.log_message.connect(self.on_log)

    def on_log(self, entry: dict) -> None:
        if LEVELS.index(entry["level"]) < LEVELS.index(self.level.currentText()):
            return
        f = self.filter.text().strip()
        if f and f.lower() not in entry["text"].lower():
            return
        self.view.appendPlainText(entry["text"])

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export logs", time.strftime("snspectrometer_%Y%m%d_%H%M%S.log"), "Log (*.log *.txt)")
        if path:
            self.controller.export_logs(path)
