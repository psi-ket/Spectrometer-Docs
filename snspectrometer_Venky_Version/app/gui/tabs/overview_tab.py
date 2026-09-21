"""Overview: real-time hardware dashboard."""
from __future__ import annotations

from typing import Any

from qtpy.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView

from ...models.units import format_rate, format_voltage
from ..widgets.common import KeyValueTable, StatusLabel


class OverviewTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.lib = StatusLabel()
        self.devices_table = QTableWidget(0, 11)
        self.devices_table.setHorizontalHeaderLabels(["Device", "Model", "Serial", "Connection", "Channels", "Active", "Total cps", "Overflows", "Ref clock", "Sync", "Recording"])
        self.devices_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.det_table = QTableWidget(0, 8)
        self.det_table.setHorizontalHeaderLabels(["Detector", "Device", "Ch", "Wavelength", "Rate", "Trigger", "Delay", "Impedance"])
        self.det_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.sys = KeyValueTable()
        self.exp = KeyValueTable()
        btn_refresh = QPushButton("Refresh hardware discovery"); btn_refresh.clicked.connect(controller.discover_async)
        btn_sim = QPushButton("Connect simulator (no hardware)"); btn_sim.clicked.connect(lambda: controller.connect_simulator_async())
        top = QHBoxLayout(); top.addWidget(QLabel("<b>Swabian library:</b>")); top.addWidget(self.lib); top.addStretch(1); top.addWidget(btn_refresh); top.addWidget(btn_sim)
        grid = QGridLayout(self)
        grid.addLayout(top, 0, 0, 1, 2)
        grid.addWidget(self._box("Devices", self.devices_table), 1, 0, 1, 2)
        grid.addWidget(self._box("Detector channels (live)", self.det_table), 2, 0, 1, 2)
        grid.addWidget(self._box("Experiment", self.exp), 3, 0)
        grid.addWidget(self._box("System", self.sys), 3, 1)
        self._rates: dict[str, dict[int, float]] = {}
        self._rate_meas: dict[str, Any] = {}
        controller.hardware_event.connect(self.on_hw)
        controller.health.connect(self.on_health)
        controller.system_stats.connect(lambda s: self.sys.set_data({k: v for k, v in s.items() if k != "time"}))
        controller.detector_map_changed.connect(self.refresh)
        controller.experiment_changed.connect(self.refresh)
        controller.sync_changed.connect(lambda *_: self.refresh())
        self.refresh()

    @staticmethod
    def _box(title: str, w: QWidget) -> QGroupBox:
        b = QGroupBox(title); l = QVBoxLayout(b); l.addWidget(w); return b

    def on_hw(self, ev: str, payload: dict) -> None:
        if ev in ("connected", "disconnected", "discovered", "channel_configured"):
            self.refresh()

    def _ensure_rate_monitor(self, dev_id: str) -> None:
        """A lightweight Countrate per device for the dashboard (created outside any group)."""
        if dev_id in self._rate_meas:
            return
        try:
            from swabian_backend import measurements as M
            h = self.controller.handle(dev_id)
            chans = self.controller.detector_map().physical_channels(dev_id, enabled_only=False) or list(h.channels_rising)
            if not chans:
                return
            self._rate_meas[dev_id] = (M.make_countrate(h.tagger, chans), chans)
        except Exception:
            pass

    def on_health(self, health: dict) -> None:
        d = self.controller.diagnostics()["swabian"]
        self.lib.set_status(f"{'available' if d['available'] else 'MISSING'} {d['version']} ({'supported' if d['version_supported'] else 'unsupported API'})", "ok" if d["available"] and d["version_supported"] else "error")
        for dev_id in self.controller.device_ids():
            self._ensure_rate_monitor(dev_id)
            rm = self._rate_meas.get(dev_id)
            if rm:
                try:
                    rates = rm[0].getData()
                    self._rates[dev_id] = {ch: float(r) for ch, r in zip(rm[1], rates)}
                    rm[0].clear()
                except Exception:
                    pass
        for dev_id in list(self._rate_meas):
            if dev_id not in self.controller.device_ids():
                self._rate_meas.pop(dev_id, None); self._rates.pop(dev_id, None)
        self.refresh(health)

    def refresh(self, health: dict | None = None) -> None:
        health = health or {}
        c = self.controller
        ids = c.device_ids()
        self.devices_table.setRowCount(len(ids))
        for i, dev_id in enumerate(ids):
            rec = c.device_record(dev_id)
            h = rec.handle
            info = health.get(dev_id, {})
            try:
                sync = c.sync_info(dev_id)
            except Exception:
                sync = {}
            rc = sync.get("reference_clock", {})
            rc_txt = ("LOCKED" if rc.get("is_locked") else ("UNLOCKED" if rc.get("enabled") else "off")) if isinstance(rc, dict) and rc else "n/a"
            active = c.detector_map().physical_channels(dev_id, True)
            total = sum(self._rates.get(dev_id, {}).values())
            rec_state = "recording" if any(g.recorder is not None for g in c.active_groups(dev_id)) else ("acquiring" if c.is_acquiring(dev_id) else "idle")
            vals = [dev_id, h.model, h.serial, h.connection_type.value, str(len(h.channels_rising)), str(len(active)), format_rate(total), str(info.get("overflows", h.overflows())), rc_txt, sync.get("sync_state", ""), rec_state]
            for j, v in enumerate(vals):
                self.devices_table.setItem(i, j, QTableWidgetItem(str(v)))
        dets = [d for d in c.detector_map().all() if d.tagger_id in ids]
        self.det_table.setRowCount(len(dets))
        for i, d in enumerate(dets):
            st = c.channel_state_dicts(d.tagger_id).get(d.physical_channel, {})
            r = self._rates.get(d.tagger_id, {}).get(d.physical_channel)
            vals = [d.detector_name, d.tagger_id, str(d.physical_channel), f"{d.wavelength_nm:g} nm" if d.wavelength_nm else "—", format_rate(r) if r is not None else "—", format_voltage(st.get("trigger_level_v")) if st.get("trigger_level_v") is not None else "n/a", f"{st.get('input_delay_ps', d.input_delay_ps)} ps", {"50_OHM": "50 Ω", "HIGH_Z": "High-Z"}.get(st.get("impedance"), "n/a")]
            for j, v in enumerate(vals):
                self.det_table.setItem(i, j, QTableWidgetItem(str(v)))
        e = c.experiment
        self.exp.set_data({"Experiment": e.experiment_name, "ID": e.experiment_id, "Operator": e.operator, "Sample": e.sample, "Pump wavelength [nm]": e.pump_wavelength_nm, "Repetition rate [Hz]": e.laser_repetition_rate_hz, "Data directory": c.settings.data_directory, "Interrupted runs found at start": len(c.interrupted_runs)})
