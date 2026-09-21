"""Preflight result dialog."""
from __future__ import annotations

from qtpy.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout
from qtpy.QtGui import QColor

from ...measurements.group import PreflightItem


class PreflightDialog(QDialog):
    def __init__(self, items: list[PreflightItem], parent=None, allow_start: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Measurement preflight")
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        lst = QListWidget()
        ok_all = True
        for it in items:
            li = QListWidgetItem(it.label)
            if it.ok:
                li.setForeground(QColor("#1b7f3b"))
            elif it.blocking:
                li.setForeground(QColor("#b00020")); ok_all = False
            else:
                li.setForeground(QColor("#b36b00"))
            lst.addItem(li)
        lay.addWidget(lst)
        summary = QLabel("READY TO START" if ok_all else "BLOCKED — fix the items marked BLOCK")
        summary.setStyleSheet("font-weight: bold; color: %s;" % ("#1b7f3b" if ok_all else "#b00020"))
        lay.addWidget(summary)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel if (ok_all and allow_start) else QDialogButtonBox.Close)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        if ok_all and allow_start:
            bb.button(QDialogButtonBox.Ok).setText("Start")
        lay.addWidget(bb)
        self.ok_all = ok_all
