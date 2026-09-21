"""Small shared GUI helpers."""
from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QHeaderView)


def section(title: str, widget: QWidget) -> QGroupBox:
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.addWidget(widget)
    return box


def hbox(*widgets: QWidget, stretch_last: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w


def vbox(*widgets: QWidget) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    return w


def scroll(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(widget)
    sa.setFrameShape(QScrollArea.NoFrame)
    return sa


def show_error(parent: Optional[QWidget], title: str, reason: str, details: str = "", suggestion: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle(title)
    text = f"<b>{title}</b><br><br><b>Reason:</b><br>{reason}"
    if suggestion:
        text += f"<br><br><b>Suggested action (diagnostic hint, not a guaranteed fix):</b><br>{suggestion}"
    box.setText(text)
    if details:
        box.setDetailedText(details)
    box.exec()


def confirm(parent: Optional[QWidget], title: str, text: str, dangerous: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning if dangerous else QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No if dangerous else QMessageBox.Yes)
    return box.exec() == QMessageBox.Yes


class KeyValueTable(QTableWidget):
    """Two-column read-only table."""

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Key", "Value"])
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)

    def set_data(self, data: dict[str, Any] | Sequence[tuple[str, Any]]) -> None:
        items = list(data.items()) if isinstance(data, dict) else list(data)
        self.setRowCount(len(items))
        for i, (k, v) in enumerate(items):
            self.setItem(i, 0, QTableWidgetItem(str(k)))
            if isinstance(v, (dict, list)):
                v = json.dumps(v, default=str)
            elif isinstance(v, float):
                v = f"{v:.6g}"
            self.setItem(i, 1, QTableWidgetItem(str(v)))
        self.resizeColumnToContents(0)


class TextDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None, monospace: bool = True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        ed = QPlainTextEdit()
        ed.setReadOnly(True)
        ed.setPlainText(text)
        if monospace:
            ed.setStyleSheet("font-family: Consolas, monospace;")
        lay.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)


class StatusLabel(QLabel):
    COLORS = {"ok": "#1b7f3b", "warn": "#b36b00", "error": "#b00020", "info": "#1f4e79", "neutral": "#444"}

    def set_status(self, text: str, kind: str = "neutral") -> None:
        self.setText(text)
        self.setStyleSheet(f"color: {self.COLORS.get(kind, '#444')}; font-weight: bold;")
