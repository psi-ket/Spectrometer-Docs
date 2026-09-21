"""TTbin combiner utility (mergeStreamFiles)."""
from __future__ import annotations

from pathlib import Path

from qtpy.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView)

from ...models.units import format_time_ps
from ...utilities.ttbin_combiner import CombinerJob, MERGE_WARNING
from ..widgets.common import show_error, TextDialog


class CombinerTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.job = CombinerJob()
        warn = QLabel("⚠ " + MERGE_WARNING); warn.setWordWrap(True); warn.setStyleSheet("color: #b00020; font-weight: bold;")
        self.table = QTableWidget(0, 7); self.table.setHorizontalHeaderLabels(["File", "Channels", "Events", "Duration", "Channel offset", "Time offset [ps]", "Config"]); self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        row = QHBoxLayout()
        for text, fn in (("Add file…", self.add), ("Remove", self.remove), ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1)), ("Preview (scan)", self.preview)):
            b = QPushButton(text); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(1)
        out = QHBoxLayout(); self.output = QLineEdit(); b_out = QPushButton("Output…"); b_out.clicked.connect(self.choose_output)
        self.overlap = QCheckBox("overlap only (merge only the time region where all files overlap)")
        out.addWidget(QLabel("Output file")); out.addWidget(self.output); out.addWidget(b_out)
        self.progress = QProgressBar(); self.progress.setRange(0, 0); self.progress.hide()
        b_merge = QPushButton("Merge"); b_merge.setStyleSheet("font-weight: bold;"); b_merge.clicked.connect(self.merge)
        self.status = QLabel(""); self.status.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(warn); lay.addWidget(self.table); lay.addLayout(row); lay.addLayout(out); lay.addWidget(self.overlap); lay.addWidget(b_merge); lay.addWidget(self.progress); lay.addWidget(self.status)
        self.table.cellChanged.connect(self.on_cell)
        controller.task_finished.connect(lambda name, res: self.done(res) if name == "merge" else None)

    def add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add TTbin files", self.controller.settings.data_directory, "TTbin (*.ttbin)")
        for p in paths:
            self.job.add(p)
        self.refresh()

    def remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.job.remove(r); self.refresh()

    def move(self, d: int) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.job.move(r, d); self.refresh(); self.table.selectRow(max(0, min(r + d, len(self.job.inputs) - 1)))

    def preview(self) -> None:
        def job(progress_cb=None, stop_flag=None):
            for ci in self.job.inputs:
                ci.preview()
            return True

        self.controller.run_async("combiner_preview", job, on_done=lambda _: self.refresh())

    def choose_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Output TTbin", self.controller.settings.data_directory, "TTbin (*.ttbin)")
        if path:
            self.output.setText(path); self.job.output = path

    def refresh(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.job.inputs))
        for i, ci in enumerate(self.job.inputs):
            s = ci.scan
            vals = [Path(ci.path).name, ", ".join(map(str, s.channels)) if s else "?", f"{s.n_events:,}" if s else "?", format_time_ps(s.duration_ps) if s else "?", str(ci.channel_offset), str(ci.time_offset_ps), "view" if s else ""]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j not in (4, 5):
                    it.setFlags(it.flags() & ~2)
                self.table.setItem(i, j, it)
        self.table.blockSignals(False)
        self.status.setText("; ".join(self.job.validate()))

    def on_cell(self, r: int, c: int) -> None:
        if r >= len(self.job.inputs):
            return
        try:
            if c == 4:
                self.job.inputs[r].channel_offset = int(self.table.item(r, c).text())
            elif c == 5:
                self.job.inputs[r].time_offset_ps = int(self.table.item(r, c).text())
        except ValueError:
            pass
        self.status.setText("; ".join(self.job.validate()))

    def merge(self) -> None:
        self.job.output = self.output.text()
        problems = self.job.validate()
        blocking = [p for p in problems if not p.startswith("Channel collisions")]
        if blocking:
            show_error(self, "Cannot merge", "; ".join(blocking)); return
        if problems:
            from ..widgets.common import confirm
            if not confirm(self, "Channel collisions", problems[0] + "\n\nMerge anyway?", dangerous=True):
                return
        self.progress.show()
        self.controller.combiner_run_async(self.job)

    def done(self, out) -> None:
        self.progress.hide()
        if out:
            self.status.setText(f"Merged: {out}")
