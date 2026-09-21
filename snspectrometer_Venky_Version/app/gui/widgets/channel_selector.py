"""Channel selection widgets fed from the detector map (labels, not bare numbers)."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QComboBox, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

ChannelProvider = Callable[[], list[tuple[int, str]]]


class ChannelCombo(QComboBox):
    """Single channel selector; ``optional=True`` adds an 'unused' entry (None)."""

    def __init__(self, provider: ChannelProvider, optional: bool = False, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.optional = optional
        self.refresh()

    def refresh(self) -> None:
        cur = self.value()
        self.blockSignals(True)
        self.clear()
        if self.optional:
            self.addItem("(unused)", None)
        for ch, label in self.provider():
            self.addItem(f"{label}  [ch {ch}]", int(ch))
        self.blockSignals(False)
        if cur is not None:
            self.set_value(cur)

    def value(self) -> Optional[int]:
        return self.currentData()

    def set_value(self, ch: Optional[int]) -> None:
        for i in range(self.count()):
            if self.itemData(i) == ch:
                self.setCurrentIndex(i)
                return
        if ch is not None:
            self.addItem(f"ch {ch} (not in map)", int(ch))
            self.setCurrentIndex(self.count() - 1)


class ChannelListWidget(QWidget):
    """Multi-select channel list with select-all / none."""

    changed = Signal()

    def __init__(self, provider: ChannelProvider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.setMaximumHeight(140)
        btn_all = QPushButton("All"); btn_none = QPushButton("None")
        btn_all.setMaximumWidth(50); btn_none.setMaximumWidth(50)
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none.clicked.connect(lambda: self._set_all(False))
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.addWidget(btn_all); row.addWidget(btn_none); row.addStretch(1)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.list); lay.addLayout(row)
        self.list.itemChanged.connect(lambda _: self.changed.emit())
        self.refresh()

    def refresh(self) -> None:
        cur = set(self.value())
        self.list.blockSignals(True)
        self.list.clear()
        for ch, label in self.provider():
            it = QListWidgetItem(f"{label}  [ch {ch}]")
            it.setData(Qt.UserRole, int(ch))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if int(ch) in cur else Qt.Unchecked)
            self.list.addItem(it)
        self.list.blockSignals(False)

    def _set_all(self, state: bool) -> None:
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if state else Qt.Unchecked)
        self.list.blockSignals(False)
        self.changed.emit()

    def value(self) -> list[int]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(int(it.data(Qt.UserRole)))
        return out

    def set_value(self, channels: Sequence[int]) -> None:
        chans = {int(c) for c in channels}
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setCheckState(Qt.Checked if int(it.data(Qt.UserRole)) in chans else Qt.Unchecked)
        self.list.blockSignals(False)
        self.changed.emit()
