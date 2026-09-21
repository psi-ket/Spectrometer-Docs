"""pyqtgraph-based scientific plot widgets with export and cursor readout."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import pyqtgraph as pg
from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QGuiApplication
from qtpy.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from .decimation import decimate_minmax, decimate_matrix
from .export import PlotExportOptions, export_bars, export_heatmap, export_lines

pg.setConfigOptions(antialias=False, background="w", foreground="k")

PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]


def _pen(i: int, width: float = 1.3):
    return pg.mkPen(PALETTE[i % len(PALETTE)], width=width)


def _label(plot: pg.PlotWidget, axis: str, text: str, unit: str = "") -> None:
    """Axis label with the unit in brackets; SI auto-prefixing (e.g. 'kps') is disabled."""
    ax = plot.getAxis(axis)
    ax.enableAutoSIPrefix(False)
    plot.setLabel(axis, f"{text} [{unit}]" if unit else text)


class ExportDialog(QDialog):
    def __init__(self, parent=None, opts: Optional[PlotExportOptions] = None):
        super().__init__(parent)
        self.setWindowTitle("Export plot")
        self.opts = opts or PlotExportOptions()
        form = QFormLayout(self)
        self.w = QDoubleSpinBox(); self.w.setRange(1, 40); self.w.setValue(self.opts.width_in)
        self.h = QDoubleSpinBox(); self.h.setRange(1, 40); self.h.setValue(self.opts.height_in)
        self.dpi = QSpinBox(); self.dpi.setRange(50, 1200); self.dpi.setValue(self.opts.dpi)
        self.font = QDoubleSpinBox(); self.font.setRange(4, 40); self.font.setValue(self.opts.font_size)
        self.lw = QDoubleSpinBox(); self.lw.setRange(0.1, 10); self.lw.setSingleStep(0.1); self.lw.setValue(self.opts.line_width)
        self.title = QLineEdit(self.opts.title)
        self.xl = QLineEdit(self.opts.x_label)
        self.yl = QLineEdit(self.opts.y_label)
        self.legend = QCheckBox(); self.legend.setChecked(self.opts.legend)
        self.grid = QCheckBox(); self.grid.setChecked(self.opts.grid)
        self.xlim = QLineEdit("" if not self.opts.x_limits else f"{self.opts.x_limits[0]}, {self.opts.x_limits[1]}")
        self.ylim = QLineEdit("" if not self.opts.y_limits else f"{self.opts.y_limits[0]}, {self.opts.y_limits[1]}")
        for lab, w in (("Width [in]", self.w), ("Height [in]", self.h), ("DPI", self.dpi), ("Font size", self.font), ("Line width", self.lw), ("Title", self.title), ("X label", self.xl), ("Y label", self.yl), ("Legend", self.legend), ("Grid", self.grid), ("X limits (min, max)", self.xlim), ("Y limits (min, max)", self.ylim)):
            form.addRow(lab, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def options(self) -> PlotExportOptions:
        o = self.opts
        o.width_in, o.height_in, o.dpi, o.font_size, o.line_width = self.w.value(), self.h.value(), self.dpi.value(), self.font.value(), self.lw.value()
        o.title, o.x_label, o.y_label, o.legend, o.grid = self.title.text(), self.xl.text(), self.yl.text(), self.legend.isChecked(), self.grid.isChecked()

        def lim(text):
            try:
                a, b = [float(v) for v in text.split(",")]
                return (a, b)
            except Exception:
                return None

        o.x_limits, o.y_limits = lim(self.xlim.text()), lim(self.ylim.text())
        return o


class PlotToolbar(QWidget):
    """Common controls: log axes, autoscale, grid, legend, export, cursor readout."""

    def __init__(self, parent=None, show_log_x: bool = True):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.log_x = QCheckBox("log X"); self.log_y = QCheckBox("log Y"); self.grid = QCheckBox("grid"); self.legend = QCheckBox("legend")
        self.grid.setChecked(True); self.legend.setChecked(True)
        self.autoscale = QPushButton("Autoscale")
        self.export_btn = QToolButton(); self.export_btn.setText("Export ▾"); self.export_btn.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self.export_btn)
        self.act_png = self.menu.addAction("Save PNG (matplotlib)")
        self.act_svg = self.menu.addAction("Save SVG")
        self.act_pdf = self.menu.addAction("Save PDF")
        self.act_screen = self.menu.addAction("Save screen PNG (as displayed)")
        self.act_copy = self.menu.addAction("Copy image")
        self.export_btn.setMenu(self.menu)
        self.readout = QLabel("")
        self.readout.setMinimumWidth(180)
        if show_log_x:
            lay.addWidget(self.log_x)
        lay.addWidget(self.log_y); lay.addWidget(self.grid); lay.addWidget(self.legend); lay.addWidget(self.autoscale); lay.addWidget(self.export_btn); lay.addStretch(1); lay.addWidget(self.readout)


class SciLinePlot(QWidget):
    """Multi-series line/histogram plot. Keeps raw arrays for export; decimates for display."""

    def __init__(self, title: str = "", x_label: str = "", x_unit: str = "", y_label: str = "", y_unit: str = "", parent=None, max_points: int = 4000):
        super().__init__(parent)
        self.title, self.x_label, self.x_unit, self.y_label, self.y_unit = title, x_label, x_unit, y_label, y_unit
        self.max_points = max_points
        self.toolbar = PlotToolbar(self)
        self.plot = pg.PlotWidget(title=title)
        _label(self.plot, "bottom", x_label, x_unit)
        _label(self.plot, "left", y_label, y_unit)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.addLegend(offset=(10, 10))
        self._legend = self.plot.plotItem.legend
        self.curves: list[pg.PlotDataItem] = []
        self.error_items: list[pg.ErrorBarItem] = []
        self._x = np.array([]); self._ys: list[np.ndarray] = []; self._labels: list[str] = []; self._errors: Optional[list[np.ndarray]] = None
        self._step = False; self._symbols = False
        self._manual_range = False
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.toolbar); lay.addWidget(self.plot)
        self.toolbar.log_x.toggled.connect(self._apply_log)
        self.toolbar.log_y.toggled.connect(self._apply_log)
        self.toolbar.grid.toggled.connect(lambda v: self.plot.showGrid(x=v, y=v, alpha=0.3))
        self.toolbar.legend.toggled.connect(lambda v: self._legend.setVisible(v))
        self.toolbar.autoscale.clicked.connect(self._autoscale)
        self.toolbar.act_png.triggered.connect(lambda: self.export("png"))
        self.toolbar.act_svg.triggered.connect(lambda: self.export("svg"))
        self.toolbar.act_pdf.triggered.connect(lambda: self.export("pdf"))
        self.toolbar.act_screen.triggered.connect(self.save_screen_png)
        self.toolbar.act_copy.triggered.connect(self.copy_image)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse)
        self.plot.plotItem.vb.sigRangeChangedManually.connect(lambda *_: setattr(self, "_manual_range", True))
        self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", style=Qt.DashLine))
        self.plot.addItem(self.vline, ignoreBounds=True)
        self.vline.hide()

    # ------------------------------------------------------------------ data
    def set_series(self, x: np.ndarray, ys: Sequence[np.ndarray] | np.ndarray, labels: Optional[Sequence[str]] = None, step: bool = False, symbols: bool = False, errors: Optional[Sequence[np.ndarray]] = None) -> None:
        x = np.asarray(x)
        ys_list = [np.asarray(y) for y in (ys if isinstance(ys, (list, tuple)) else (np.atleast_2d(ys) if np.asarray(ys).ndim == 2 else [ys]))]
        labels = list(labels or [f"series {i}" for i in range(len(ys_list))])
        self._x, self._ys, self._labels, self._step, self._symbols = x, ys_list, labels, step, symbols
        self._errors = [np.asarray(e) for e in errors] if errors is not None else None
        while len(self.curves) < len(ys_list):
            i = len(self.curves)
            c = self.plot.plot([], [], pen=_pen(i), name=labels[i] if i < len(labels) else None)
            self.curves.append(c)
        for i, c in enumerate(self.curves):
            if i >= len(ys_list):
                c.setData([], [])
                continue
            y = ys_list[i]
            n = min(x.size, y.size)
            xd, yd = decimate_minmax(x[:n], y[:n], self.max_points)
            xd = np.asarray(xd, dtype=float); yd = np.asarray(yd, dtype=float)
            if self.toolbar.log_y.isChecked():
                yd = np.where(yd > 0, yd, np.nan)
            if step and xd.size > 1 and not symbols:
                # stepMode="center" needs len(x) == len(y) + 1 (bin edges)
                mids = (xd[1:] + xd[:-1]) / 2.0
                edges = np.concatenate([[xd[0] - (mids[0] - xd[0])], mids, [xd[-1] + (xd[-1] - mids[-1])]])
                c.setData(edges, yd, stepMode="center", pen=_pen(i), symbol=None)
            else:
                c.setData(xd, yd, stepMode=None, pen=_pen(i), symbol="o" if symbols else None, symbolSize=5, symbolBrush=PALETTE[i % len(PALETTE)])
            c.setVisible(True)
        for it in self.error_items:
            self.plot.removeItem(it)
        self.error_items = []
        if self._errors is not None:
            for i, e in enumerate(self._errors):
                if i >= len(ys_list) or e is None:
                    continue
                n = min(x.size, ys_list[i].size, np.asarray(e).size)
                if n > 3000:
                    continue
                it = pg.ErrorBarItem(x=x[:n], y=ys_list[i][:n], height=2 * np.asarray(e)[:n], beam=0.0, pen=_pen(i, 0.8))
                self.plot.addItem(it)
                self.error_items.append(it)
        if not self._manual_range:
            self.plot.enableAutoRange()

    def clear(self) -> None:
        for c in self.curves:
            c.setData([], [])
        self._x, self._ys = np.array([]), []

    def set_labels(self, x_label: str, x_unit: str, y_label: str, y_unit: str) -> None:
        self.x_label, self.x_unit, self.y_label, self.y_unit = x_label, x_unit, y_label, y_unit
        _label(self.plot, "bottom", x_label, x_unit)
        _label(self.plot, "left", y_label, y_unit)

    # ------------------------------------------------------------------ view
    def _apply_log(self) -> None:
        self.plot.setLogMode(self.toolbar.log_x.isChecked(), self.toolbar.log_y.isChecked())
        if self._ys:
            self.set_series(self._x, self._ys, self._labels, self._step, self._symbols, self._errors)

    def _autoscale(self) -> None:
        self._manual_range = False
        self.plot.enableAutoRange()
        self.plot.autoRange()

    def _on_mouse(self, pos) -> None:
        vb = self.plot.plotItem.vb
        if not self.plot.sceneBoundingRect().contains(pos):
            return
        p = vb.mapSceneToView(pos)
        x = 10 ** p.x() if self.toolbar.log_x.isChecked() else p.x()
        text = f"x = {x:.6g} {self.x_unit}"
        if self._x.size and self._ys:
            i = int(np.clip(np.searchsorted(self._x, x), 0, self._x.size - 1))
            vals = [f"{self._labels[k] if k < len(self._labels) else k}: {y[i]:.5g}" for k, y in enumerate(self._ys[:4]) if i < y.size]
            text += "  " + "; ".join(vals)
            self.vline.setPos(np.log10(self._x[i]) if self.toolbar.log_x.isChecked() and self._x[i] > 0 else self._x[i])
            self.vline.show()
        self.toolbar.readout.setText(text)

    # ------------------------------------------------------------------ export
    def export_options(self) -> PlotExportOptions:
        return PlotExportOptions(title=self.title, x_label=self.x_label, y_label=self.y_label, log_x=self.toolbar.log_x.isChecked(), log_y=self.toolbar.log_y.isChecked(), legend=self.toolbar.legend.isChecked(), grid=self.toolbar.grid.isChecked())

    def export(self, fmt: str, path: Optional[str] = None, opts: Optional[PlotExportOptions] = None) -> Optional[Path]:
        if not self._ys:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, f"Save {fmt.upper()}", f"{self.title or 'plot'}.{fmt}", f"{fmt.upper()} (*.{fmt})")
            if not path:
                return None
            dlg = ExportDialog(self, self.export_options())
            if dlg.exec() != QDialog.Accepted:
                return None
            opts = dlg.options()
        opts = opts or self.export_options()
        return export_lines(path, self._x, self._ys, self._labels, opts, self.x_unit, self.y_unit, self._step, self._errors, self._symbols)

    def save_screen_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save screen PNG", f"{self.title or 'plot'}_screen.png", "PNG (*.png)")
        if path:
            self.plot.grab().save(path)

    def copy_image(self) -> None:
        QGuiApplication.clipboard().setPixmap(self.plot.grab())


class HeatmapPlot(QWidget):
    """2-D matrix as a colour map with numeric axes (uniform or non-uniform centres)."""

    cell_clicked = Signal(int, int)

    def __init__(self, title: str = "", x_label: str = "", x_unit: str = "", y_label: str = "", y_unit: str = "", value_label: str = "", parent=None):
        super().__init__(parent)
        self.title, self.x_label, self.x_unit, self.y_label, self.y_unit, self.value_label = title, x_label, x_unit, y_label, y_unit, value_label
        self.toolbar = PlotToolbar(self, show_log_x=False)
        self.toolbar.log_y.setText("log colour")
        self.toolbar.legend.hide()
        self.plot = pg.PlotWidget(title=title)
        _label(self.plot, "bottom", x_label, x_unit)
        _label(self.plot, "left", y_label, y_unit)
        self.image = pg.ImageItem()
        self.plot.addItem(self.image)
        self.cmap = pg.colormap.get("viridis")
        self.bar = pg.ColorBarItem(colorMap=self.cmap, label=value_label)
        self.bar.setImageItem(self.image, insert_in=self.plot.plotItem)
        self._m = np.zeros((1, 1)); self._x = None; self._y = None; self._xt = None; self._yt = None
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.toolbar); lay.addWidget(self.plot)
        self.toolbar.log_y.toggled.connect(lambda _: self._refresh())
        self.toolbar.grid.toggled.connect(lambda v: self.plot.showGrid(x=v, y=v, alpha=0.3))
        self.toolbar.autoscale.clicked.connect(lambda: self.plot.autoRange())
        self.toolbar.act_png.triggered.connect(lambda: self.export("png"))
        self.toolbar.act_svg.triggered.connect(lambda: self.export("svg"))
        self.toolbar.act_pdf.triggered.connect(lambda: self.export("pdf"))
        self.toolbar.act_screen.triggered.connect(self.save_screen_png)
        self.toolbar.act_copy.triggered.connect(lambda: QGuiApplication.clipboard().setPixmap(self.plot.grab()))
        self.plot.scene().sigMouseMoved.connect(self._on_mouse)
        self.plot.scene().sigMouseClicked.connect(self._on_click)

    def set_matrix(self, m: np.ndarray, x_axis: Optional[np.ndarray] = None, y_axis: Optional[np.ndarray] = None, x_ticklabels: Optional[list[str]] = None, y_ticklabels: Optional[list[str]] = None) -> None:
        self._m = np.asarray(m, dtype=float)
        self._x = None if x_axis is None else np.asarray(x_axis, dtype=float)
        self._y = None if y_axis is None else np.asarray(y_axis, dtype=float)
        self._xt, self._yt = x_ticklabels, y_ticklabels
        self._refresh()

    def _refresh(self) -> None:
        m = decimate_matrix(self._m)
        disp = m.copy()
        if self.toolbar.log_y.isChecked():
            with np.errstate(divide="ignore", invalid="ignore"):
                disp = np.log10(np.where(disp > 0, disp, np.nan))
        # ImageItem expects (x, y) -> transpose so rows map to y
        self.image.setImage(disp.T, autoLevels=True)
        finite = disp[np.isfinite(disp)]
        if finite.size:
            self.bar.setLevels((float(finite.min()), float(finite.max()) if finite.max() > finite.min() else float(finite.min()) + 1))
        ny, nx = m.shape
        if self._x is not None and self._y is not None and self._x.size == nx and self._y.size == ny and nx > 1 and ny > 1 and np.all(np.diff(self._x) > 0) and np.all(np.diff(self._y) > 0):
            x0, x1 = self._x[0] - (self._x[1] - self._x[0]) / 2, self._x[-1] + (self._x[-1] - self._x[-2]) / 2
            y0, y1 = self._y[0] - (self._y[1] - self._y[0]) / 2, self._y[-1] + (self._y[-1] - self._y[-2]) / 2
            self.image.setRect(pg.QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
            for axis, ticks in (("bottom", None), ("left", None)):
                self.plot.getAxis(axis).setTicks(None)
        else:
            self.image.setRect(pg.QtCore.QRectF(-0.5, -0.5, nx, ny))
            if self._xt:
                self.plot.getAxis("bottom").setTicks([[(i, t) for i, t in enumerate(self._xt)]])
            if self._yt:
                self.plot.getAxis("left").setTicks([[(i, t) for i, t in enumerate(self._yt)]])

    def _index_at(self, pos) -> Optional[tuple[int, int]]:
        vb = self.plot.plotItem.vb
        if not self.plot.sceneBoundingRect().contains(pos):
            return None
        p = vb.mapSceneToView(pos)
        ny, nx = self._m.shape
        if self._x is not None and self._y is not None and self._x.size == nx and self._y.size == ny and nx > 1 and ny > 1:
            j = int(np.argmin(np.abs(self._x - p.x())))
            i = int(np.argmin(np.abs(self._y - p.y())))
        else:
            j, i = int(round(p.x())), int(round(p.y()))
        if 0 <= i < ny and 0 <= j < nx:
            return i, j
        return None

    def _on_mouse(self, pos) -> None:
        ij = self._index_at(pos)
        if ij is None:
            return
        i, j = ij
        xs = f"{self._x[j]:.6g}" if self._x is not None and j < self._x.size else (self._xt[j] if self._xt else str(j))
        ys = f"{self._y[i]:.6g}" if self._y is not None and i < self._y.size else (self._yt[i] if self._yt else str(i))
        self.toolbar.readout.setText(f"x={xs} y={ys} value={self._m[i, j]:.5g}")

    def _on_click(self, ev) -> None:
        ij = self._index_at(ev.scenePos())
        if ij is not None:
            self.cell_clicked.emit(*ij)

    def export(self, fmt: str, path: Optional[str] = None) -> Optional[Path]:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, f"Save {fmt.upper()}", f"{self.title or 'heatmap'}.{fmt}", f"{fmt.upper()} (*.{fmt})")
            if not path:
                return None
            dlg = ExportDialog(self, PlotExportOptions(title=self.title, x_label=self.x_label, y_label=self.y_label, log_y=self.toolbar.log_y.isChecked()))
            if dlg.exec() != QDialog.Accepted:
                return None
            opts = dlg.options()
        else:
            opts = PlotExportOptions(title=self.title, x_label=self.x_label, y_label=self.y_label, log_y=self.toolbar.log_y.isChecked())
        return export_heatmap(path, self._m, self._x, self._y, opts, self.x_unit, self.y_unit, self.value_label, self._xt, self._yt, annotate=self._m.size <= 400)

    def save_screen_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save screen PNG", f"{self.title or 'heatmap'}_screen.png", "PNG (*.png)")
        if path:
            self.plot.grab().save(path)


class MatrixPlot(QWidget):
    """N x N matrix: heatmap + numeric table + quantity/symmetry/diagonal controls."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.heatmap = HeatmapPlot(title, "Stop channel", "", "Start channel", "", "value")
        self.table = QTableWidget()
        self.quantity = QComboBox()
        self.symmetric = QCheckBox("symmetric (i,j)+(j,i) / 2")
        self.show_diag = QCheckBox("show diagonal"); self.show_diag.setChecked(True)
        self.wavelength_axes = QCheckBox("wavelength axes"); self.wavelength_axes.setChecked(True)
        ctrl = QHBoxLayout(); ctrl.addWidget(QLabel("Quantity:")); ctrl.addWidget(self.quantity); ctrl.addWidget(self.symmetric); ctrl.addWidget(self.show_diag); ctrl.addWidget(self.wavelength_axes); ctrl.addStretch(1)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addLayout(ctrl); lay.addWidget(self.heatmap, 3); lay.addWidget(self.table, 2)
        self._data: dict[str, np.ndarray] = {}; self._labels: list[str] = []; self._wl: Optional[np.ndarray] = None
        for w in (self.symmetric, self.show_diag, self.wavelength_axes):
            w.toggled.connect(lambda _: self._refresh())
        self.quantity.currentIndexChanged.connect(lambda _: self._refresh())

    def set_quantities(self, keys: Sequence[str]) -> None:
        cur = self.quantity.currentText()
        self.quantity.blockSignals(True)
        self.quantity.clear()
        self.quantity.addItems(list(keys))
        if cur in keys:
            self.quantity.setCurrentText(cur)
        self.quantity.blockSignals(False)

    def set_data(self, data: dict[str, np.ndarray], labels: Sequence[str], wavelengths: Optional[np.ndarray] = None) -> None:
        self._data = {k: np.asarray(v, dtype=float) for k, v in data.items() if np.asarray(v).ndim == 2}
        self._labels = list(labels)
        self._wl = None if wavelengths is None else np.asarray(wavelengths, dtype=float)
        if self.quantity.count() == 0:
            self.set_quantities(list(self._data))
        self._refresh()

    def current_matrix(self) -> Optional[np.ndarray]:
        key = self.quantity.currentText()
        m = self._data.get(key)
        if m is None:
            return None
        m = m.copy()
        if self.symmetric.isChecked() and m.shape[0] == m.shape[1]:
            m = (m + m.T) / 2.0
        if not self.show_diag.isChecked() and m.shape[0] == m.shape[1]:
            np.fill_diagonal(m, np.nan)
        return m

    def _refresh(self) -> None:
        m = self.current_matrix()
        if m is None:
            return
        use_wl = self.wavelength_axes.isChecked() and self._wl is not None and self._wl.size == m.shape[0] and np.all(np.isfinite(self._wl)) and np.all(np.diff(self._wl) > 0)
        if use_wl:
            self.heatmap.set_matrix(m, self._wl, self._wl)
            _label(self.heatmap.plot, "bottom", "Wavelength (stop)", "nm")
            _label(self.heatmap.plot, "left", "Wavelength (start)", "nm")
        else:
            self.heatmap.set_matrix(m, None, None, self._labels, self._labels)
            _label(self.heatmap.plot, "bottom", "Stop channel")
            _label(self.heatmap.plot, "left", "Start channel")
        self.heatmap.value_label = self.quantity.currentText()
        self.heatmap.bar.getAxis("right").setLabel(self.quantity.currentText())
        n, k = m.shape
        self.table.setRowCount(n); self.table.setColumnCount(k)
        self.table.setHorizontalHeaderLabels(self._labels[:k] if len(self._labels) >= k else [str(i) for i in range(k)])
        self.table.setVerticalHeaderLabels(self._labels[:n] if len(self._labels) >= n else [str(i) for i in range(n)])
        for i in range(n):
            for j in range(k):
                v = m[i, j]
                self.table.setItem(i, j, QTableWidgetItem("" if not np.isfinite(v) else (f"{v:.0f}" if abs(v) >= 100 or v == int(v) else f"{v:.4g}")))
        self.table.resizeColumnsToContents()


