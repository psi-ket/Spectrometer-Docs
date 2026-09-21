"""Virtual channel editor (advanced): Coincidence(s), Combinations, DelayedChannels, GatedChannels, Combiner."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qtpy.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView)

from swabian_backend import virtual_channels as V
from swabian_backend import measurements as M

from ..widgets.channel_selector import ChannelCombo, ChannelListWidget
from ..widgets.common import show_error, confirm
from ..widgets.device_selector import DeviceSelector


@dataclass
class VirtualChannelEntry:
    name: str
    kind: str
    inputs: list[int]
    outputs: list[int]
    params: dict[str, Any]
    obj: Any = None
    countrate: Any = None
    labels: list[str] = field(default_factory=list)

    def entries(self) -> list[tuple[int, str]]:
        return [(ch, f"VC {self.name}" + (f" #{i}" if len(self.outputs) > 1 else "")) for i, ch in enumerate(self.outputs)]


class VirtualChannelsEditor(QWidget):
    KINDS = ["Coincidence", "Coincidences (pairs of selected)", "Combinations", "DelayedChannels", "GatedChannels", "Combiner"]

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        if not hasattr(controller, "virtual_channels"):
            controller.virtual_channels = {}
        self.device = DeviceSelector(controller)
        provider = self.device.channel_provider()
        form = QGroupBox("Create virtual channel"); f = QFormLayout(form)
        self.kind = QComboBox(); self.kind.addItems(self.KINDS)
        self.name = QLineEdit("vc1")
        self.inputs = ChannelListWidget(provider)
        self.window = QSpinBox(); self.window.setRange(1, 10**9); self.window.setValue(1000); self.window.setSuffix(" ps")
        self.delay = QSpinBox(); self.delay.setRange(-10**9, 10**9); self.delay.setValue(0); self.delay.setSuffix(" ps")
        self.gate_start = ChannelCombo(provider); self.gate_stop = ChannelCombo(provider)
        self.timestamp = QComboBox(); self.timestamp.addItems(list(V.COINCIDENCE_TIMESTAMP_MODES))
        self.combo_preview = QLabel("")
        f.addRow("Kind", self.kind); f.addRow("Name", self.name); f.addRow("Input channels", self.inputs); f.addRow("Window", self.window); f.addRow("Delay", self.delay)
        f.addRow("Gate start", self.gate_start); f.addRow("Gate stop", self.gate_stop); f.addRow("Timestamp", self.timestamp); f.addRow("", self.combo_preview)
        btn = QPushButton("Create"); btn.clicked.connect(self.create)
        f.addRow(btn)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Kind", "Inputs", "Virtual channels", "Live cps", "Remove"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Device</b>")); lay.addWidget(self.device); lay.addWidget(form)
        lay.addWidget(QLabel("Virtual channels are software-defined in the Swabian library and are available to measurements on the same device (they appear in channel selectors as 'VC …')."))
        lay.addWidget(self.table)
        self.inputs.changed.connect(self._preview)
        self.kind.currentIndexChanged.connect(self._preview)
        self.device.currentIndexChanged.connect(lambda _: (self.inputs.refresh(), self.gate_start.refresh(), self.gate_stop.refresh(), self.refresh_table()))
        controller.detector_map_changed.connect(lambda: (self.inputs.refresh(), self.gate_start.refresh(), self.gate_stop.refresh()))
        controller.health.connect(lambda _: self.update_rates())
        self._preview()

    def _preview(self) -> None:
        n = len(self.inputs.value())
        k = self.kind.currentText()
        if k.startswith("Combinations"):
            self.combo_preview.setText(f"Combinations exposes up to 2^N - 1 = {V.count_combinations(n)} virtual channels for N={n}; only the {n} sum channels are enabled here." + ("  ⚠ exponential growth" if n > 8 else ""))
        elif k.startswith("Coincidences"):
            self.combo_preview.setText(f"{V.count_coincidence_groups(n, 2)} pair groups")
        else:
            self.combo_preview.setText("")

    def create(self) -> None:
        dev = self.device.device_id()
        if dev is None:
            show_error(self, "No device", "Connect a device first."); return
        if self.controller.is_acquiring(dev) and not confirm(self, "Acquisition active", "An acquisition is running on this device. Create the virtual channel anyway?", dangerous=True):
            return
        handle = self.controller.handle(dev)
        chans = self.inputs.value()
        kind = self.kind.currentText()
        name = self.name.text() or kind
        try:
            if kind == "Coincidence":
                obj, ch = V.make_coincidence(handle.tagger, chans, self.window.value(), self.timestamp.currentText()); outs = [ch]; params = {"window_ps": self.window.value(), "timestamp": self.timestamp.currentText()}
            elif kind.startswith("Coincidences"):
                groups = V.coincidence_groups(chans, 2)
                obj, outs = V.make_coincidences(handle.tagger, groups, self.window.value(), self.timestamp.currentText()); params = {"groups": groups, "window_ps": self.window.value()}
            elif kind.startswith("Combinations"):
                obj = V.make_combinations(handle.tagger, chans, self.window.value()); outs = [int(obj.getSumChannel(n)) for n in range(1, len(chans) + 1)]; params = {"window_ps": self.window.value(), "note": "outputs are the n-fold sum channels"}
            elif kind == "DelayedChannels":
                obj, outs = V.make_delayed_channels(handle.tagger, chans, self.delay.value()); params = {"delay_ps": self.delay.value()}
            elif kind == "GatedChannels":
                obj, outs = V.make_gated_channels(handle.tagger, chans, self.gate_start.value(), self.gate_stop.value()); params = {"gate_start": self.gate_start.value(), "gate_stop": self.gate_stop.value()}
            else:
                obj, ch = V.make_combiner(handle.tagger, chans); outs = [ch]; params = {}
            cr = M.make_countrate(handle.tagger, outs)
        except Exception as exc:
            show_error(self, "Virtual channel creation failed", str(exc)); return
        entry = VirtualChannelEntry(name, kind, list(chans), list(outs), params, obj, cr)
        self.controller.virtual_channels.setdefault(dev, []).append(entry)
        self.controller.detector_map_changed.emit()
        self.refresh_table()

    def refresh_table(self) -> None:
        dev = self.device.device_id()
        entries = self.controller.virtual_channels.get(dev, []) if dev else []
        self.table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            self.table.setItem(i, 0, QTableWidgetItem(e.name)); self.table.setItem(i, 1, QTableWidgetItem(e.kind))
            self.table.setItem(i, 2, QTableWidgetItem(", ".join(map(str, e.inputs)))); self.table.setItem(i, 3, QTableWidgetItem(", ".join(map(str, e.outputs))))
            self.table.setItem(i, 4, QTableWidgetItem(""))
            btn = QPushButton("Remove"); btn.clicked.connect(lambda _=False, e=e: self.remove(e)); self.table.setCellWidget(i, 5, btn)

    def update_rates(self) -> None:
        dev = self.device.device_id()
        entries = self.controller.virtual_channels.get(dev, []) if dev else []
        for i, e in enumerate(entries):
            if e.countrate is not None and i < self.table.rowCount():
                try:
                    rates = [f"{r:.4g}" for r in e.countrate.getData()]
                    self.table.setItem(i, 4, QTableWidgetItem(", ".join(rates)))
                except Exception:
                    pass

    def remove(self, e: VirtualChannelEntry) -> None:
        dev = self.device.device_id()
        lst = self.controller.virtual_channels.get(dev, [])
        if e in lst:
            lst.remove(e)
        e.countrate = None; e.obj = None
        self.controller.detector_map_changed.emit()
        self.refresh_table()
