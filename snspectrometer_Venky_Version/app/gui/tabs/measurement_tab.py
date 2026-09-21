"""Generic single-measurement tab: configuration | live plot, run card, help."""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from ...measurements.group import MeasurementGroup, RawRecordingConfig
from ...measurements.registry import get_measurement_class
from ...models.results import MeasurementResult
from ...plotting.panel import MeasurementPlotPanel
from ..widgets.common import scroll, show_error, confirm
from ..widgets.config_form import ConfigForm
from ..widgets.device_selector import DeviceSelector
from ..widgets.help_panel import HelpPanel
from ..widgets.preflight_dialog import PreflightDialog
from ..widgets.run_card import RunCard

log = logging.getLogger("snspec.gui.measurement")


class MeasurementTab(QWidget):
    def __init__(self, controller, type_name: str, title: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.type_name = type_name
        self.cls = get_measurement_class(type_name)
        self.title = title or self.cls.display_name
        self.group: Optional[MeasurementGroup] = None
        self.panel: Optional[MeasurementPlotPanel] = None
        self.last_snapshot = None

        # ---------------- left: configuration
        left = QWidget()
        ll = QVBoxLayout(left)
        self.device = DeviceSelector(controller)
        ll.addWidget(QLabel("<b>Device</b>"))
        ll.addWidget(self.device)
        self.form = ConfigForm(self.cls.ConfigClass.FIELDS, self.device.channel_provider(), show_advanced=True)
        self.form.set_values(asdict(self.cls.ConfigClass()))
        self.form.changed.connect(lambda: None)
        box = QGroupBox("Parameters")
        bl = QVBoxLayout(box); bl.addWidget(self.form)
        self.preview = QLabel(""); self.preview.setWordWrap(True); self.preview.setStyleSheet("color: #1f4e79;")
        bl.addWidget(self.preview)
        self.extra_box = QVBoxLayout(); bl.addLayout(self.extra_box)
        ll.addWidget(box)
        acq = QGroupBox("Acquisition")
        af = QFormLayout(acq)
        self.run_name = QLineEdit(self.type_name)
        self.duration = QDoubleSpinBox(); self.duration.setRange(0.0, 1e6); self.duration.setDecimals(2); self.duration.setSuffix(" s"); self.duration.setValue(float(controller.settings.default_duration_s)); self.duration.setSpecialValueText("until stopped")
        self.record_raw = QCheckBox("Record raw TTbin (FileWriter)")
        self.record_raw.setChecked(controller.settings.raw_recording_policy == "ALWAYS")
        self.record_raw.setEnabled(controller.settings.raw_recording_policy == "ASK")
        self.include_virtual = QCheckBox("include this measurement's virtual channels in the TTbin")
        self.max_size = QSpinBox(); self.max_size.setRange(1, 100000); self.max_size.setSuffix(" MB"); self.max_size.setValue(int(controller.settings.default_max_file_size_mb))
        self.save_results = QCheckBox("Save JSON + NPZ results"); self.save_results.setChecked(bool(controller.settings.autosave))
        af.addRow("Run name", self.run_name); af.addRow("Duration", self.duration); af.addRow("", self.record_raw); af.addRow("", self.include_virtual); af.addRow("Max TTbin file size", self.max_size); af.addRow("", self.save_results)
        ll.addWidget(acq)
        btns = QHBoxLayout()
        self.btn_preflight = QPushButton("Preflight"); self.btn_start = QPushButton("Arm && Start"); self.btn_start.setStyleSheet("font-weight: bold;")
        self.btn_save = QPushButton("Save snapshot"); self.btn_export = QPushButton("Export data…")
        for b in (self.btn_preflight, self.btn_start, self.btn_save, self.btn_export):
            btns.addWidget(b)
        ll.addLayout(btns)
        self.status = QLabel(""); self.status.setWordWrap(True)
        ll.addWidget(self.status)
        self.help = HelpPanel(self.cls.HELP)
        ll.addWidget(self.help)
        ll.addStretch(1)

        # ---------------- right: plots + run card
        right = QWidget()
        rl = QVBoxLayout(right)
        self.plot_host = QVBoxLayout()
        rl.addLayout(self.plot_host, 1)
        self.run_card = RunCard(f"{self.title} run")
        rl.addWidget(self.run_card)
        self._build_panel(self.cls(self.cls.ConfigClass()).plot_specs())

        split = QSplitter(Qt.Horizontal)
        split.addWidget(scroll(left)); split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1)
        split.setSizes([520, 900])
        lay = QVBoxLayout(self); lay.setContentsMargins(4, 4, 4, 4); lay.addWidget(split)

        # ---------------- wiring
        self.btn_preflight.clicked.connect(self.on_preflight)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_save.clicked.connect(self.on_save_snapshot)
        self.btn_export.clicked.connect(self.on_export)
        self.run_card.action.connect(self.on_card_action)
        self.form.changed.connect(self.update_preview)
        self.device.currentIndexChanged.connect(lambda _: (self.form.refresh_channels(), self.update_preview()))
        controller.detector_map_changed.connect(self.form.refresh_channels)
        controller.snapshots.connect(self.on_snapshots)
        controller.measurement_event.connect(self.on_measurement_event)
        controller.settings_changed.connect(self._apply_policy)
        self.update_preview()

    # ------------------------------------------------------------------ helpers
    def _apply_policy(self) -> None:
        pol = self.controller.settings.raw_recording_policy
        self.record_raw.setChecked(pol == "ALWAYS")
        self.record_raw.setEnabled(pol == "ASK")

    def _build_panel(self, specs) -> None:
        if self.panel is not None:
            self.plot_host.removeWidget(self.panel)
            self.panel.deleteLater()
        self.panel = MeasurementPlotPanel(specs)
        self.plot_host.addWidget(self.panel)

    def params(self) -> dict[str, Any]:
        return self.form.values()

    def update_preview(self) -> None:
        try:
            cfg = self.cls.ConfigClass.from_dict(self.params())
        except Exception as exc:
            self.preview.setText(str(exc)); return
        text = self.preview_text(cfg)
        self.preview.setText(text)

    def preview_text(self, cfg) -> str:
        if hasattr(cfg, "planned_groups"):
            try:
                groups = cfg.planned_groups()
                from swabian_backend.virtual_channels import count_coincidence_groups
                total = count_coincidence_groups(len(cfg.channels), cfg.order)
                warn = "  ⚠ large number of virtual channels" if total > 64 else ""
                return f"Planned groups: {len(groups)} of C({len(cfg.channels)},{cfg.order}) = {total}{warn}"
            except Exception as exc:
                return str(exc)
        return ""

    def raw_config(self) -> RawRecordingConfig:
        pol = self.controller.settings.raw_recording_policy
        enabled = True if pol == "ALWAYS" else (False if pol == "NEVER" else self.record_raw.isChecked())
        return RawRecordingConfig(enabled=enabled, include_virtual=self.include_virtual.isChecked(), max_file_size_bytes=int(self.max_size.value()) * 1_000_000, filename=self.run_name.text() or "raw")

    def specs(self) -> list[dict[str, Any]]:
        return [{"type": self.type_name, "params": self.params(), "name": self.run_name.text() or self.title}]

    # ------------------------------------------------------------------ actions
    def on_preflight(self) -> None:
        dev = self.device.device_id()
        if dev is None:
            show_error(self, "No device", "Connect a Time Tagger, a simulator or open a replay first."); return
        try:
            group = self.controller.build_group(self.run_name.text() or self.title, dev, self.specs(), self.duration.value() or None, self.raw_config(), save=False)
        except Exception as exc:
            show_error(self, "Configuration error", str(exc)); return
        items = group.preflight()
        PreflightDialog(items, self, allow_start=False).exec()

    def on_start(self) -> None:
        dev = self.device.device_id()
        if dev is None:
            show_error(self, "No device", "Connect a Time Tagger, a simulator or open a replay first."); return
        if self.group is not None and self.group.status.is_active:
            show_error(self, "Already running", "Stop the current run first."); return
        try:
            group = self.controller.build_group(self.run_name.text() or self.title, dev, self.specs(), self.duration.value() or None, self.raw_config(), save=self.save_results.isChecked())
        except Exception as exc:
            show_error(self, "Configuration error", str(exc), "", "Check channels and parameters."); return
        items = group.preflight()
        if not group.preflight_ok:
            PreflightDialog(items, self, allow_start=False).exec(); return
        try:
            self.controller.start_group(group)
        except Exception:
            return
        self.group = group
        self._build_panel(group.measurements[0].plot_specs())
        self.status.setText(f"Running: {group.group_id}" + (f"  → {group.run_dir.path}" if group.run_dir else ""))
        self.run_card.update_card(group.run_card())

    def on_card_action(self, action: str) -> None:
        if self.group is None:
            return
        if action == "stop" and not confirm(self, "Stop acquisition", "Stop the running measurement? Results will be finalized and saved."):
            return
        try:
            self.controller.group_action(self.group, action)
        except Exception as exc:
            show_error(self, "Action failed", str(exc))
        self.run_card.update_card(self.group.run_card())

    def on_snapshots(self, snaps: dict) -> None:
        if self.group is None or self.group.group_id not in snaps:
            return
        d = snaps[self.group.group_id]
        m = self.group.measurements[0]
        if m.measurement_id in d:
            self.last_snapshot = d[m.measurement_id]
            if self.panel is not None:
                self.panel.update_snapshot(self.last_snapshot)
        self.run_card.update_card(self.group.run_card())

    def on_measurement_event(self, ev: str, group) -> None:
        if group is not self.group:
            return
        self.run_card.update_card(group.run_card())
        if ev == "finished":
            m = group.measurements[0]
            if m.last_snapshot is not None and self.panel is not None:
                self.last_snapshot = m.last_snapshot
                self.panel.update_snapshot(m.last_snapshot)
            self.status.setText(f"Finished: {group.status.value} ({group.status_reason})" + (f"  → {group.run_dir.path}" if group.run_dir else ""))

    def current_result(self) -> Optional[MeasurementResult]:
        if self.group is None:
            return None
        m = self.group.measurements[0]
        if self.group.results.get(m.measurement_id) is not None:
            return self.group.results[m.measurement_id]
        snap = self.last_snapshot or m.last_snapshot
        if snap is None:
            return None
        return m.build_result(snap, self.group.timestamp_start, "", self.group.status.value, "snapshot", self.group.recorder.files() if self.group.recorder else [], {"group_id": self.group.group_id, "snapshot": True})

    def on_save_snapshot(self) -> None:
        res = self.current_result()
        if res is None:
            show_error(self, "Nothing to save", "Start a measurement first."); return
        directory = str(self.group.run_dir.results_dir) if self.group.run_dir else QFileDialog.getExistingDirectory(self, "Save snapshot to")
        if not directory:
            return
        j, n = res.save(directory, f"{self.type_name}_snapshot_{res.measurement_id}")
        self.status.setText(f"Snapshot saved: {j}")

    def on_export(self) -> None:
        res = self.current_result()
        if res is None:
            show_error(self, "Nothing to export", "Start a measurement first."); return
        path, flt = QFileDialog.getSaveFileName(self, "Export data", f"{self.type_name}.csv", "CSV (*.csv);;NPZ (*.npz);;JSON (*.json)")
        if not path:
            return
        p = Path(path)
        if p.suffix == ".csv":
            res.to_csv(p)
        elif p.suffix == ".npz":
            import numpy as np
            np.savez_compressed(p, **{k: v for k, v in res.arrays.items()})
        else:
            from ...models.results import dump_json
            d = res.json_dict(); d["arrays_data"] = {k: v.tolist() for k, v in res.arrays.items() if v.size <= 200_000}
            dump_json(d, p)
        self.status.setText(f"Exported {p}")


