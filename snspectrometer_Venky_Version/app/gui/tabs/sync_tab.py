"""Synchronization tab: sync state, multi-device view, reference clock configuration."""
from __future__ import annotations

from qtpy.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView)

from ...models.units import format_frequency
from ...synchronization.sync_manager import ReferenceClockConfig
from ..widgets.channel_selector import ChannelCombo
from ..widgets.common import KeyValueTable, StatusLabel, confirm, show_error
from ..widgets.device_selector import DeviceSelector


class SyncTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.device = DeviceSelector(controller)
        self.state = StatusLabel("—")
        self.reason = QLabel(""); self.reason.setWordWrap(True)
        self.subdev = QTableWidget(0, 5); self.subdev.setHorizontalHeaderLabels(["Index", "Serial", "Model", "Address", "Channels"]); self.subdev.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rc_state = KeyValueTable()
        # reference clock config
        cfg = QGroupBox("Software-defined reference clock (setReferenceClock)"); f = QFormLayout(cfg)
        provider = self.device.channel_provider()
        self.enable = QCheckBox("Enable reference clock")
        self.clock_ch = ChannelCombo(provider)
        self.freq = QDoubleSpinBox(); self.freq.setDecimals(3); self.freq.setRange(1e3, 1e9); self.freq.setValue(10e6); self.freq.setSuffix(" Hz")
        self.tc = QDoubleSpinBox(); self.tc.setDecimals(6); self.tc.setRange(1e-6, 100); self.tc.setValue(1e-3); self.tc.setSuffix(" s")
        self.pps = ChannelCombo(provider, optional=True)
        self.offset = QSpinBox(); self.offset.setRange(-10**9, 10**9); self.offset.setSuffix(" ps")
        self.wait = QCheckBox("Wait until locked (blocks, throws on lock failure)"); self.wait.setChecked(True)
        f.addRow("", self.enable); f.addRow("Reference clock channel", self.clock_ch); f.addRow("Clock frequency", self.freq); f.addRow("PLL time constant", self.tc); f.addRow("1PPS synchronization channel", self.pps); f.addRow("Synchronization offset", self.offset); f.addRow("", self.wait)
        self.warning = QLabel("⚠ Applying or disabling a reference clock changes the instrument time base. Data acquired before and after are NOT comparable; acquisition must be stopped first. Overflows are expected until the PLL locks (manual 5.3.1).")
        self.warning.setWordWrap(True); self.warning.setStyleSheet("color: #b00020; font-weight: bold;")
        b_apply = QPushButton("Apply reference clock configuration"); b_apply.clicked.connect(self.apply)
        b_refresh = QPushButton("Refresh state"); b_refresh.clicked.connect(self.refresh)
        f.addRow(self.warning); f.addRow(b_apply); f.addRow(b_refresh)
        top = QHBoxLayout(); top.addWidget(QLabel("<b>Device</b>")); top.addWidget(self.device); top.addWidget(QLabel("Synchronization state:")); top.addWidget(self.state); top.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addLayout(top); lay.addWidget(self.reason)
        lay.addWidget(QLabel("<b>Devices in this handle (Synchronizer members / network servers)</b>")); lay.addWidget(self.subdev)
        lay.addWidget(QLabel("<b>Reference clock live state (getReferenceClockState)</b>")); lay.addWidget(self.rc_state)
        lay.addWidget(cfg)
        self.device.currentIndexChanged.connect(lambda _: (self.clock_ch.refresh(), self.pps.refresh(), self.refresh()))
        controller.sync_changed.connect(lambda *_: self.refresh())
        controller.hardware_event.connect(lambda ev, p: self.refresh() if ev in ("connected", "disconnected") else None)
        controller.health.connect(lambda _: self.refresh_state_only())
        self.refresh()

    def refresh(self) -> None:
        dev = self.device.device_id()
        if dev is None:
            self.state.set_status("no device", "neutral"); self.reason.setText(""); self.subdev.setRowCount(0); self.rc_state.set_data({}); return
        try:
            info = self.controller.sync_info(dev)
        except Exception as exc:
            self.state.set_status("error", "error"); self.reason.setText(str(exc)); return
        st = info["sync_state"]
        kind = "ok" if st in ("SYNCED", "REFERENCE_CLOCK_LOCKED") else ("warn" if st in ("SYNCING", "SYNC_REQUIRED") else "error")
        self.state.set_status(st, kind); self.reason.setText(f"{info['reason']} — connection {info['connection_type']}, {info['sub_devices']} sub-device(s)")
        h = self.controller.handle(dev)
        self.subdev.setRowCount(len(h.sub_devices))
        for i, sd in enumerate(h.sub_devices):
            for j, v in enumerate([sd.index, sd.serial, sd.model, sd.address, f"{sd.channels[0]}…{sd.channels[-1]} ({len(sd.channels)})" if sd.channels else ""]):
                self.subdev.setItem(i, j, QTableWidgetItem(str(v)))
        self.refresh_state_only(info)
        cfg = ReferenceClockConfig.from_dict(info.get("config", {}))
        self.enable.setChecked(cfg.enabled)
        if cfg.clock_channel is not None:
            self.clock_ch.set_value(cfg.clock_channel)
        self.freq.setValue(cfg.clock_frequency_hz); self.tc.setValue(cfg.time_constant_s); self.pps.set_value(cfg.synchronization_channel); self.offset.setValue(cfg.synchronization_offset_ps)

    def refresh_state_only(self, info: dict | None = None) -> None:
        dev = self.device.device_id()
        if dev is None:
            return
        try:
            info = info or self.controller.sync_info(dev)
        except Exception:
            return
        rc = info.get("reference_clock") or {}
        flat = {}
        if isinstance(rc, dict) and rc and all(isinstance(v, dict) for v in rc.values()):
            for addr, st in rc.items():
                for k, v in st.items():
                    flat[f"{addr}: {k}"] = v
        else:
            flat = dict(rc)
        if "clock_period" in flat and flat["clock_period"]:
            try:
                flat["clock_frequency"] = format_frequency(1e12 / float(flat["clock_period"]))
            except Exception:
                pass
        display = {"Reference clock": ("LOCKED" if flat.get("is_locked") else ("UNLOCKED" if flat.get("enabled") else "disabled")) if flat else "not available on this device"}
        display["PPS"] = "synchronized" if flat.get("is_synchronized") else ("not synchronized" if flat.get("enabled") else "n/a")
        display.update(flat)
        self.rc_state.set_data(display)

    def apply(self) -> None:
        dev = self.device.device_id()
        if dev is None:
            return
        if not self.controller.handle(dev).capabilities.get("reference_clock"):
            show_error(self, "Unsupported", "This device type (virtual/simulated) has no reference clock."); return
        if self.controller.is_acquiring(dev):
            show_error(self, "Acquisition active", "Stop all acquisitions on this device before changing the reference clock."); return
        if not confirm(self, "Change reference clock", "This alters the internal time base. Data acquired before and after the change are not comparable. Continue?", dangerous=True):
            return
        cfg = ReferenceClockConfig(clock_channel=self.clock_ch.value(), clock_frequency_hz=self.freq.value(), time_constant_s=self.tc.value(), synchronization_channel=self.pps.value(), synchronization_offset_ps=self.offset.value(), wait_until_locked=self.wait.isChecked(), enabled=self.enable.isChecked())
        try:
            self.controller.apply_reference_clock_async(dev, cfg)
        except Exception as exc:
            show_error(self, "Reference clock", str(exc))
