"""Measurement groups: several measurements started together on one SynchronizedMeasurements."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ...measurements.group import MeasurementGroup, RawRecordingConfig
from ...measurements.registry import get_measurement_class, list_measurement_types
from ...models.results import dump_json, load_json
from ...plotting.panel import MeasurementPlotPanel
from ..widgets.common import scroll, show_error, confirm
from ..widgets.config_form import ConfigForm
from ..widgets.device_selector import DeviceSelector
from ..widgets.help_panel import HelpPanel
from ..widgets.preflight_dialog import PreflightDialog
from ..widgets.run_card import RunCard


class MeasurementEntry(QGroupBox):
    def __init__(self, type_name: str, provider, on_remove, parent=None):
        cls = get_measurement_class(type_name)
        super().__init__(cls.display_name, parent)
        self.type_name = type_name
        self.cls = cls
        self.name = QLineEdit(cls.display_name)
        self.form = ConfigForm(cls.ConfigClass.FIELDS, provider)
        self.form.set_values(asdict(cls.ConfigClass()))
        btn = QPushButton("Remove"); btn.setMaximumWidth(80); btn.clicked.connect(lambda: on_remove(self))
        top = QHBoxLayout(); top.addWidget(QLabel("Name")); top.addWidget(self.name); top.addWidget(btn)
        lay = QVBoxLayout(self); lay.addLayout(top); lay.addWidget(self.form); lay.addWidget(HelpPanel(cls.HELP))

    def spec(self) -> dict[str, Any]:
        return {"type": self.type_name, "params": self.form.values(), "name": self.name.text()}


class GroupsTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.entries: list[MeasurementEntry] = []
        self.group: Optional[MeasurementGroup] = None
        self.panels: dict[str, MeasurementPlotPanel] = {}

        left = QWidget(); ll = QVBoxLayout(left)
        self.device = DeviceSelector(controller)
        ll.addWidget(QLabel("<b>Device</b>")); ll.addWidget(self.device)
        gbox = QGroupBox("Measurement group"); gf = QFormLayout(gbox)
        self.group_name = QLineEdit("Run")
        self.duration = QDoubleSpinBox(); self.duration.setRange(0, 1e6); self.duration.setSuffix(" s"); self.duration.setSpecialValueText("until stopped"); self.duration.setValue(float(controller.settings.default_duration_s))
        self.record_raw = QCheckBox("Record raw TTbin (all enabled physical channels)"); self.record_raw.setChecked(controller.settings.raw_recording_policy == "ALWAYS"); self.record_raw.setEnabled(controller.settings.raw_recording_policy == "ASK")
        self.include_virtual = QCheckBox("include virtual channels of the measurements")
        self.max_size = QSpinBox(); self.max_size.setRange(1, 100000); self.max_size.setSuffix(" MB"); self.max_size.setValue(int(controller.settings.default_max_file_size_mb))
        self.marker = QLineEdit(); self.marker.setPlaceholderText("optional FileWriter marker text")
        self.save_results = QCheckBox("Save JSON + NPZ results"); self.save_results.setChecked(bool(controller.settings.autosave))
        gf.addRow("Group name", self.group_name); gf.addRow("Duration", self.duration); gf.addRow("", self.record_raw); gf.addRow("", self.include_virtual); gf.addRow("Max TTbin file size", self.max_size); gf.addRow("Marker", self.marker); gf.addRow("", self.save_results)
        ll.addWidget(gbox)
        add_row = QHBoxLayout()
        self.type_combo = QComboBox()
        self.refresh_types()
        btn_add = QPushButton("Add measurement"); btn_add.clicked.connect(self.add_entry)
        add_row.addWidget(self.type_combo); add_row.addWidget(btn_add)
        ll.addLayout(add_row)
        self.entries_box = QVBoxLayout(); ll.addLayout(self.entries_box)
        tmpl = QHBoxLayout()
        b_save = QPushButton("Save template…"); b_load = QPushButton("Load template…")
        b_save.clicked.connect(self.save_template); b_load.clicked.connect(self.load_template)
        tmpl.addWidget(b_save); tmpl.addWidget(b_load); ll.addLayout(tmpl)
        ctl = QHBoxLayout()
        self.btn_prepare = QPushButton("Prepare (preflight)"); self.btn_arm = QPushButton("Arm"); self.btn_start = QPushButton("Start"); self.btn_start.setStyleSheet("font-weight: bold;")
        for b in (self.btn_prepare, self.btn_arm, self.btn_start):
            ctl.addWidget(b)
        ll.addLayout(ctl)
        self.status = QLabel(""); self.status.setWordWrap(True); ll.addWidget(self.status)
        ll.addStretch(1)

        right = QWidget(); rl = QVBoxLayout(right)
        self.plot_tabs = QTabWidget(); rl.addWidget(self.plot_tabs, 1)
        self.run_card = RunCard("Group run"); rl.addWidget(self.run_card)
        split = QSplitter(Qt.Horizontal); split.addWidget(scroll(left)); split.addWidget(right); split.setSizes([460, 900])
        lay = QVBoxLayout(self); lay.addWidget(split)

        self.btn_prepare.clicked.connect(self.on_prepare)
        self.btn_arm.clicked.connect(self.on_arm)
        self.btn_start.clicked.connect(self.on_start)
        self.run_card.action.connect(self.on_action)
        self.device.currentIndexChanged.connect(lambda _: [e.form.refresh_channels() for e in self.entries])
        controller.detector_map_changed.connect(lambda: [e.form.refresh_channels() for e in self.entries])
        controller.snapshots.connect(self.on_snapshots)
        controller.measurement_event.connect(self.on_event)
        controller.settings_changed.connect(self.refresh_types)

    # ------------------------------------------------------------------ entries
    def refresh_types(self) -> None:
        mode = self.controller.settings.app_mode
        cats = {"BASIC": ("basic",), "ADVANCED": ("basic", "advanced"), "DEVELOPER": ("basic", "advanced", "developer")}[mode]
        self.type_combo.clear()
        for cls in list_measurement_types():
            if cls.category in cats:
                self.type_combo.addItem(cls.display_name, cls.type_name)

    def add_entry(self, type_name: Optional[str] = None, spec: Optional[dict[str, Any]] = None) -> MeasurementEntry:
        tn = type_name or self.type_combo.currentData()
        e = MeasurementEntry(tn, self.device.channel_provider(), self.remove_entry)
        if spec:
            e.name.setText(spec.get("name", e.name.text()))
            e.form.set_values(spec.get("params", {}))
        self.entries.append(e)
        self.entries_box.addWidget(e)
        return e

    def remove_entry(self, e: MeasurementEntry) -> None:
        self.entries.remove(e)
        self.entries_box.removeWidget(e)
        e.deleteLater()

    def specs(self) -> list[dict[str, Any]]:
        return [e.spec() for e in self.entries]

    def raw_config(self) -> RawRecordingConfig:
        pol = self.controller.settings.raw_recording_policy
        enabled = True if pol == "ALWAYS" else (False if pol == "NEVER" else self.record_raw.isChecked())
        return RawRecordingConfig(enabled=enabled, include_virtual=self.include_virtual.isChecked(), max_file_size_bytes=int(self.max_size.value()) * 1_000_000, filename=self.group_name.text() or "raw", marker=self.marker.text())

    def save_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save group template", "group_template.json", "JSON (*.json)")
        if path:
            dump_json({"group_name": self.group_name.text(), "duration_s": self.duration.value(), "raw": self.raw_config().to_dict(), "measurements": self.specs()}, path)

    def load_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load group template", "", "JSON (*.json)")
        if not path:
            return
        d = load_json(path)
        for e in list(self.entries):
            self.remove_entry(e)
        self.group_name.setText(d.get("group_name", "Run")); self.duration.setValue(float(d.get("duration_s", 10)))
        for spec in d.get("measurements", []):
            self.add_entry(spec["type"], spec)

    # ------------------------------------------------------------------ lifecycle
    def _build(self, save: bool) -> Optional[MeasurementGroup]:
        dev = self.device.device_id()
        if dev is None:
            show_error(self, "No device", "Connect a device first."); return None
        if not self.entries:
            show_error(self, "Empty group", "Add at least one measurement."); return None
        try:
            return self.controller.build_group(self.group_name.text() or "Run", dev, self.specs(), self.duration.value() or None, self.raw_config(), save=save)
        except Exception as exc:
            show_error(self, "Configuration error", str(exc)); return None

    def on_prepare(self) -> None:
        g = self._build(save=False)
        if g is None:
            return
        PreflightDialog(g.preflight(), self, allow_start=False).exec()

    def on_arm(self) -> None:
        if self.group is not None and self.group.status.is_active:
            show_error(self, "Already running", "Stop the current group first."); return
        g = self._build(save=self.save_results.isChecked())
        if g is None:
            return
        g.preflight()
        if not g.preflight_ok:
            PreflightDialog(g.preflight_items, self, allow_start=False).exec(); return
        try:
            g.prepare(); g.arm()
        except Exception as exc:
            show_error(self, "Arming failed", str(exc), g.error); return
        self.group = g
        self._build_panels(g)
        self.status.setText(f"Armed: {g.group_id}. Press Start.")
        self.run_card.update_card(g.run_card())

    def on_start(self) -> None:
        if self.group is None or self.group.status.value != "ARMED":
            self.on_arm()
            if self.group is None or self.group.status.value != "ARMED":
                return
        try:
            self.group.start()
        except Exception as exc:
            show_error(self, "Start failed", str(exc)); return
        self.controller.measurement_event.emit("started", self.group)
        self.status.setText(f"Running: {self.group.group_id}" + (f" → {self.group.run_dir.path}" if self.group.run_dir else ""))
        self.run_card.update_card(self.group.run_card())

    def _build_panels(self, g: MeasurementGroup) -> None:
        self.plot_tabs.clear(); self.panels = {}
        for m in g.measurements:
            p = MeasurementPlotPanel(m.plot_specs())
            self.panels[m.measurement_id] = p
            self.plot_tabs.addTab(p, m.name)

    def on_action(self, action: str) -> None:
        if self.group is None:
            return
        if action == "stop" and not confirm(self, "Stop group", "Stop the running measurement group?"):
            return
        self.controller.group_action(self.group, action)
        self.run_card.update_card(self.group.run_card())

    def on_snapshots(self, snaps: dict) -> None:
        if self.group is None or self.group.group_id not in snaps:
            return
        for mid, snap in snaps[self.group.group_id].items():
            if mid in self.panels:
                self.panels[mid].update_snapshot(snap)
        self.run_card.update_card(self.group.run_card())

    def on_event(self, ev: str, group) -> None:
        if group is not self.group:
            return
        self.run_card.update_card(group.run_card())
        if ev == "finished":
            for m in group.measurements:
                if m.last_snapshot is not None and m.measurement_id in self.panels:
                    self.panels[m.measurement_id].update_snapshot(m.last_snapshot)
            self.status.setText(f"Finished: {group.status.value} ({group.status_reason})" + (f" → {group.run_dir.path}" if group.run_dir else ""))
