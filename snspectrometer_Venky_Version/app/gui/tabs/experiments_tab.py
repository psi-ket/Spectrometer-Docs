"""Experiment / run browser."""
from __future__ import annotations

import json
from pathlib import Path

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QPlainTextEdit)

from ...measurements.base import Snapshot
from ...models.results import MeasurementResult
from ...plotting.panel import MeasurementPlotPanel
from ...storage.experiment_browser import scan_data_root, run_summary
from ..widgets.common import KeyValueTable, TextDialog, show_error


class ExperimentsTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Experiments / runs", "Status", "Started"])
        self.tree.currentItemChanged.connect(self.show_item)
        row = QHBoxLayout(); b = QPushButton("Refresh"); b.clicked.connect(self.refresh); row.addWidget(b)
        b2 = QPushButton("Open run folder in Analysis"); b2.clicked.connect(self.to_analysis); row.addWidget(b2); row.addStretch(1)
        left = QWidget(); ll = QVBoxLayout(left); ll.addLayout(row); ll.addWidget(self.tree)
        self.tabs = QTabWidget()
        self.summary = KeyValueTable(); self.tabs.addTab(self.summary, "Configuration")
        self.results_tree = QTreeWidget(); self.results_tree.setHeaderLabels(["Result / analysis files"]); self.results_tree.itemDoubleClicked.connect(self.open_result); self.tabs.addTab(self.results_tree, "Results")
        self.plot_host = QWidget(); self.plot_layout = QVBoxLayout(self.plot_host); self.tabs.addTab(self.plot_host, "Plot")
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.tabs.addTab(self.log, "Logs")
        self.meta = QPlainTextEdit(); self.meta.setReadOnly(True); self.meta.setStyleSheet("font-family: Consolas, monospace;"); self.tabs.addTab(self.meta, "measurement.json")
        split = QSplitter(Qt.Horizontal); split.addWidget(left); split.addWidget(self.tabs); split.setSizes([420, 900])
        lay = QVBoxLayout(self); lay.addWidget(split)
        self.panel = None
        self.selected_run = None
        controller.measurement_event.connect(lambda ev, g: self.refresh() if ev == "finished" else None)
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        for exp in scan_data_root(self.controller.settings.data_directory):
            e = QTreeWidgetItem([f"{exp.name}  ({exp.experiment_id})", "", ""]); e.setData(0, Qt.UserRole, None)
            for run in exp.runs:
                r = QTreeWidgetItem([run.run_id, run.status + (" ⚠" if run.missing_raw else ""), run.timestamp_start[:19]])
                r.setData(0, Qt.UserRole, run)
                if run.status == "INTERRUPTED":
                    r.setForeground(1, Qt.red)
                e.addChild(r)
            self.tree.addTopLevelItem(e)
        self.tree.expandAll()

    def show_item(self, item, _prev=None) -> None:
        if item is None:
            return
        run = item.data(0, Qt.UserRole)
        self.selected_run = run
        if run is None:
            return
        s = run_summary(run)
        self.summary.set_data(s)
        self.results_tree.clear()
        for f in run.result_files:
            self.results_tree.addTopLevelItem(QTreeWidgetItem([f]))
        for f in run.analysis_files:
            self.results_tree.addTopLevelItem(QTreeWidgetItem([f]))
        for f in run.raw_files:
            self.results_tree.addTopLevelItem(QTreeWidgetItem([f + "   (raw TTbin)"]))
        if run.missing_raw:
            self.results_tree.addTopLevelItem(QTreeWidgetItem(["⚠ missing raw files referenced by metadata: " + ", ".join(run.missing_raw)]))
        log_path = Path(run.path) / "run.log"
        self.log.setPlainText(log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "(no run log)")
        self.meta.setPlainText(json.dumps(run.metadata, indent=2, default=str))

    def open_result(self, item, _col=0) -> None:
        path = item.text(0).split("   ")[0]
        if not path.endswith(".json"):
            return
        try:
            res = MeasurementResult.load(path)
        except Exception as exc:
            show_error(self, "Load failed", str(exc)); return
        from ...measurements.registry import get_measurement_class
        specs = []
        try:
            cls = get_measurement_class(res.measurement_type)
            specs = cls(cls.ConfigClass.from_dict(res.configuration.get("parameters", {}))).plot_specs()
        except Exception:
            pass
        if self.panel is not None:
            self.plot_layout.removeWidget(self.panel); self.panel.deleteLater(); self.panel = None
        if specs:
            self.panel = MeasurementPlotPanel(specs)
            self.panel.update_snapshot(Snapshot(0.0, int(res.metadata.get("capture_duration_ps", 0)), res.arrays, res.scalars, True, res.metadata.get("labels", {})))
            self.plot_layout.addWidget(self.panel)
            self.tabs.setCurrentWidget(self.plot_host)
        else:
            TextDialog(Path(path).name, json.dumps(res.json_dict(), indent=2, default=str), self).exec()

    def to_analysis(self) -> None:
        run = self.selected_run
        if run is None or not run.raw_files:
            show_error(self, "No raw file", "This run has no raw TTbin."); return
        main = self.window()
        if hasattr(main, "open_in_analysis"):
            main.open_in_analysis(run.raw_files[0])
