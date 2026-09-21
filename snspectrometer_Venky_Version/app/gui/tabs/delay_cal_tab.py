"""Automatic channel delay calibration tab."""
from __future__ import annotations

import json

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QFileDialog)

from ...plotting.widgets import SciLinePlot
from ..widgets.channel_selector import ChannelCombo, ChannelListWidget
from ..widgets.common import show_error, confirm
from ..widgets.device_selector import DeviceSelector
from ..widgets.help_panel import HelpPanel


class DelayCalibrationTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        left = QWidget(); ll = QVBoxLayout(left)
        self.device = DeviceSelector(controller)
        provider = self.device.channel_provider()
        box = QGroupBox("Calibration"); f = QFormLayout(box)
        self.reference = ChannelCombo(provider); self.channels = ChannelListWidget(provider)
        self.binwidth = QSpinBox(); self.binwidth.setRange(1, 10**6); self.binwidth.setValue(10); self.binwidth.setSuffix(" ps")
        self.n_bins = QSpinBox(); self.n_bins.setRange(10, 10_000_000); self.n_bins.setValue(4000)
        self.duration = QDoubleSpinBox(); self.duration.setRange(0.1, 3600); self.duration.setValue(2.0); self.duration.setSuffix(" s")
        self.window = QSpinBox(); self.window.setRange(0, 10**9); self.window.setValue(1000); self.window.setSuffix(" ps"); self.window.setSpecialValueText("whole histogram")
        self.reset = QCheckBox("Reset input delays to 0 before measuring (manual recipe)"); self.reset.setChecked(True)
        f.addRow("Reference channel", self.reference); f.addRow("Channels to calibrate", self.channels); f.addRow("Bin width", self.binwidth); f.addRow("Bins", self.n_bins); f.addRow("Duration", self.duration); f.addRow("Peak window", self.window); f.addRow("", self.reset)
        ll.addWidget(QLabel("<b>Device</b>")); ll.addWidget(self.device); ll.addWidget(box)
        row = QHBoxLayout()
        self.b_measure = QPushButton("1. Measure correlations"); self.b_apply = QPushButton("2. Apply accepted delays"); self.b_verify = QPushButton("3. Verify (re-measure)"); self.b_report = QPushButton("Save report…")
        for b in (self.b_measure, self.b_apply, self.b_verify, self.b_report):
            row.addWidget(b)
        ll.addLayout(row)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000); ll.addWidget(self.progress)
        self.status = QLabel(""); self.status.setWordWrap(True); ll.addWidget(self.status)
        ll.addWidget(HelpPanel("""Workflow (manual 3.1.3): Correlation(reference, channel) for every selected channel inside one
SynchronizedMeasurements group; delay = count-weighted centre of the correlation peak. Accept or reject
each proposed value, apply with setInputDelay(), then verify that the re-measured peaks sit at zero.
Channels without a significant correlation peak (e.g. uncorrelated detectors) are flagged and not accepted
by default. Nothing is written to the hardware until you press Apply."""))
        ll.addStretch(1)
        right = QWidget(); rl = QVBoxLayout(right)
        self.table = QTableWidget(0, 9); self.table.setHorizontalHeaderLabels(["Channel", "Counts", "Peak [ps]", "Delay [ps]", "FWHM [ps]", "Previous [ps]", "Proposed [ps]", "Accept", "Residual after [ps]"]); self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.before = SciLinePlot("Before: correlation vs reference", "Delay", "ps", "Counts", "counts")
        self.after = SciLinePlot("After: verification", "Delay", "ps", "Counts", "counts")
        rl.addWidget(self.table); rl.addWidget(self.before, 1); rl.addWidget(self.after, 1)
        split = QSplitter(Qt.Horizontal); split.addWidget(left); split.addWidget(right); split.setSizes([430, 900])
        lay = QVBoxLayout(self); lay.addWidget(split)
        self.b_measure.clicked.connect(self.measure); self.b_apply.clicked.connect(self.apply); self.b_verify.clicked.connect(self.verify); self.b_report.clicked.connect(self.save_report)
        self.device.currentIndexChanged.connect(lambda _: (self.reference.refresh(), self.channels.refresh()))
        controller.detector_map_changed.connect(lambda: (self.reference.refresh(), self.channels.refresh()))
        controller.task_progress.connect(lambda name, frac, msg: self.progress.setValue(int(frac * 1000)) if name in ("delay_calibration", "delay_verification") else None)
        controller.task_finished.connect(self.on_task)

    def _dc(self):
        dev = self.device.device_id()
        return self.controller.delay_calibrations.get(dev) if dev else None

    def measure(self) -> None:
        dev = self.device.device_id()
        if dev is None:
            show_error(self, "No device", "Connect a device first."); return
        chans = self.channels.value(); ref = self.reference.value()
        if ref is None or not chans:
            show_error(self, "Selection", "Choose a reference channel and at least one channel to calibrate."); return
        if self.controller.is_acquiring(dev):
            show_error(self, "Acquisition active", "Stop acquisitions on this device first."); return
        try:
            self.controller.delay_calibration_measure_async(dev, ref, chans, self.binwidth.value(), self.n_bins.value(), self.duration.value(), self.window.value(), self.reset.isChecked())
        except Exception as exc:
            show_error(self, "Calibration", str(exc)); return
        self.status.setText("Measuring…"); self.b_measure.setEnabled(False)

    def on_task(self, name: str, payload) -> None:
        if name == "delay_calibration_measured":
            self.b_measure.setEnabled(True); self.show_results(); self.status.setText("Measured. Review, tick Accept, then Apply.")
        elif name == "delay_calibration_verified":
            self.show_results(after=True); self.status.setText("Verification done: residuals shown (should be ≈ 0 ps).")
        elif name in ("delay_calibration", "delay_verification") and payload is None:
            self.b_measure.setEnabled(True)

    def show_results(self, after: bool = False) -> None:
        dc = self._dc()
        if dc is None:
            return
        res = dc.results
        self.table.setRowCount(len(res))
        xs, ys, labels, xa, ya = None, [], [], None, []
        for i, (ch, r) in enumerate(sorted(res.items())):
            vals = [str(ch), str(r.total_counts), f"{r.peak_ps:.0f}", f"{r.delay_ps:.1f}" if np.isfinite(r.delay_ps) else "n/a", f"{r.fwhm_ps:.1f}" if np.isfinite(r.fwhm_ps) else "n/a", str(r.previous_delay_ps), str(r.proposed_delay_ps), "", f"{r.residual_ps:.1f}" if r.residual_ps is not None else ""]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j != 7:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if j == 1 and not r.significant:
                    it.setToolTip(r.significance_note); it.setText(v + " ⚠")
                self.table.setItem(i, j, it)
            cb = QCheckBox(); cb.setChecked(r.significant and (r.accepted or not after)); cb.setToolTip(r.significance_note); self.table.setCellWidget(i, 7, cb)
            xs = r.index_ps.astype(float); ys.append(r.counts.astype(float)); labels.append(f"ch {ch} ({r.significance_note.split('(')[0].strip()})")
            if r.after_counts is not None:
                xa = r.after_index_ps.astype(float); ya.append(r.after_counts.astype(float))
        if xs is not None:
            self.before.set_series(xs, ys, labels, step=True)
        if xa is not None and ya:
            self.after.set_series(xa, ya, labels[: len(ya)], step=True)

    def apply(self) -> None:
        dc = self._dc(); dev = self.device.device_id()
        if dc is None or not dc.results:
            show_error(self, "Nothing measured", "Run step 1 first."); return
        accepted = {}
        for i in range(self.table.rowCount()):
            ch = int(self.table.item(i, 0).text()); accepted[ch] = self.table.cellWidget(i, 7).isChecked()
        if not any(accepted.values()):
            show_error(self, "Nothing accepted", "Tick Accept for at least one channel."); return
        if not confirm(self, "Apply delays", "Apply the accepted delays with setInputDelay()? Existing calibrated values on those channels will be replaced."):
            return
        applied = self.controller.delay_calibration_apply(dev, accepted)
        self.status.setText("Applied: " + ", ".join(f"ch {c}: {d} ps" for c, d in applied.items()))
        self.show_results()

    def verify(self) -> None:
        dev = self.device.device_id()
        if self._dc() is None:
            return
        self.controller.delay_calibration_verify_async(dev)
        self.status.setText("Verifying…")

    def save_report(self) -> None:
        dc = self._dc()
        if dc is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save calibration report", "delay_calibration.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(dc.report(), fh, indent=2, default=str)
            self.status.setText(f"Report saved: {path}")
