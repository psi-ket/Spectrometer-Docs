"""Builds plot widgets from a measurement's PlotSpec list and updates them from snapshots."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from qtpy.QtWidgets import QComboBox, QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from ..measurements.base import PlotSpec, Snapshot
from .widgets import BarPlot, HeatmapPlot, MatrixPlot, SciLinePlot

HISTOGRAM_MODES = {"counts": "counts", "counts / s": "cps", "normalized (max = 1)": "norm", "counts / bin width [ps]": "per_ps"}


class MeasurementPlotPanel(QWidget):
    """One tab per PlotSpec; snapshot arrays are mapped to the widgets."""

    def __init__(self, specs: list[PlotSpec], parent=None):
        super().__init__(parent)
        self.specs = specs
        self.tabs = QTabWidget()
        self.widgets: list[QWidget] = []
        self.controls: list[dict[str, Any]] = []
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.tabs)
        for spec in specs:
            page = QWidget(); pl = QVBoxLayout(page); pl.setContentsMargins(0, 0, 0, 0)
            ctrl: dict[str, Any] = {}
            top = QHBoxLayout()
            if spec.kind in ("histogram",):
                mode = QComboBox(); mode.addItems(list(HISTOGRAM_MODES)); ctrl["mode"] = mode
                top.addWidget(QLabel("Display:")); top.addWidget(mode)
            alts = spec.extra.get("alternatives")
            if alts:
                alt = QComboBox(); alt.addItems(list(alts)); ctrl["alt"] = alt
                top.addWidget(QLabel("Quantity:")); top.addWidget(alt)
            top.addStretch(1)
            if top.count() > 1:
                pl.addLayout(top)
            if spec.kind in ("lines", "histogram", "scatter"):
                w = SciLinePlot(spec.title, spec.x_label, spec.x_unit, spec.y_label, spec.y_unit)
                if spec.log_y:
                    w.toolbar.log_y.setChecked(True)
            elif spec.kind in ("heatmap", "jsi"):
                w = HeatmapPlot(spec.title, spec.x_label, spec.x_unit, spec.y_label, spec.y_unit, spec.extra.get("value_label", "counts"))
            elif spec.kind == "matrix":
                w = MatrixPlot(spec.title)
                w.set_quantities(list(spec.y_keys))
            elif spec.kind == "bars":
                w = BarPlot(spec.title, spec.y_label, spec.y_unit)
            else:
                w = SciLinePlot(spec.title, spec.x_label, spec.x_unit, spec.y_label, spec.y_unit)
            pl.addWidget(w)
            self.widgets.append(w)
            self.controls.append(ctrl)
            self.tabs.addTab(page, spec.title)
        self._last: Optional[Snapshot] = None
        for ctrl in self.controls:
            for c in ctrl.values():
                c.currentIndexChanged.connect(lambda _: self._last is not None and self.update_snapshot(self._last))

    # ------------------------------------------------------------------ update
    def update_snapshot(self, snap: Snapshot) -> None:
        self._last = snap
        for spec, w, ctrl in zip(self.specs, self.widgets, self.controls):
            try:
                self._update_one(spec, w, ctrl, snap)
            except Exception as exc:  # never let a plot error kill the GUI loop
                import logging
                logging.getLogger("snspec.plot").debug("plot update failed (%s): %s", spec.title, exc)

    def _series_labels(self, spec: PlotSpec, snap: Snapshot, n: int) -> list[str]:
        labs = snap.labels.get(spec.series_label_key) if spec.series_label_key else None
        if labs and len(labs) >= n:
            return [str(x) for x in labs[:n]]
        if n == 1:
            return [spec.y_keys[0] if spec.y_keys else spec.title]
        return [f"{spec.y_keys[0] if spec.y_keys else 'series'} {i}" for i in range(n)]

    def _update_one(self, spec: PlotSpec, w: QWidget, ctrl: dict[str, Any], snap: Snapshot) -> None:
        A = snap.arrays
        if spec.kind in ("lines", "histogram", "scatter"):
            x = A.get(spec.x_key)
            if x is None:
                return
            key = spec.y_keys[0] if spec.y_keys else None
            if "alt" in ctrl:
                key = spec.extra["alternatives"][ctrl["alt"].currentText()]
            y_label, y_unit = spec.y_label, spec.y_unit
            proj = spec.extra.get("projection_of")
            if proj and proj in A:
                arr = np.asarray(A[proj])
                y = arr.sum(axis=tuple(range(1, arr.ndim))) if arr.ndim > 1 else arr
            else:
                y = A.get(key)
            if y is None:
                return
            y = np.asarray(y, dtype=float)
            ys = list(y) if y.ndim == 2 else [y]
            if "mode" in ctrl:
                mode = HISTOGRAM_MODES[ctrl["mode"].currentText()]
                dur = snap.capture_duration_ps / 1e12
                if mode == "cps" and dur > 0:
                    ys = [v / dur for v in ys]; y_label, y_unit = "Counts per second", "cps"
                elif mode == "norm":
                    ys = [v / np.nanmax(v) if np.nanmax(v) > 0 else v for v in ys]; y_label, y_unit = "Normalized", ""
                elif mode == "per_ps":
                    bw = float(snap.scalars.get("binwidth_ps", 1) or 1)
                    ys = [v / bw for v in ys]; y_label, y_unit = "Counts per ps", "1/ps"
            if "alt" in ctrl:
                y_label = ctrl["alt"].currentText()
            extra_keys = [k for k in spec.y_keys[1:] if k in A and k != key]
            labels = self._series_labels(spec, snap, len(ys))
            for k in extra_keys:
                ys.append(np.asarray(A[k], dtype=float)); labels.append(k)
            errs = None
            ek = spec.extra.get("error_key")
            if ek and ek in A:
                errs = [np.asarray(A[ek], dtype=float)] + [None] * (len(ys) - 1)
            w.set_labels(spec.x_label, spec.x_unit, y_label, y_unit)
            w.set_series(np.asarray(x, dtype=float), ys, labels, step=bool(spec.extra.get("step")) or spec.kind == "histogram", symbols=bool(spec.extra.get("symbols")) or spec.kind == "scatter", errors=errs)
        elif spec.kind in ("heatmap", "jsi"):
            key = spec.y_keys[0] if spec.y_keys else None
            if "alt" in ctrl:
                key = spec.extra["alternatives"][ctrl["alt"].currentText()]
            m = A.get(key)
            if m is None:
                return
            m = np.asarray(m, dtype=float)
            if spec.extra.get("transpose"):
                m = m.T if False else m  # rows = histogram index, columns = delay: y is index
                y_axis = np.arange(m.shape[0], dtype=float)
                x_axis = A.get(spec.x_key)
                w.set_matrix(m, None if x_axis is None else np.asarray(x_axis, dtype=float), y_axis)
                return
            x_axis = A.get(spec.x_key)
            y_axis = A.get(spec.extra.get("y_axis_key", ""))
            if x_axis is not None and y_axis is not None and m.shape == (len(y_axis), len(x_axis)):
                xa, ya = np.asarray(x_axis, dtype=float), np.asarray(y_axis, dtype=float)
                if np.all(np.isfinite(xa)) and np.all(np.isfinite(ya)) and np.all(np.diff(xa) > 0) and np.all(np.diff(ya) > 0):
                    w.set_matrix(m, xa, ya)
                else:
                    xl = snap.labels.get("idler_labels") or [str(v) for v in xa]
                    yl = snap.labels.get("signal_labels") or [str(v) for v in ya]
                    w.set_matrix(m, None, None, xl, yl)
            elif x_axis is not None and m.ndim == 2 and m.shape[0] == len(x_axis):
                # Histogram2D: data[n1, n2] with axis1 rows
                w.set_matrix(m.T, np.asarray(x_axis, dtype=float), np.asarray(y_axis, dtype=float) if y_axis is not None else None)
            else:
                w.set_matrix(m)
        elif spec.kind == "matrix":
            data = {k: A[k] for k in spec.y_keys if k in A}
            labels = snap.labels.get(spec.series_label_key, [])
            wl = A.get(spec.extra.get("wavelength_key", ""))
            w.set_data(data, labels, wl)
        elif spec.kind == "bars":
            key = spec.y_keys[0]
            vals = A.get(key)
            if vals is None:
                return
            labels = snap.labels.get(spec.series_label_key, [str(i) for i in range(len(vals))])
            short = snap.labels.get(spec.extra.get("short_label_key", ""), None)
            err = A.get(spec.extra.get("error_key", ""))
            w.set_values(np.asarray(vals, dtype=float), labels, err, short_labels=short)
