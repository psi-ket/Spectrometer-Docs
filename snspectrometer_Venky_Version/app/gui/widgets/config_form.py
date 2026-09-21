"""Form generated from a list of FieldSpec entries."""
from __future__ import annotations

from typing import Any, Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QWidget

from ...measurements.base import FieldSpec
from .channel_selector import ChannelCombo, ChannelListWidget, ChannelProvider


class ConfigForm(QWidget):
    changed = Signal()

    def __init__(self, fields: list[FieldSpec], provider: ChannelProvider, show_advanced: bool = True, parent=None):
        super().__init__(parent)
        self.fields = fields
        self.provider = provider
        self.widgets: dict[str, QWidget] = {}
        self._int_doubles: set[str] = set()
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        for spec in fields:
            if spec.advanced and not show_advanced:
                continue
            w = self._make(spec)
            self.widgets[spec.name] = w
            label = spec.label + (f" [{spec.unit}]" if spec.unit else "")
            lab = QLabel(label)
            if spec.help:
                lab.setToolTip(spec.help)
                w.setToolTip(spec.help)
            self.form.addRow(lab, w)

    def _make(self, spec: FieldSpec) -> QWidget:
        k = spec.kind
        if k == "int":
            lo = int(spec.minimum) if spec.minimum is not None else -2_000_000_000
            hi = int(spec.maximum) if spec.maximum is not None else 2_000_000_000
            if lo < -2_147_483_648 or hi > 2_147_483_647:
                # Qt's QSpinBox is 32-bit; use a 0-decimal double spin box for wide ps ranges
                w = QDoubleSpinBox()
                w.setDecimals(0)
                w.setRange(float(lo), float(hi))
                w.setSingleStep(float(spec.step) if spec.step else 1.0)
                self._int_doubles.add(spec.name)
            else:
                w = QSpinBox()
                w.setRange(lo, hi)
                w.setSingleStep(int(spec.step) if spec.step else 1)
            w.valueChanged.connect(lambda _: self.changed.emit())
        elif k == "float":
            w = QDoubleSpinBox()
            w.setDecimals(4)
            w.setRange(float(spec.minimum) if spec.minimum is not None else -1e15, float(spec.maximum) if spec.maximum is not None else 1e15)
            w.setSingleStep(float(spec.step) if spec.step else 1.0)
            w.valueChanged.connect(lambda _: self.changed.emit())
        elif k == "bool":
            w = QCheckBox()
            w.toggled.connect(lambda _: self.changed.emit())
        elif k == "choice":
            w = QComboBox()
            for c in spec.choices or []:
                w.addItem(str(c), c)
            w.currentIndexChanged.connect(lambda _: self.changed.emit())
        elif k in ("channel", "channel_optional"):
            w = ChannelCombo(self.provider, optional=(k == "channel_optional"))
            w.currentIndexChanged.connect(lambda _: self.changed.emit())
        elif k in ("channels", "channels_optional"):
            w = ChannelListWidget(self.provider)
            w.changed.connect(self.changed.emit)
        else:
            w = QLineEdit()
            w.textChanged.connect(lambda _: self.changed.emit())
        return w

    def refresh_channels(self) -> None:
        for w in self.widgets.values():
            if isinstance(w, (ChannelCombo, ChannelListWidget)):
                w.refresh()

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for spec in self.fields:
            w = self.widgets.get(spec.name)
            if w is None:
                continue
            if isinstance(w, QSpinBox):
                out[spec.name] = int(w.value())
            elif isinstance(w, QDoubleSpinBox):
                out[spec.name] = int(round(w.value())) if spec.name in self._int_doubles else float(w.value())
            elif isinstance(w, QCheckBox):
                out[spec.name] = bool(w.isChecked())
            elif isinstance(w, ChannelCombo):
                out[spec.name] = w.value()
            elif isinstance(w, ChannelListWidget):
                out[spec.name] = w.value()
            elif isinstance(w, QComboBox):
                out[spec.name] = w.currentData()
            elif isinstance(w, QLineEdit):
                text = w.text()
                if spec.kind == "str" and spec.name in ("n_histograms",):
                    out[spec.name] = [int(x) for x in text.split(",") if x.strip()]
                else:
                    out[spec.name] = text
        return out

    def set_values(self, values: dict[str, Any]) -> None:
        for spec in self.fields:
            if spec.name not in values:
                continue
            w = self.widgets.get(spec.name)
            v = values[spec.name]
            if w is None:
                continue
            w.blockSignals(True)
            try:
                if isinstance(w, QSpinBox):
                    w.setValue(int(v))
                elif isinstance(w, QDoubleSpinBox):
                    w.setValue(float(v))
                elif isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, ChannelCombo):
                    w.set_value(v)
                elif isinstance(w, ChannelListWidget):
                    w.set_value(v or [])
                elif isinstance(w, QComboBox):
                    idx = w.findData(v)
                    if idx < 0:
                        idx = w.findText(str(v))
                    if idx >= 0:
                        w.setCurrentIndex(idx)
                elif isinstance(w, QLineEdit):
                    w.setText(",".join(str(x) for x in v) if isinstance(v, list) else str(v))
            finally:
                w.blockSignals(False)

    def set_enabled_all(self, enabled: bool) -> None:
        for w in self.widgets.values():
            w.setEnabled(enabled)
