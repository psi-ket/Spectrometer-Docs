"""Hardware tab: discovery, connections, device details, startup diagnostics."""
from __future__ import annotations

import json
from typing import Any

from qtpy.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget, QDoubleSpinBox)
from qtpy.QtCore import Qt

from ..widgets.common import KeyValueTable, TextDialog, confirm, show_error


class HardwareTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        # discovery
        disc = QGroupBox("Discovery"); dl = QVBoxLayout(disc)
        row = QHBoxLayout()
        self.btn_scan = QPushButton("Refresh discovery (scanTimeTagger / scanTimeTaggerServers)")
        row.addWidget(self.btn_scan); row.addStretch(1)
        dl.addLayout(row)
        self.local_list = QListWidget(); self.server_list = QListWidget()
        h = QHBoxLayout(); h.addWidget(self._labeled("Local USB devices", self.local_list)); h.addWidget(self._labeled("Network servers", self.server_list))
        dl.addLayout(h)
        self.disc_status = QLabel(""); self.disc_status.setWordWrap(True); dl.addWidget(self.disc_status)
        # connect
        con = QGroupBox("Connect"); cl = QVBoxLayout(con)
        r1 = QHBoxLayout(); self.serial = QLineEdit(); self.serial.setPlaceholderText("serial (empty = first available)")
        self.resolution = QComboBox(); self.resolution.addItems(["Standard", "HighResA", "HighResB", "HighResC"])
        b_local = QPushButton("Connect USB"); b_local.clicked.connect(self.connect_local)
        r1.addWidget(QLabel("USB:")); r1.addWidget(self.serial); r1.addWidget(QLabel("Resolution")); r1.addWidget(self.resolution); r1.addWidget(b_local)
        r2 = QHBoxLayout(); self.addresses = QLineEdit(); self.addresses.setPlaceholderText("host:port[, host2:port] (multiple servers must be synchronized)")
        b_net = QPushButton("Connect network"); b_net.clicked.connect(self.connect_network)
        b_info = QPushButton("Server info"); b_info.clicked.connect(self.server_info)
        r2.addWidget(QLabel("Network:")); r2.addWidget(self.addresses); r2.addWidget(b_net); r2.addWidget(b_info)
        r3 = QHBoxLayout(); self.scenario = QComboBox(); self.scenario.addItems(["spectrometer_jsi", "four_detector_coincidences", "two_detector_pairs"])
        self.sim_duration = QDoubleSpinBox(); self.sim_duration.setRange(2, 600); self.sim_duration.setValue(10); self.sim_duration.setSuffix(" s")
        b_sim = QPushButton("Connect simulator"); b_sim.clicked.connect(self.connect_sim)
        b_sim_file = QPushButton("Simulator from TTbin…"); b_sim_file.clicked.connect(self.connect_sim_file)
        r3.addWidget(QLabel("Simulator:")); r3.addWidget(self.scenario); r3.addWidget(self.sim_duration); r3.addWidget(b_sim); r3.addWidget(b_sim_file)
        for r in (r1, r2, r3):
            cl.addLayout(r)
        # devices
        dev = QGroupBox("Connected devices"); vl = QVBoxLayout(dev)
        self.device_list = QListWidget(); self.device_list.currentRowChanged.connect(self.show_device)
        row2 = QHBoxLayout()
        b_disc = QPushButton("Disconnect"); b_disc.clicked.connect(self.disconnect)
        b_cfg = QPushButton("Show getConfiguration()"); b_cfg.clicked.connect(self.show_config)
        b_ovf = QPushButton("Clear overflows"); b_ovf.clicked.connect(self.clear_overflows)
        b_hwc = QPushButton("Toggle HW delay compensation"); b_hwc.clicked.connect(self.toggle_hwc)
        for b in (b_disc, b_cfg, b_ovf, b_hwc):
            row2.addWidget(b)
        row2.addStretch(1)
        self.details = KeyValueTable()
        vl.addWidget(self.device_list); vl.addLayout(row2); vl.addWidget(self.details)
        diag = QGroupBox("Startup diagnostics"); dgl = QVBoxLayout(diag)
        self.diag = QTextEdit(); self.diag.setReadOnly(True); self.diag.setStyleSheet("font-family: Consolas, monospace;")
        dgl.addWidget(self.diag)
        left = QWidget(); ll = QVBoxLayout(left); ll.addWidget(disc); ll.addWidget(con); ll.addWidget(diag)
        split = QSplitter(Qt.Horizontal); split.addWidget(left); split.addWidget(dev); split.setSizes([600, 700])
        lay = QVBoxLayout(self); lay.addWidget(split)
        self.btn_scan.clicked.connect(controller.discover_async)
        controller.hardware_event.connect(self.on_hw)
        controller.health.connect(lambda h: self.show_device(self.device_list.currentRow()))
        self.refresh_devices()
        self.write_diagnostics()

    @staticmethod
    def _labeled(title: str, w: QWidget) -> QWidget:
        box = QWidget(); l = QVBoxLayout(box); l.setContentsMargins(0, 0, 0, 0); l.addWidget(QLabel(f"<b>{title}</b>")); l.addWidget(w); return box

    # ------------------------------------------------------------------ events
    def on_hw(self, ev: str, payload: dict) -> None:
        if ev == "discovered":
            self.local_list.clear()
            for d in payload.get("local", []):
                self.local_list.addItem(f"{d['serial']}  {d['model']}")
            self.server_list.clear()
            for s in payload.get("servers", []):
                self.server_list.addItem(s)
            errs = payload.get("errors", [])
            self.disc_status.setText(("Errors: " + "; ".join(errs)) if errs else f"{len(payload.get('local', []))} local device(s), {len(payload.get('servers', []))} server(s) found")
            self.write_diagnostics(payload)
        elif ev in ("connected", "disconnected", "state", "error", "channel_configured"):
            self.refresh_devices()
        if ev == "error":
            self.disc_status.setText(f"Error on {payload.get('device_id')}: {payload.get('error')}")

    def write_diagnostics(self, payload: dict | None = None) -> None:
        c = self.controller
        d = c.diagnostics()
        lines = ["Time Taggers detected", "---------------------"]
        payload = payload or {"local": [x.to_dict() for x in c.hardware.discovered], "servers": c.hardware.servers}
        for i, dev in enumerate(payload.get("local", []), 1):
            lines += [f"Device {i}", f"  Serial: {dev['serial']}", f"  Model: {dev['model']}", f"  Connection: LOCAL_USB", "  Status: detected (not connected)" if f"TT:{dev['serial']}" not in c.device_ids() else "  Status: CONNECTED", ""]
        if not payload.get("local"):
            lines += ["  (none)", ""]
        lines += ["Network Time Tagger servers", "---------------------------"] + [f"  {s}" for s in payload.get("servers", [])] + (["  (none)"] if not payload.get("servers") else []) + [""]
        for dev_id in c.device_ids():
            h = c.handle(dev_id)
            sync = c.sync_info(dev_id)
            lines += [f"Connected: {dev_id}", f"  Model: {h.model}  Serial: {h.serial}  Type: {h.connection_type.value}", f"  Channels (rising): {h.channels_rising}", f"  Sub-devices: {len(h.sub_devices)}", f"  Synchronizer: {'yes' if h.connection_type.value == 'SYNCHRONIZED_MULTI_TAGGER' else 'no'}", f"  Reference clock: {sync.get('reference_clock') or 'n/a'}", f"  Overflows: {h.overflows()}", f"  Sync state: {sync.get('sync_state')} ({sync.get('reason')})", ""]
        s = d["swabian"]
        lines += ["License / API status", "--------------------", f"  Library available: {s['available']}", f"  Version: {s['version']} (expected {s['expected_api']}, supported: {s['version_supported']})", f"  Virtual license: {s.get('virtual_license')}", f"  Error: {s.get('error') or '-'}", "", f"Python {d['python']}, NumPy {d['numpy']}, SciPy {d['scipy']}, Qt {d['qt_binding']}, pyqtgraph {d['pyqtgraph']}, {d['os']}"]
        if c.interrupted_runs:
            lines += ["", f"Interrupted runs marked at startup: {len(c.interrupted_runs)}"] + [f"  {r.path}" for r in c.interrupted_runs]
        self.diag.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------ actions
    def connect_local(self) -> None:
        serial = self.serial.text().strip()
        if not serial and self.local_list.currentItem():
            serial = self.local_list.currentItem().text().split()[0]
        res = self.resolution.currentText()
        self.controller.connect_local_async(serial, None if res == "Standard" else res)

    def connect_network(self) -> None:
        text = self.addresses.text().strip()
        if not text and self.server_list.currentItem():
            text = self.server_list.currentItem().text()
        addrs = [a.strip() for a in text.split(",") if a.strip()]
        if not addrs:
            show_error(self, "No address", "Enter host:port or select a discovered server."); return
        if len(addrs) > 1 and not confirm(self, "Multiple servers", "Connecting several servers requires them to be synchronized to a common reference clock (the library refuses otherwise). Continue?"):
            return
        self.controller.connect_network_async(addrs)

    def server_info(self) -> None:
        text = self.addresses.text().strip() or (self.server_list.currentItem().text() if self.server_list.currentItem() else "")
        if not text:
            return
        from swabian_backend.devices import server_info
        try:
            info = server_info(text.split(",")[0].strip())
        except Exception as exc:
            show_error(self, "Server info failed", str(exc)); return
        TextDialog("getTimeTaggerServerInfo", json.dumps(info, indent=2), self).exec()

    def connect_sim(self) -> None:
        self.controller.connect_simulator_async(None, self.scenario.currentText(), self.sim_duration.value())

    def connect_sim_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "TTbin dataset for simulator", "", "TTbin (*.ttbin)")
        if path:
            self.controller.connect_simulator_async(path)

    def selected(self) -> str | None:
        it = self.device_list.currentItem()
        return it.text().split()[0] if it else None

    def disconnect(self) -> None:
        dev = self.selected()
        if not dev:
            return
        if self.controller.is_acquiring(dev) and not confirm(self, "Acquisition active", "Disconnecting will abort the running acquisition (marked INTERRUPTED). Continue?", dangerous=True):
            return
        self.controller.disconnect(dev)

    def show_config(self) -> None:
        dev = self.selected()
        if dev:
            TextDialog(f"getConfiguration() — {dev}", json.dumps(self.controller.handle(dev).get_configuration(), indent=2, default=str), self).exec()

    def clear_overflows(self) -> None:
        dev = self.selected()
        if dev:
            self.controller.hardware.clear_overflows(dev)

    def toggle_hwc(self) -> None:
        dev = self.selected()
        if not dev:
            return
        h = self.controller.handle(dev)
        if not h.capabilities.get("hardware_delay_compensation"):
            show_error(self, "Unsupported", "Hardware delay compensation is not available on this device."); return
        st = self.controller.channel_state_dicts(dev)
        cur = any((v.get("hardware_delay_compensation_ps") or 0) != 0 for v in st.values())
        if confirm(self, "Hardware delay compensation", f"Currently {'active' if cur else 'inactive'}. Set to {'inactive' if cur else 'active'}?"):
            try:
                self.controller.hardware.set_hardware_delay_compensation(dev, not cur)
            except Exception as exc:
                show_error(self, "Failed", str(exc))

    def refresh_devices(self) -> None:
        cur = self.selected()
        self.device_list.clear()
        for dev_id in self.controller.device_ids():
            rec = self.controller.device_record(dev_id)
            self.device_list.addItem(f"{dev_id}  [{rec.state.value}]  {rec.handle.model}")
        for i in range(self.device_list.count()):
            if self.device_list.item(i).text().split()[0] == cur:
                self.device_list.setCurrentRow(i)
        if self.device_list.count() and self.device_list.currentRow() < 0:
            self.device_list.setCurrentRow(0)
        self.write_diagnostics()

    def show_device(self, row: int) -> None:
        dev = self.selected()
        if not dev:
            self.details.set_data({}); return
        rec = self.controller.device_record(dev)
        if rec is None:
            return
        h = rec.handle
        d = h.describe()
        d.update({"state": rec.state.value, "last_error": rec.last_error, "overflows": h.overflows(), "overflow_events_seen": rec.overflow_events, **{f"fw_{k}": v for k, v in h.firmware_info().items()}})
        self.details.set_data(d)
