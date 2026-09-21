"""Measurement sequence editor and runner."""
from __future__ import annotations

import json
from typing import Any

from qtpy.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QCheckBox)

from ...models.results import dump_json, load_json
from ...utilities.sequence_runner import STEP_TYPES, Sequence, SequenceStep
from ..widgets.common import show_error, TextDialog
from ..widgets.help_panel import HelpPanel

EXAMPLE = {
    "name": "SPDC characterisation",
    "description": "Example sequence; edit device_id and channels for your setup.",
    "steps": [
        {"name": "Verify hardware", "type": "verify_hardware", "configuration": {"strict": False}, "preconditions": ["hardware_connected"]},
        {"name": "Count rates 5 s", "type": "measurement_group", "duration_s": 5, "configuration": {"device_id": "<device id>", "measurements": [{"type": "countrate", "params": {"channels": [1, 2]}, "name": "rates"}], "raw": {"enabled": False}}},
        {"name": "Delay calibration", "type": "delay_calibration", "duration_s": 2, "configuration": {"device_id": "<device id>", "reference": 1, "channels": [2], "auto_apply": False}},
        {"name": "G2 30 s", "type": "measurement_group", "duration_s": 30, "configuration": {"device_id": "<device id>", "measurements": [{"type": "g2", "params": {"channel_1": 2, "channel_2": 1, "binwidth_ps": 50, "n_bins": 2000}, "name": "g2"}], "raw": {"enabled": True}}, "preconditions": ["sync_ok"]},
        {"name": "Save", "type": "save_all", "configuration": {}},
    ],
}


class SequenceTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.sequence = Sequence("New sequence")
        top = QHBoxLayout(); self.name = QLineEdit(self.sequence.name); top.addWidget(QLabel("Sequence name")); top.addWidget(self.name)
        for text, fn in (("Load…", self.load), ("Save…", self.save), ("Load example", self.load_example)):
            b = QPushButton(text); b.clicked.connect(fn); top.addWidget(b)
        self.table = QTableWidget(0, 6); self.table.setHorizontalHeaderLabels(["#", "Name", "Type", "Duration [s]", "Status", "Message"]); self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        edit = QGroupBox("Step editor (JSON configuration)"); ef = QFormLayout(edit)
        self.s_name = QLineEdit(); self.s_type = QComboBox(); self.s_type.addItems(STEP_TYPES); self.s_dur = QDoubleSpinBox(); self.s_dur.setRange(0, 1e6); self.s_dur.setSuffix(" s")
        self.s_pre = QLineEdit(); self.s_pre.setPlaceholderText("comma list: hardware_connected, sync_ok, no_overflow, not_acquiring")
        self.s_post = QLineEdit(); self.s_save = QCheckBox("save results"); self.s_save.setChecked(True)
        self.s_cfg = QPlainTextEdit(); self.s_cfg.setPlaceholderText('{"device_id": "SIM:...", "measurements": [...]}'); self.s_cfg.setMaximumHeight(140)
        ef.addRow("Name", self.s_name); ef.addRow("Type", self.s_type); ef.addRow("Duration", self.s_dur); ef.addRow("Preconditions", self.s_pre); ef.addRow("Postconditions", self.s_post); ef.addRow("", self.s_save); ef.addRow("Configuration", self.s_cfg)
        er = QHBoxLayout()
        for text, fn in (("Add step", self.add_step), ("Update selected", self.update_step), ("Remove", self.remove_step), ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1))):
            b = QPushButton(text); b.clicked.connect(fn); er.addWidget(b)
        ef.addRow(er)
        run = QHBoxLayout()
        for text, fn in (("▶ Run sequence", self.run), ("⏸ Pause", lambda: self.controller.sequence_runner.pause()), ("Resume", lambda: self.controller.sequence_runner.resume()), ("Skip step", lambda: self.controller.sequence_runner.skip()), ("Repeat step", lambda: self.controller.sequence_runner.repeat()), ("⏹ Stop", lambda: self.controller.sequence_runner.stop())):
            b = QPushButton(text); b.clicked.connect(fn); run.addWidget(b)
        self.status = QLabel(""); self.status.setWordWrap(True)
        lay = QVBoxLayout(self); lay.addLayout(top); lay.addWidget(self.table); lay.addWidget(edit); lay.addLayout(run); lay.addWidget(self.status)
        lay.addWidget(HelpPanel("""Steps run sequentially in a background thread. Step types: apply_preset {"preset": name}, verify_hardware,
measurement_group {"device_id", "measurements": [{"type","params","name"}], "raw": {...}}, delay_calibration
{"device_id","reference","channels","auto_apply"}, wait, save_all. Measurements inside a measurement_group step
always start through SynchronizedMeasurements. Preconditions: hardware_connected, sync_ok, no_overflow, not_acquiring."""))
        self.table.currentCellChanged.connect(lambda r, *_: self.show_step(r))
        controller.sequence_event.connect(self.on_event)

    # ------------------------------------------------------------------ editing
    def _step_from_editor(self) -> SequenceStep:
        try:
            cfg = json.loads(self.s_cfg.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"configuration is not valid JSON: {exc}")
        return SequenceStep(self.s_name.text() or self.s_type.currentText(), self.s_type.currentText(), cfg, self.s_dur.value() or None, [x.strip() for x in self.s_pre.text().split(",") if x.strip()], [x.strip() for x in self.s_post.text().split(",") if x.strip()], self.s_save.isChecked())

    def add_step(self) -> None:
        try:
            self.sequence.steps.append(self._step_from_editor())
        except ValueError as exc:
            show_error(self, "Step", str(exc)); return
        self.refresh()

    def update_step(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        try:
            self.sequence.steps[r] = self._step_from_editor()
        except ValueError as exc:
            show_error(self, "Step", str(exc)); return
        self.refresh()

    def remove_step(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            del self.sequence.steps[r]; self.refresh()

    def move(self, d: int) -> None:
        r = self.table.currentRow(); j = r + d
        if 0 <= r < len(self.sequence.steps) and 0 <= j < len(self.sequence.steps):
            s = self.sequence.steps; s[r], s[j] = s[j], s[r]; self.refresh(); self.table.selectRow(j)

    def show_step(self, r: int) -> None:
        if not (0 <= r < len(self.sequence.steps)):
            return
        s = self.sequence.steps[r]
        self.s_name.setText(s.name); self.s_type.setCurrentText(s.type); self.s_dur.setValue(s.duration_s or 0); self.s_pre.setText(", ".join(s.preconditions)); self.s_post.setText(", ".join(s.postconditions)); self.s_save.setChecked(s.save); self.s_cfg.setPlainText(json.dumps(s.configuration, indent=1))

    def refresh(self) -> None:
        self.sequence.name = self.name.text()
        self.table.setRowCount(len(self.sequence.steps))
        for i, s in enumerate(self.sequence.steps):
            for j, v in enumerate([i + 1, s.name, s.type, s.duration_s or "", s.status, s.message]):
                self.table.setItem(i, j, QTableWidgetItem(str(v)))

    def load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load sequence", "", "JSON (*.json)")
        if path:
            self.sequence = Sequence.from_dict(load_json(path)); self.name.setText(self.sequence.name); self.refresh()

    def save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save sequence", f"{self.name.text()}.json", "JSON (*.json)")
        if path:
            self.sequence.name = self.name.text(); dump_json(self.sequence.to_dict(), path)

    def load_example(self) -> None:
        ex = json.loads(json.dumps(EXAMPLE))
        devs = self.controller.device_ids()
        if devs:
            for st in ex["steps"]:
                if "device_id" in st.get("configuration", {}):
                    st["configuration"]["device_id"] = devs[0]
        self.sequence = Sequence.from_dict(ex); self.name.setText(self.sequence.name); self.refresh()

    # ------------------------------------------------------------------ running
    def run(self) -> None:
        if not self.sequence.steps:
            show_error(self, "Empty sequence", "Add steps first."); return
        for s in self.sequence.steps:
            s.status, s.message = "pending", ""
        try:
            self.controller.run_sequence(self.sequence)
        except Exception as exc:
            show_error(self, "Sequence", str(exc))

    def on_event(self, ev: str, payload: dict) -> None:
        self.refresh()
        if ev == "finished":
            self.status.setText(f"Sequence {payload.get('state')}: " + "; ".join(f"{r['name']}={r['status']}" for r in payload.get("results", [])))
        elif ev == "step_started":
            self.status.setText(f"Running step {payload['index'] + 1}: {payload['step']['name']}")