class BarPlot(QWidget):
    def __init__(self, title: str = "", y_label: str = "", y_unit: str = "", parent=None):
        super().__init__(parent)
        self.title, self.y_label, self.y_unit = title, y_label, y_unit
        self.toolbar = PlotToolbar(self, show_log_x=False)
        self.toolbar.legend.hide()
        self.plot = pg.PlotWidget(title=title)
        _label(self.plot, "left", y_label, y_unit)
        self.plot.showGrid(x=False, y=True, alpha=0.3)
        self.bars: Optional[pg.BarGraphItem] = None
        self._values = np.array([]); self._labels: list[str] = []; self._errors: Optional[np.ndarray] = None
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.toolbar); lay.addWidget(self.plot)
        self.toolbar.log_y.toggled.connect(lambda v: (self.plot.setLogMode(False, v), self._refresh()))
        self.toolbar.grid.toggled.connect(lambda v: self.plot.showGrid(x=False, y=v, alpha=0.3))
        self.toolbar.autoscale.clicked.connect(lambda: self.plot.autoRange())
        self.toolbar.act_png.triggered.connect(lambda: self.export("png"))
        self.toolbar.act_svg.triggered.connect(lambda: self.export("svg"))
        self.toolbar.act_pdf.triggered.connect(lambda: self.export("pdf"))
        self.toolbar.act_screen.triggered.connect(lambda: self._save_screen())
        self.toolbar.act_copy.triggered.connect(lambda: QGuiApplication.clipboard().setPixmap(self.plot.grab()))
        self.plot.scene().sigMouseMoved.connect(self._on_mouse)

    def set_values(self, values: np.ndarray, labels: Sequence[str], errors: Optional[np.ndarray] = None, short_labels: Optional[Sequence[str]] = None) -> None:
        self._values = np.asarray(values, dtype=float); self._labels = list(labels); self._errors = None if errors is None else np.asarray(errors, dtype=float)
        self._short = list(short_labels) if short_labels is not None else None
        self._refresh()

    def _refresh(self) -> None:
        if self.bars is not None:
            self.plot.removeItem(self.bars)
        x = np.arange(self._values.size)
        vals = self._values
        if self.toolbar.log_y.isChecked():
            vals = np.where(vals > 0, vals, np.nan)
        self.bars = pg.BarGraphItem(x=x, height=np.nan_to_num(vals), width=0.7, brush=PALETTE[0])
        self.plot.addItem(self.bars)
        ticks = self._short if getattr(self, "_short", None) and len(self._short) == len(self._labels) else self._labels
        self.plot.getAxis("bottom").setTicks([[(i, lab) for i, lab in enumerate(ticks)]])

    def _on_mouse(self, pos) -> None:
        if not self.plot.sceneBoundingRect().contains(pos) or not self._values.size:
            return
        p = self.plot.plotItem.vb.mapSceneToView(pos)
        i = int(round(p.x()))
        if 0 <= i < self._values.size:
            self.toolbar.readout.setText(f"{self._labels[i] if i < len(self._labels) else i}: {self._values[i]:.5g} {self.y_unit}")

    def export(self, fmt: str, path: Optional[str] = None) -> Optional[Path]:
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, f"Save {fmt.upper()}", f"{self.title or 'bars'}.{fmt}", f"{fmt.upper()} (*.{fmt})")
            if not path:
                return None
        opts = PlotExportOptions(title=self.title, y_label=self.y_label, log_y=self.toolbar.log_y.isChecked())
        return export_bars(path, self._values, self._labels, opts, self.y_unit, self._errors)

    def _save_screen(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save screen PNG", f"{self.title or 'bars'}_screen.png", "PNG (*.png)")
        if path:
            self.plot.grab().save(path)