class JSITab(MeasurementTab):
    """JSI tab with helpers to fill signal/idler groups from detector roles."""

    def __init__(self, controller, parent=None):
        super().__init__(controller, "jsi", parent=parent)
        btn = QPushButton("Fill signal / idler from detector roles")
        btn.clicked.connect(self.fill_from_roles)
        self.extra_box.addWidget(btn)
        self.axis_note = QLabel("Axes: Signal wavelength [nm] (rows) vs Idler wavelength [nm] (columns); ordering by calibrated wavelength.")
        self.axis_note.setWordWrap(True)
        self.extra_box.addWidget(self.axis_note)

    def fill_from_roles(self) -> None:
        dev = self.device.device_id()
        if dev is None:
            return
        dm = self.controller.detector_map()
        sig = [c.physical_channel for c in dm.by_role("signal", dev) if c.detector_enabled]
        idl = [c.physical_channel for c in dm.by_role("idler", dev) if c.detector_enabled]
        self.form.set_values({"signal_channels": sig, "idler_channels": idl})
        self.update_preview()

    def preview_text(self, cfg) -> str:
        dm = self.controller.detector_map()
        dev = self.device.device_id()
        missing = [c for c in (cfg.signal_channels + cfg.idler_channels) if dev and (dm.get_physical(dev, c) is None or dm.get_physical(dev, c).wavelength_nm is None)]
        txt = f"{len(cfg.signal_channels)} signal × {len(cfg.idler_channels)} idler detectors"
        if missing:
            txt += f"  ⚠ channels without wavelength calibration: {missing}"
        return txt
