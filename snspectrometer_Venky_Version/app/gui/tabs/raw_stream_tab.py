"""Raw stream analysis: FileReader access to timestamps, channels, event types, missed events."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from qtpy.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QComboBox)
from qtpy.QtCore import Qt

from ...analysis.raw_stream import read_events, event_type_summary, interarrival_histogram
from ...plotting.widgets import SciLinePlot
from ..widgets.common import KeyValueTable, show_error


class RawStreamTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.events: dict[str, np.ndarray] = {}
        left = QWidget(); ll = QVBoxLayout(left)
        box = QGroupBox("FileReader"); f = QFormLayout(box)
        r = QHBoxLayout(); self.file = QLineEdit(); b = QPushButton("Open…"); b.clicked.connect(self.open); r.addWidget(self.file); r.addWidget(b); f.addRow("TTbin", r)
        self.max_events = QSpinBox(); self.max_events.setRange(1000, 50_000_000); self.max_events.setValue(1_000_000); f.addRow("Max events to load", self.max_events)
        self.chunk = QSpinBox(); self.chunk.setRange(1000, 10_000_000); self.chunk.setValue(1_000_000); f.addRow("getData(n_events) chunk", self.chunk)
        b_read = QPushButton("Read"); b_read.clicked.connect(self.read); f.addRow(b_read)
        ll.addWidget(box)
        self.summary = KeyValueTable(); ll.addWidget(QLabel("<b>Stream summary</b>")); ll.addWidget(self.summary)
        hb = QGroupBox("Inter-arrival histogram"); hf = QFormLayout(hb)
        self.ch = QComboBox(); self.bw = QSpinBox(); self.bw.setRange(1, 10**9); self.bw.setValue(1000); self.bw.setSuffix(" ps"); self.nb = QSpinBox(); self.nb.setRange(10, 1_000_000); self.nb.setValue(2000)
        hf.addRow("Channel", self.ch); hf.addRow("Bin width", self.bw); hf.addRow("Bins", self.nb)
        b_h = QPushButton("Compute"); b_h.clicked.connect(self.histogram); hf.addRow(b_h)
        ll.addWidget(hb); ll.addStretch(1)
        right = QWidget(); rl = QVBoxLayout(right)
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(["timestamp [ps]", "channel", "event type", "missed events"]); self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.plot = SciLinePlot("Inter-arrival times", "Δt", "ps", "Counts", "counts")
        rl.addWidget(QLabel("<b>First events</b>")); rl.addWidget(self.table, 1); rl.addWidget(self.plot, 1)
        split = QSplitter(Qt.Horizontal); split.addWidget(left); split.addWidget(right); split.setSizes([420, 900])
        lay = QVBoxLayout(self); lay.addWidget(split)

    def open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open TTbin", self.controller.settings.data_directory, "TTbin (*.ttbin)")
        if path:
            self.file.setText(path)

    def read(self) -> None:
        path = self.file.text()
        if not Path(path).exists():
            show_error(self, "File not found", path); return

        def job(progress_cb=None, stop_flag=None):
            return read_events(path, int(self.chunk.value()), int(self.max_events.value()), stop_flag=stop_flag)

        self.controller.run_async("raw_read", job, on_done=self.show)

    def show(self, ev: dict) -> None:
        self.events = ev
        ts, ch, et, me = ev["timestamps"], ev["channels"], ev["event_types"], ev["missed_events"]
        u, c = np.unique(ch, return_counts=True)
        self.summary.set_data({"events loaded": int(ts.size), "time span": f"{(ts[-1] - ts[0]) / 1e12:.6f} s" if ts.size > 1 else "—", "event types": event_type_summary(et), "missed events total": int(me.sum()), "overflow present": bool((et != 0).any()), "per channel": {int(a): int(b) for a, b in zip(u, c)}})
        n = min(500, ts.size)
        self.table.setRowCount(n)
        for i in range(n):
            for j, v in enumerate((int(ts[i]), int(ch[i]), int(et[i]), int(me[i]))):
                self.table.setItem(i, j, QTableWidgetItem(str(v)))
        self.ch.clear()
        for a in u:
            self.ch.addItem(str(int(a)), int(a))

    def histogram(self) -> None:
        if not self.events:
            return
        chn = self.ch.currentData()
        if chn is None:
            return
        x, h = interarrival_histogram(self.events["timestamps"], self.events["channels"], int(chn), int(self.bw.value()), int(self.nb.value()))
        self.plot.set_series(x.astype(float), [h.astype(float)], [f"channel {chn}"], step=True)
