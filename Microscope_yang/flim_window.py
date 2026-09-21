"""
flim_window.py — PyQt5 FLIM Window with area scan, lifetime map,
                 single-pixel click-to-measure, and live histogram display.

Layout
------
Left panel:   Parameters (area scan, single-pixel, hardware)
Right panel:  Top    → Lifetime / Intensity map (clickable)
              Bottom → Single-pixel decay histogram + fit
"""

import os
import time
import gc
import numpy as np
from pathlib import Path
import json

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QSpinBox, QDoubleSpinBox,
    QCheckBox, QPushButton, QComboBox, QProgressBar,
    QFileDialog, QMessageBox, QSplitter, QFrame, QSizePolicy,
    QAction, QStatusBar, QScrollArea,
)

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

from scan_engine import ScanConfig, ScanWorker
from flim_analysis import FLIMAnalyzer, plot_flim_with_fit


# ═══════════════════════════════════════════════════════════════════════════
# COLOUR PALETTE  (shared dark theme)
# ═══════════════════════════════════════════════════════════════════════════

BG_DARK       = "#0f1117"
BG_PANEL      = "#181b24"
BG_GROUP      = "#1e2230"
BG_INPUT      = "#262a38"
BORDER        = "#2d3348"
ACCENT        = "#6c8dfa"
ACCENT_HOVER  = "#8aa5ff"
ACCENT_PRESS  = "#4e6ee0"
TEXT_PRIMARY   = "#e2e4eb"
TEXT_SECONDARY = "#8b90a5"
SUCCESS       = "#4cd97b"
DANGER        = "#f06c75"
WARNING       = "#e5c07b"

STYLESHEET = f"""
QMainWindow {{
    background-color: {BG_DARK};
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
}}
QGroupBox {{
    background-color: {BG_GROUP};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 10px;
    padding: 12px 6px 8px 6px;
    font-weight: 600;
    font-size: 13px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    color: {ACCENT};
}}
QLabel {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 3px 6px;
    min-height: 22px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 18px;
    border: none;
    background: {BG_INPUT};
}}
QCheckBox {{
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background-color: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QPushButton {{
    background-color: {ACCENT};
    color: #ffffff;
    font-weight: 600;
    border: none;
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 24px;
}}
QPushButton:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton:pressed {{
    background-color: {ACCENT_PRESS};
}}
QPushButton:disabled {{
    background-color: {BORDER};
    color: {TEXT_SECONDARY};
}}
QPushButton#stopBtn {{
    background-color: {DANGER};
}}
QPushButton#stopBtn:hover {{
    background-color: #e05560;
}}
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    text-align: center;
    min-height: 16px;
    max-height: 18px;
    color: {TEXT_PRIMARY};
    font-size: 10px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT}, stop:1 {ACCENT_HOVER});
    border-radius: 4px;
}}
QMenuBar {{
    background-color: {BG_PANEL};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item:selected {{
    background-color: {BG_GROUP};
}}
QMenu {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
}}
QMenu::item:selected {{
    background-color: {ACCENT};
}}
QStatusBar {{
    background-color: {BG_PANEL};
    border-top: 1px solid {BORDER};
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}
QSplitter::handle {{
    background-color: {BORDER};
    width: 2px;
}}
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QScrollBar:vertical {{
    background: {BG_PANEL};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""


# ═══════════════════════════════════════════════════════════════════════════
# SINGLE-PIXEL ACQUISITION WORKER  (runs in QThread)
# ═══════════════════════════════════════════════════════════════════════════

class SinglePixelWorker(QThread):
    """Acquire a FLIM histogram at a fixed galvo position.

    Emits periodic updates so the GUI can plot intermediate results.
    """
    update = pyqtSignal(object, object, dict)   # histogram, bin_edges, stats
    finished = pyqtSignal(object, object, dict)  # final histogram, bin_edges, stats
    error = pyqtSignal(str)

    def __init__(self, tagger, lj_handle, volt_x, volt_y,
                 dwell_s, continuous, laser_ch, detector_ch,
                 laser_freq, n_bins, binwidth,
                 tdac_x, tdac_y):
        super().__init__()
        self.tagger = tagger
        self.lj_handle = lj_handle
        self.volt_x = volt_x
        self.volt_y = volt_y
        self.dwell_s = dwell_s
        self.continuous = continuous
        self.laser_ch = laser_ch
        self.detector_ch = detector_ch
        self.laser_freq = laser_freq
        self.n_bins = n_bins
        self.binwidth = binwidth
        self.tdac_x = tdac_x
        self.tdac_y = tdac_y
        self._stop_requested = False

    def stop_acquisition(self):
        self._stop_requested = True

    def run(self):
        try:
            from labjack import ljm
            from flim_engine import PulsedLaserFLIM

            # Move galvo
            ljm.eWriteName(self.lj_handle, self.tdac_x, float(self.volt_x))
            ljm.eWriteName(self.lj_handle, self.tdac_y, float(self.volt_y))
            time.sleep(0.05)

            flim = PulsedLaserFLIM(
                self.tagger,
                click_channel=self.detector_ch,
                laser_channel=self.laser_ch,
                laser_frequency=self.laser_freq,
                n_hist_bins=self.n_bins,
                begin_channel=None,
                end_channel=None,
                binwidth=self.binwidth,
            )

            flim.start()
            t0 = time.time()

            while True:
                time.sleep(0.5)
                histogram = flim.get_histogram()
                bin_edges = flim.get_bin_edges()
                stats = flim.get_statistics()
                self.update.emit(histogram.copy(), bin_edges.copy(), stats)

                elapsed = time.time() - t0
                if self._stop_requested:
                    break
                if not self.continuous and elapsed >= self.dwell_s:
                    break

            flim.stop()
            histogram = flim.get_histogram()
            bin_edges = flim.get_bin_edges()
            stats = flim.get_statistics()
            self.finished.emit(histogram.copy(), bin_edges.copy(), stats)

        except Exception as e:
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════
# MATPLOTLIB CANVASES
# ═══════════════════════════════════════════════════════════════════════════

class MapCanvas(FigureCanvas):
    """Clickable 2-D image (lifetime or intensity map)."""

    pixel_clicked = pyqtSignal(int, int, float, float)  # px, py, vx, vy

    def __init__(self, parent=None):
        self.fig = Figure(facecolor=BG_DARK, edgecolor=BG_DARK)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self._style_ax()
        self.im = None
        self.cbar = None
        self._crosshair_h = None
        self._crosshair_v = None
        self._x_axis = None
        self._y_axis = None
        self.fig.tight_layout(pad=1.5)
        self.mpl_connect("button_press_event", self._on_click)

    def _style_ax(self):
        self.ax.set_facecolor(BG_PANEL)
        self.ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
        for spine in self.ax.spines.values():
            spine.set_color(BORDER)
        self.ax.set_xlabel("X (μm)", color=TEXT_SECONDARY, fontsize=10)
        self.ax.set_ylabel("Y (μm)", color=TEXT_SECONDARY, fontsize=10)
        self.ax.set_title("Lifetime Map", color=TEXT_PRIMARY, fontsize=12,
                          fontweight="bold")
        self.ax.set_anchor('C')

    def update_image(self, data_2d, cmap="viridis", x_axis=None, y_axis=None,
                     label="Lifetime (ps)", title="Lifetime Map"):
        self._x_axis = x_axis
        self._y_axis = y_axis
        extent = None
        if x_axis is not None and y_axis is not None:
            extent = [x_axis[0], x_axis[-1], y_axis[0], y_axis[-1]]

        if self.im is None:
            self.im = self.ax.imshow(
                data_2d, cmap=cmap, origin="lower", aspect="equal",
                interpolation="nearest", extent=extent,
            )
            self.cbar = self.fig.colorbar(self.im, ax=self.ax, fraction=0.046, pad=0.04)
            self.cbar.ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
            self.cbar.set_label(label, color=TEXT_SECONDARY, fontsize=10)
            self.cbar.outline.set_edgecolor(BORDER)
        else:
            self.im.set_data(data_2d)
            self.im.set_cmap(cmap)
            vmin = np.nanmin(data_2d) if np.any(np.isfinite(data_2d)) else 0
            vmax = np.nanmax(data_2d) if np.any(np.isfinite(data_2d)) else 1
            self.im.set_clim(vmin, vmax)
            if extent:
                self.im.set_extent(extent)
            # Update colorbar label when redrawing
            self.cbar.set_label(label, color=TEXT_SECONDARY, fontsize=10)
        self.ax.set_title(title, color=TEXT_PRIMARY, fontsize=12, fontweight="bold")
        # Re-run layout now that image + colorbar are present, then center
        self.fig.tight_layout(pad=1.5)
        self.ax.set_anchor('C')
        self.draw_idle()

    def clear_image(self):
        if self.cbar is not None:
            self.cbar.remove()
        self.ax.cla()
        self._style_ax()
        self.im = None
        self.cbar = None
        self._crosshair_h = None
        self._crosshair_v = None
        self.draw_idle()

    def clear_crosshair(self):
        """Remove crosshair lines without clearing the image."""
        if self._crosshair_h:
            self._crosshair_h.remove()
            self._crosshair_h = None
        if self._crosshair_v:
            self._crosshair_v.remove()
            self._crosshair_v = None
        self.draw_idle()

    def draw_crosshair(self, vx, vy):
        """Draw crosshair at voltage coordinates."""
        if self._crosshair_h:
            self._crosshair_h.remove()
        if self._crosshair_v:
            self._crosshair_v.remove()
        self._crosshair_h = self.ax.axhline(vy, color='cyan', lw=0.8, ls='--')
        self._crosshair_v = self.ax.axvline(vx, color='cyan', lw=0.8, ls='--')
        self.draw_idle()

    def _on_click(self, event):
        if event.inaxes != self.ax or self._x_axis is None:
            return
        vx, vy = event.xdata, event.ydata
        px = int(np.argmin(np.abs(self._x_axis - vx)))
        py = int(np.argmin(np.abs(self._y_axis - vy)))
        snap_vx = float(self._x_axis[px])
        snap_vy = float(self._y_axis[py])

        # Draw crosshair
        if self._crosshair_h:
            self._crosshair_h.remove()
        if self._crosshair_v:
            self._crosshair_v.remove()
        self._crosshair_h = self.ax.axhline(snap_vy, color='cyan', lw=0.8, ls='--')
        self._crosshair_v = self.ax.axvline(snap_vx, color='cyan', lw=0.8, ls='--')
        self.draw_idle()

        self.pixel_clicked.emit(px, py, snap_vx, snap_vy)


class HistogramCanvas(FigureCanvas):
    """Single-pixel decay histogram with fit overlay."""

    def __init__(self, parent=None):
        self.fig = Figure(facecolor=BG_DARK, edgecolor=BG_DARK)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self._style_ax()
        self.fig.tight_layout(pad=1.5)

    def _style_ax(self, use_nanoseconds=False):
        self.ax.set_facecolor(BG_PANEL)
        self.ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
        for spine in self.ax.spines.values():
            spine.set_color(BORDER)
        unit = "ns" if use_nanoseconds else "ps"
        self.ax.set_xlabel(f"Photon Arrival Time ({unit})", color=TEXT_SECONDARY, fontsize=10)
        self.ax.set_ylabel("Counts", color=TEXT_SECONDARY, fontsize=10)
        self.ax.set_title("Single-Pixel Histogram", color=TEXT_PRIMARY, fontsize=12,
                          fontweight="bold")

    def plot_histogram(self, histogram, bin_edges, fit_result=None,
                       title="Single-Pixel Histogram", use_nanoseconds=False):
        self.ax.cla()
        self._style_ax(use_nanoseconds=use_nanoseconds)
        if fit_result and fit_result.get('fit_success'):
            plot_flim_with_fit(histogram, bin_edges, fit_result, ax=self.ax,
                               title=title, use_nanoseconds=use_nanoseconds)
        else:
            bc = (bin_edges[:-1] + bin_edges[1:]) / 2
            bw = np.diff(bin_edges)
            if use_nanoseconds:
                bc = bc / 1000.0
                bw = bw / 1000.0
            self.ax.bar(bc, histogram, width=bw, alpha=0.6,
                        edgecolor='black', color='steelblue', label='Data')
            self.ax.set_yscale('log')
            self.ax.set_title(title, color=TEXT_PRIMARY, fontsize=12,
                              fontweight="bold")
            self.ax.grid(True, alpha=0.3)
        # Restyle after plot_flim_with_fit overwrites colours
        self.ax.set_facecolor(BG_PANEL)
        self.ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
        for spine in self.ax.spines.values():
            spine.set_color(BORDER)
        self.ax.title.set_color(TEXT_PRIMARY)
        self.ax.xaxis.label.set_color(TEXT_SECONDARY)
        self.ax.yaxis.label.set_color(TEXT_SECONDARY)
        self.draw_idle()

    def clear_plot(self):
        self.ax.cla()
        self._style_ax()
        self.draw_idle()


# ═══════════════════════════════════════════════════════════════════════════
# MAIN FLIM WINDOW
# ═══════════════════════════════════════════════════════════════════════════

class FLIMWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FLIM Scanner")
        self.setMinimumSize(1300, 800)
        self.resize(1500, 900)

        self._scan_worker = None
        self._pixel_worker = None
        self.tagger = None
        self.lj_handle = None

        # Scan data cache
        self._lifetime_map = None
        self._intensity_map = None
        self._frame_3d = None
        self._bin_edges = None
        self._x_axis = None
        self._y_axis = None

        # Display preferences
        self._use_nanoseconds = True
        self._lifetime_filter_min_ps = 0.0
        self._lifetime_filter_max_ps = 10000.0

        self._build_menus()
        self._build_ui()

        self.statusBar().showMessage("Initializing hardware...")
        self._init_hardware()

    # ── Hardware ───────────────────────────────────────────────────────────

    def _init_hardware(self):
        # TimeTagger
        try:
            import TimeTagger
            self.tagger = TimeTagger.createTimeTagger()
            self.statusBar().showMessage(
                f"TimeTagger: {self.tagger.getModel()} ({self.tagger.getSerial()})")
        except Exception as e:
            self.statusBar().showMessage(f"TimeTagger error: {e}")
            QMessageBox.warning(self, "TimeTagger", f"Could not connect:\n{e}")
            return

        # LabJack is NOT opened here — scan_engine opens its own handle,
        # and single-pixel mode opens/closes on demand to avoid conflicts.
        self.statusBar().showMessage(
            f"TimeTagger: {self.tagger.getModel()} ({self.tagger.getSerial()})  |  Ready")

    def closeEvent(self, event):
        # Stop any running workers
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.abort()
            self._scan_worker.wait()
        if self._pixel_worker and self._pixel_worker.isRunning():
            self._pixel_worker.stop_acquisition()
            self._pixel_worker.wait()

        if self.tagger:
            try:
                import TimeTagger
                TimeTagger.freeTimeTagger(self.tagger)
            except Exception:
                pass
        super().closeEvent(event)

    # ── Menus ──────────────────────────────────────────────────────────────

    def _build_menus(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")

        open_act = QAction("Open NPZ…", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._open_npz)
        file_menu.addAction(open_act)

        save_act = QAction("Save Image as PNG…", self)
        save_act.setShortcut("Ctrl+S")
        save_act.triggered.connect(self._save_image_png)
        file_menu.addAction(save_act)

        file_menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

    # ── Build UI ───────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ────────────── LEFT PANEL (parameters) ──────────────
        left_frame = QFrame()
        left_frame.setStyleSheet(f"background-color: {BG_PANEL};")
        left_frame.setFixedWidth(340)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_widget = QWidget()
        left_layout = QVBoxLayout(scroll_widget)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(4)

        title_lbl = QLabel("⚛  FLIM Scanner")
        title_lbl.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY}; "
            f"padding: 6px 0 2px 0;"
        )
        left_layout.addWidget(title_lbl)

        # --- Scan Grid ---
        grp_grid = QGroupBox("Scan Grid")
        fl = QFormLayout(grp_grid)
        fl.setLabelAlignment(Qt.AlignRight)
        self.sp_nx = self._spin(1, 10000, 50)
        self.sp_ny = self._spin(1, 10000, 50)
        self.chk_bidir = QCheckBox("Bidirectional")
        self.chk_bidir.setChecked(True)
        fl.addRow("NX:", self.sp_nx)
        fl.addRow("NY:", self.sp_ny)
        fl.addRow(self.chk_bidir)
        left_layout.addWidget(grp_grid)

        # --- Timing ---
        grp_time = QGroupBox("Timing")
        fl = QFormLayout(grp_time)
        fl.setLabelAlignment(Qt.AlignRight)
        self.sp_dwell = self._dspin(0.1, 100000, 20.0, 1, " ms")
        self.sp_settle = self._dspin(0.0, 10000, 1.0, 1, " ms")
        fl.addRow("Dwell:", self.sp_dwell)
        fl.addRow("Settle:", self.sp_settle)
        left_layout.addWidget(grp_time)

        # --- Voltage Range ---
        grp_volt = QGroupBox("Voltage Range")
        fl = QFormLayout(grp_volt)
        fl.setLabelAlignment(Qt.AlignRight)
        self.sp_xmin = self._dspin(-10, 10, -0.8, 4, " V")
        self.sp_xmax = self._dspin(-10, 10,  0.222, 4, " V")
        self.sp_ymin = self._dspin(-10, 10,  0.26, 4, " V")
        self.sp_ymax = self._dspin(-10, 10,  0.9, 4, " V")
        fl.addRow("X min:", self.sp_xmin)
        fl.addRow("X max:", self.sp_xmax)
        fl.addRow("Y min:", self.sp_ymin)
        fl.addRow("Y max:", self.sp_ymax)
        left_layout.addWidget(grp_volt)

        # --- FLIM Hardware ---
        grp_flim = QGroupBox("FLIM Hardware")
        fl = QFormLayout(grp_flim)
        fl.setLabelAlignment(Qt.AlignRight)
        self.sp_laser_ch = self._spin(1, 18, 1)
        self.sp_detector_ch = self._spin(1, 18, 2)
        self.sp_laser_freq = self._dspin(0.001, 1000, 5.0, 3, " MHz")
        self.sp_n_bins = self._spin(16, 4096, 256)
        self.sp_binwidth = self._spin(1, 100000, 200)
        self.sp_hw_delay = self._dspin(0, 100000, 22400, 0, " ps")
        self.sp_fit_end = self._dspin(0, 200000, 45000, 0, " ps")
        fl.addRow("Laser Ch:", self.sp_laser_ch)
        fl.addRow("Detector Ch:", self.sp_detector_ch)
        fl.addRow("Laser Freq:", self.sp_laser_freq)
        fl.addRow("Bins:", self.sp_n_bins)
        fl.addRow("Bin Width:", self.sp_binwidth)
        self.sp_calibration = self._dspin(1, 10000, 292.56, 2, " μm/V")
        self.sp_calibration.valueChanged.connect(self._on_calibration_changed)
        fl.addRow("HW Delay:", self.sp_hw_delay)
        fl.addRow("Fit End:", self.sp_fit_end)
        fl.addRow("Calibration:", self.sp_calibration)

        # Display unit preference
        self.chk_use_ns = QCheckBox("Nanoseconds")
        self.chk_use_ns.setChecked(True)
        self.chk_use_ns.toggled.connect(self._on_unit_changed)
        fl.addRow("Unit:", self.chk_use_ns)

        # Lifetime filter range
        self.sp_lifetime_min = self._dspin(0, 100000, 0, 0, " ns")
        self.sp_lifetime_min.valueChanged.connect(self._on_filter_changed)
        fl.addRow("τ Min:", self.sp_lifetime_min)

        self.sp_lifetime_max = self._dspin(0, 100000, 10, 0, " ns")
        self.sp_lifetime_max.valueChanged.connect(self._on_filter_changed)
        fl.addRow("τ Max:", self.sp_lifetime_max)
        left_layout.addWidget(grp_flim)

        # --- LabJack ---
        grp_lj = QGroupBox("LabJack")
        fl = QFormLayout(grp_lj)
        fl.setLabelAlignment(Qt.AlignRight)
        self.le_tdac_x = QLineEdit("TDAC2")
        self.le_tdac_y = QLineEdit("TDAC3")
        self.le_marker_dio = QLineEdit("FIO1")
        self.sp_marker_ch = self._spin(1, 18, 4)
        self.sp_marker_trig = self._dspin(-5, 5, 0.2, 3, " V")
        self.sp_click_trig = self._dspin(-5, 5, 0.125, 4, " V")
        fl.addRow("TDAC X:", self.le_tdac_x)
        fl.addRow("TDAC Y:", self.le_tdac_y)
        fl.addRow("Marker DIO:", self.le_marker_dio)
        fl.addRow("Marker Ch:", self.sp_marker_ch)
        fl.addRow("Marker Trig:", self.sp_marker_trig)
        fl.addRow("Click Trig:", self.sp_click_trig)
        left_layout.addWidget(grp_lj)

        # --- Area Scan Actions ---
        grp_scan = QGroupBox("Area Scan")
        scan_layout = QVBoxLayout(grp_scan)
        scan_layout.setSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self.btn_start_scan = QPushButton("▶  Start Scan")
        self.btn_start_scan.setCursor(Qt.PointingHandCursor)
        self.btn_start_scan.clicked.connect(self._start_area_scan)

        self.btn_stop_scan = QPushButton("■  Stop Scan")
        self.btn_stop_scan.setObjectName("stopBtn")
        self.btn_stop_scan.setCursor(Qt.PointingHandCursor)
        self.btn_stop_scan.setEnabled(False)
        self.btn_stop_scan.clicked.connect(self._abort_area_scan)

        btn_row.addWidget(self.btn_start_scan)
        btn_row.addWidget(self.btn_stop_scan)
        scan_layout.addLayout(btn_row)

        self.btn_load_scan = QPushButton("📁  Load Scan")
        self.btn_load_scan.setCursor(Qt.PointingHandCursor)
        self.btn_load_scan.clicked.connect(self._open_npz)
        scan_layout.addWidget(self.btn_load_scan)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat("%v/%m px (%p%)")
        scan_layout.addWidget(self.progress)

        self.lbl_scan_status = QLabel("Idle")
        self.lbl_scan_status.setWordWrap(True)
        self.lbl_scan_status.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 2px;")
        scan_layout.addWidget(self.lbl_scan_status)
        left_layout.addWidget(grp_scan)

        # --- Single Pixel Actions ---
        grp_pixel = QGroupBox("Single-Pixel Scan")
        pixel_layout = QVBoxLayout(grp_pixel)

        info = QLabel("Click on the lifetime map to select a pixel,\n"
                      "or use Load NPZ to view saved histograms.")
        info.setWordWrap(True)
        pixel_layout.addWidget(info)

        fl_px = QFormLayout()
        fl_px.setLabelAlignment(Qt.AlignRight)
        self.sp_pixel_dwell = self._dspin(0.1, 600000, 5000, 0, " ms")
        self.chk_continuous_pixel = QCheckBox("Continuous (until stopped)")
        fl_px.addRow("Dwell:", self.sp_pixel_dwell)
        pixel_layout.addLayout(fl_px)
        pixel_layout.addWidget(self.chk_continuous_pixel)

        self.btn_start_pixel = QPushButton("▶  Acquire Pixel")
        self.btn_start_pixel.setCursor(Qt.PointingHandCursor)
        self.btn_start_pixel.setEnabled(False)
        self.btn_start_pixel.clicked.connect(self._start_pixel_scan)
        pixel_layout.addWidget(self.btn_start_pixel)

        self.btn_stop_pixel = QPushButton("■  STOP  Acquisition")
        self.btn_stop_pixel.setObjectName("stopBtn")
        self.btn_stop_pixel.setCursor(Qt.PointingHandCursor)
        self.btn_stop_pixel.setVisible(False)
        self.btn_stop_pixel.clicked.connect(self._stop_pixel_scan)
        pixel_layout.addWidget(self.btn_stop_pixel)

        self.lbl_pixel_info = QLabel("No pixel selected")
        self.lbl_pixel_info.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 2px;")
        self.lbl_pixel_info.setWordWrap(True)
        pixel_layout.addWidget(self.lbl_pixel_info)

        left_layout.addWidget(grp_pixel)
        left_layout.addStretch()

        scroll.setWidget(scroll_widget)
        left_container = QVBoxLayout(left_frame)
        left_container.setContentsMargins(0, 0, 0, 0)
        left_container.addWidget(scroll)

        # ────────────── RIGHT PANEL (plots) ──────────────
        right_frame = QFrame()
        right_frame.setStyleSheet(f"background-color: {BG_DARK};")
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(8, 6, 8, 6)

        # Toolbar row – colormap selector applies to lifetime map only
        toolbar_row = QHBoxLayout()
        lbl_cm = QLabel("Lifetime Colormap:")
        lbl_cm.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
        self.cmb_cmap = QComboBox()
        for cm in ["viridis", "inferno", "hot", "magma", "plasma", "gray", "cividis", "turbo"]:
            self.cmb_cmap.addItem(cm)
        self.cmb_cmap.currentTextChanged.connect(self._on_cmap_changed)

        toolbar_row.addWidget(lbl_cm)
        toolbar_row.addWidget(self.cmb_cmap)
        toolbar_row.addStretch()
        right_layout.addLayout(toolbar_row)

        # Vertical splitter: top = maps, bottom = histogram
        vsplitter = QSplitter(Qt.Vertical)

        # Horizontal splitter for the two maps side-by-side
        map_splitter = QSplitter(Qt.Horizontal)

        # Intensity canvas (LEFT, always "hot")
        self.intensity_canvas = MapCanvas()
        self.intensity_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.intensity_canvas.pixel_clicked.connect(self._on_pixel_clicked)
        self.intensity_canvas.ax.set_title("Intensity Map", color=TEXT_PRIMARY,
                                           fontsize=12, fontweight="bold")
        map_splitter.addWidget(self.intensity_canvas)

        # Lifetime canvas (RIGHT, user-adjustable colormap)
        self.lifetime_canvas = MapCanvas()
        self.lifetime_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.lifetime_canvas.pixel_clicked.connect(self._on_pixel_clicked)
        self.lifetime_canvas.ax.set_title("Lifetime Map", color=TEXT_PRIMARY,
                                          fontsize=12, fontweight="bold")
        map_splitter.addWidget(self.lifetime_canvas)

        map_splitter.setStretchFactor(0, 1)
        map_splitter.setStretchFactor(1, 1)
        map_splitter.setCollapsible(0, False)
        map_splitter.setCollapsible(1, False)
        vsplitter.addWidget(map_splitter)

        # Histogram canvas
        self.hist_canvas = HistogramCanvas()
        self.hist_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vsplitter.addWidget(self.hist_canvas)

        vsplitter.setStretchFactor(0, 3)
        vsplitter.setStretchFactor(1, 2)
        vsplitter.setCollapsible(0, False)
        vsplitter.setCollapsible(1, False)
        right_layout.addWidget(vsplitter, stretch=1)

        # Action bar below graphs
        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(4, 4, 4, 4)
        action_bar.setSpacing(6)

        action_btn_css = f"""
            QPushButton {{
                background-color: {BG_GROUP};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 13px;
                min-height: 28px;
            }}
            QPushButton:hover {{
                border-color: {ACCENT};
                background-color: {BG_INPUT};
            }}
            QPushButton:disabled {{
                color: {TEXT_SECONDARY};
                border-color: {BORDER};
            }}
        """

        # Pixel coordinate inputs
        lbl_px = QLabel("Pixel:")
        lbl_px.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600; font-size: 12px;")
        self.sp_goto_x = QSpinBox()
        self.sp_goto_x.setRange(0, 9999)
        self.sp_goto_x.setPrefix("X: ")
        self.sp_goto_x.setFixedWidth(70)
        self.sp_goto_y = QSpinBox()
        self.sp_goto_y.setRange(0, 9999)
        self.sp_goto_y.setPrefix("Y: ")
        self.sp_goto_y.setFixedWidth(70)

        self.btn_move_galvo = QPushButton("\u25CE  Move")
        self.btn_move_galvo.setCursor(Qt.PointingHandCursor)
        self.btn_move_galvo.setEnabled(False)
        self.btn_move_galvo.clicked.connect(self._move_galvo_to_pixel)
        self.btn_move_galvo.setStyleSheet(action_btn_css)

        self.btn_clear_crosshair = QPushButton("\u2715  Clear")
        self.btn_clear_crosshair.setCursor(Qt.PointingHandCursor)
        self.btn_clear_crosshair.clicked.connect(self._clear_selection)
        self.btn_clear_crosshair.setStyleSheet(action_btn_css)

        self.btn_export_data = QPushButton("\U0001F4CA  Export Data")
        self.btn_export_data.setCursor(Qt.PointingHandCursor)
        self.btn_export_data.setEnabled(False)
        self.btn_export_data.clicked.connect(self._export_histogram_data)
        self.btn_export_data.setStyleSheet(action_btn_css)

        self.btn_export_graphs = QPushButton("\U0001F4BE  Export Maps")
        self.btn_export_graphs.setCursor(Qt.PointingHandCursor)
        self.btn_export_graphs.setEnabled(False)
        self.btn_export_graphs.clicked.connect(self._export_graphs)
        self.btn_export_graphs.setStyleSheet(action_btn_css)

        action_bar.addStretch()
        action_bar.addWidget(lbl_px)
        action_bar.addWidget(self.sp_goto_x)
        action_bar.addWidget(self.sp_goto_y)
        action_bar.addWidget(self.btn_move_galvo)
        action_bar.addWidget(self.btn_clear_crosshair)
        action_bar.addWidget(self.btn_export_data)
        action_bar.addWidget(self.btn_export_graphs)
        action_bar.addStretch()
        right_layout.addLayout(action_bar)

        # ────────────── Assemble ──────────────
        main_layout.addWidget(left_frame)
        main_layout.addWidget(right_frame, stretch=1)

        # ── Internal state ──
        self._selected_px = None
        self._selected_py = None
        self._selected_vx = None
        self._selected_vy = None

    # ── Widget helpers ─────────────────────────────────────────────────────

    def _spin(self, lo, hi, default):
        sb = QSpinBox()
        sb.setRange(lo, hi)
        sb.setValue(default)
        return sb

    def _dspin(self, lo, hi, default, decimals=3, suffix=""):
        dsb = QDoubleSpinBox()
        dsb.setRange(lo, hi)
        dsb.setDecimals(decimals)
        dsb.setValue(default)
        if suffix:
            dsb.setSuffix(suffix)
        return dsb

    # ═══════════════════════════════════════════════════════════════════════
    # AREA SCAN
    # ═══════════════════════════════════════════════════════════════════════

    def _build_scan_config(self) -> ScanConfig:
        return ScanConfig(
            nx=self.sp_nx.value(),
            ny=self.sp_ny.value(),
            dwell_s=self.sp_dwell.value() / 1000.0,
            settle_s=self.sp_settle.value() / 1000.0,
            x_vmin=self.sp_xmin.value(),
            x_vmax=self.sp_xmax.value(),
            y_vmin=self.sp_ymin.value(),
            y_vmax=self.sp_ymax.value(),
            bidirectional=self.chk_bidir.isChecked(),
            tdac_x=self.le_tdac_x.text().strip(),
            tdac_y=self.le_tdac_y.text().strip(),
            marker_dio=self.le_marker_dio.text().strip(),
            marker_pulse_s=0.0001,
            click_channels=[self.sp_detector_ch.value()],
            marker_channel=self.sp_marker_ch.value(),
            marker_trigger_level=self.sp_marker_trig.value(),
            click_trigger_level=self.sp_click_trig.value(),
            save_dir="./flim_data",
            continuous=False,
            # FLIM
            flim_enabled=True,
            flim_use_native=True,
            flim_laser_channel=self.sp_laser_ch.value(),
            flim_detector_channel=self.sp_detector_ch.value(),
            flim_laser_frequency=self.sp_laser_freq.value() * 1e6,
            flim_n_bins=self.sp_n_bins.value(),
            flim_binwidth=self.sp_binwidth.value(),
            flim_use_conditional_filter=True,
            flim_hardware_delay_ps=self.sp_hw_delay.value(),
            flim_fit_end_ps=self.sp_fit_end.value(),
        )

    def _start_area_scan(self):
        if not self.tagger:
            QMessageBox.critical(self, "Error",
                                 "TimeTagger not connected.")
            return
        cfg = self._build_scan_config()
        n_total = cfg.nx * cfg.ny

        self.progress.setMaximum(n_total)
        self.progress.setValue(0)
        self.lbl_scan_status.setText("Scanning…")
        self.lbl_scan_status.setStyleSheet(
            f"color: {WARNING}; font-size: 11px; padding: 2px;")
        self.btn_start_scan.setEnabled(False)
        self.btn_stop_scan.setEnabled(True)
        self.intensity_canvas.clear_image()
        self.lifetime_canvas.clear_image()

        self._scan_worker = ScanWorker(cfg, self.tagger)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.row_done.connect(self._on_row_done)
        self._scan_worker.finished_scan.connect(self._on_scan_finished)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _abort_area_scan(self):
        if self._scan_worker:
            self._scan_worker.abort()
            self.lbl_scan_status.setText("Stopping…")
            self.btn_stop_scan.setEnabled(False)

    def _on_scan_progress(self, current, total):
        self.progress.setValue(current)

    def _on_row_done(self, row_idx, img_array):
        # During scan show intensity on the intensity canvas (always "hot")
        ch = 0
        if ch < img_array.shape[0]:
            self.intensity_canvas.update_image(
                img_array[ch],
                cmap="hot",
                title="Scanning… (Intensity)",
                label="Counts",
            )
        self.lbl_scan_status.setText(f"Row {row_idx + 1} complete")

    def _on_scan_finished(self, result: dict):
        self._x_axis = result.get("x_axis")
        self._y_axis = result.get("y_axis")
        if self._x_axis is not None:
            self.sp_goto_x.setRange(0, len(self._x_axis) - 1)
        if self._y_axis is not None:
            self.sp_goto_y.setRange(0, len(self._y_axis) - 1)

        if "flim_lifetime_map" in result:
            self._lifetime_map = result["flim_lifetime_map"]
            self._intensity_map = result["flim_intensity_map"]
            # Load frame_3d and bin_edges from saved NPZ (not in result dict)
            npz_path = result.get("npz_path")
            if npz_path:
                try:
                    d = np.load(npz_path)
                    if "flim_frame_3d" in d:
                        self._frame_3d = d["flim_frame_3d"]
                    if "flim_bin_edges_ps" in d:
                        self._bin_edges = d["flim_bin_edges_ps"]
                except Exception:
                    pass

            stats = result.get("flim_stats", {})
            self._show_map()

            self.lbl_scan_status.setText(
                f"✓ Done — {stats.get('total_photons', '?')} photons\n"
                f"{os.path.basename(result['npz_path'])}")
            self.lbl_scan_status.setStyleSheet(
                f"color: {SUCCESS}; font-size: 11px; padding: 2px;")
        else:
            self.lbl_scan_status.setText(
                f"✓ Done (no FLIM)\n{os.path.basename(result['npz_path'])}")
            self.lbl_scan_status.setStyleSheet(
                f"color: {WARNING}; font-size: 11px; padding: 2px;")

        self.btn_start_scan.setEnabled(True)
        self.btn_stop_scan.setEnabled(False)

        self.statusBar().showMessage(
            f"Saved: {result['npz_path']}  |  {result['meta_path']}")

    def _on_scan_error(self, msg):
        self.lbl_scan_status.setText(f"✗ Error: {msg}")
        self.lbl_scan_status.setStyleSheet(
            f"color: {DANGER}; font-size: 11px; padding: 2px;")
        self.btn_start_scan.setEnabled(True)
        self.btn_stop_scan.setEnabled(False)
        QMessageBox.critical(self, "Scan Error", msg)

    # ═══════════════════════════════════════════════════════════════════════
    # MAP DISPLAY
    # ═══════════════════════════════════════════════════════════════════════

    def _calib_um_per_v(self):
        """Conversion factor: micrometers per volt (calibration spinbox is in μm/V)."""
        return self.sp_calibration.value()

    def _axes_um(self):
        """Return (x_um, y_um) numpy arrays, or (None, None) if axes not loaded."""
        if self._x_axis is None or self._y_axis is None:
            return None, None
        c = self._calib_um_per_v()
        return self._x_axis * c, self._y_axis * c

    def _on_calibration_changed(self, _value):
        if self._lifetime_map is not None or self._intensity_map is not None:
            self._show_map()

    def _show_map(self):
        """Update both intensity and lifetime canvases."""
        cmap = self.cmb_cmap.currentText()
        x_um, y_um = self._axes_um()

        # Intensity canvas (always "hot")
        if self._intensity_map is not None:
            self.intensity_canvas.update_image(
                self._intensity_map, cmap="hot",
                x_axis=x_um, y_axis=y_um,
                label="Photon Count", title="Intensity Map")

        # Lifetime canvas (user-adjustable colormap + filter)
        if self._lifetime_map is not None:
            masked = self._lifetime_map.copy()
            masked = np.where(masked > 0, masked, np.nan)
            masked = np.where(masked >= self._lifetime_filter_min_ps, masked, np.nan)
            masked = np.where(masked <= self._lifetime_filter_max_ps, masked, np.nan)

            if self._use_nanoseconds:
                masked = masked / 1000.0
                label = "Lifetime (ns)"
            else:
                label = "Lifetime (ps)"

            self.lifetime_canvas.update_image(
                masked, cmap=cmap,
                x_axis=x_um, y_axis=y_um,
                label=label, title="Lifetime Map")

    def _on_cmap_changed(self, _cmap):
        if self._lifetime_map is not None:
            self._show_map()

    def _on_unit_changed(self, checked):
        """Toggle between nanosecond and picosecond display units."""
        # Before changing units, convert current spinbox values to ps
        cur_min = self.sp_lifetime_min.value()
        cur_max = self.sp_lifetime_max.value()
        min_ps = cur_min * (1000 if self._use_nanoseconds else 1)
        max_ps = cur_max * (1000 if self._use_nanoseconds else 1)

        # Update preference
        self._use_nanoseconds = checked
        self._lifetime_filter_min_ps = min_ps
        self._lifetime_filter_max_ps = max_ps

        # Update spinboxes to show same thresholds in new units
        for sp, val_ps in [(self.sp_lifetime_min, min_ps),
                           (self.sp_lifetime_max, max_ps)]:
            sp.blockSignals(True)
            if checked:  # switching to nanoseconds
                sp.setMaximum(100000)
                sp.setValue(int(val_ps / 1000))
                sp.setSuffix(" ns")
            else:  # switching to picoseconds
                sp.setMaximum(100000000)
                sp.setValue(int(val_ps))
                sp.setSuffix(" ps")
            sp.blockSignals(False)

        if self._lifetime_map is not None:
            self._show_map()

    def _on_filter_changed(self, _value):
        """Update lifetime filter range."""
        scale = 1000 if self._use_nanoseconds else 1
        self._lifetime_filter_min_ps = self.sp_lifetime_min.value() * scale
        self._lifetime_filter_max_ps = self.sp_lifetime_max.value() * scale
        if self._lifetime_map is not None:
            self._show_map()

    # ═══════════════════════════════════════════════════════════════════════
    # PIXEL SELECTION / CLEAR / GOTO
    # ═══════════════════════════════════════════════════════════════════════

    def _select_pixel(self, px, py):
        """Centralized pixel selection: update state, spinboxes, crosshair, histogram."""
        if self._x_axis is None or self._y_axis is None:
            return
        px = max(0, min(px, len(self._x_axis) - 1))
        py = max(0, min(py, len(self._y_axis) - 1))
        vx = float(self._x_axis[px])
        vy = float(self._y_axis[py])

        self._selected_px = px
        self._selected_py = py
        self._selected_vx = vx
        self._selected_vy = vy

        # Sync spinboxes
        self.sp_goto_x.blockSignals(True)
        self.sp_goto_y.blockSignals(True)
        self.sp_goto_x.setValue(px)
        self.sp_goto_y.setValue(py)
        self.sp_goto_x.blockSignals(False)
        self.sp_goto_y.blockSignals(False)

        self.btn_start_pixel.setEnabled(True)
        self.btn_move_galvo.setEnabled(True)
        self.btn_export_data.setEnabled(True)
        self.btn_export_graphs.setEnabled(True)

        # Draw crosshair on both canvases (in μm)
        calib = self._calib_um_per_v()
        self.intensity_canvas.draw_crosshair(vx * calib, vy * calib)
        self.lifetime_canvas.draw_crosshair(vx * calib, vy * calib)

        # Show stored histogram if available
        if self._frame_3d is not None and self._bin_edges is not None:
            histogram = self._frame_3d[py, px, :]
            bin_edges = self._bin_edges
            total = int(np.sum(histogram))

            hw_delay = self.sp_hw_delay.value()
            fit_end = self.sp_fit_end.value()
            analyzer = FLIMAnalyzer(histogram, bin_edges,
                                    fixed_peak_time_ps=hw_delay,
                                    fit_end_ps=fit_end if fit_end > 0 else None)
            fit_single = analyzer.fit_exponential()
            tau_ps = fit_single['tau_ps'] if fit_single['fit_success'] else 0
            tau_display = tau_ps / 1000.0 if self._use_nanoseconds else tau_ps
            tau_unit = "ns" if self._use_nanoseconds else "ps"
            tau_str = (f"τ = {tau_display:.3f} {tau_unit}  "
                       f"(R² = {fit_single['r_squared']:.4f})"
                       if fit_single['fit_success'] else "fit failed")
            lt_val = (self._lifetime_map[py, px]
                      if self._lifetime_map is not None else 0)
            lt_display = lt_val / 1000.0 if self._use_nanoseconds else lt_val
            lt_unit = "ns" if self._use_nanoseconds else "ps"
            calib = self._calib_um_per_v()
            self.lbl_pixel_info.setText(
                f"Pixel ({px}, {py})  ({vx*calib:.2f}, {vy*calib:.2f}) μm\n"
                f"Photons: {total}  |  MLE τ: {lt_display:.3f} {lt_unit}\n"
                f"Fit: {tau_str}")
            self.hist_canvas.plot_histogram(
                histogram, bin_edges, fit_single,
                title=f"Pixel ({px}, {py})",
                use_nanoseconds=self._use_nanoseconds)
        else:
            lt_val = (self._lifetime_map[py, px]
                      if self._lifetime_map is not None else 0)
            lt_display = lt_val / 1000.0 if self._use_nanoseconds else lt_val
            lt_unit = "ns" if self._use_nanoseconds else "ps"
            calib = self._calib_um_per_v()
            self.lbl_pixel_info.setText(
                f"Pixel ({px}, {py})  ({vx*calib:.2f}, {vy*calib:.2f}) μm\n"
                f"MLE τ: {lt_display:.3f} {lt_unit}  |  Click 'Acquire Pixel' to measure")
    # ═══════════════════════════════════════════════════════════════════════

    def _on_pixel_clicked(self, px, py, vx, vy):
        self._select_pixel(px, py)

    def _clear_selection(self):
        """Clear crosshair and pixel selection state."""
        self._selected_px = None
        self._selected_py = None
        self._selected_vx = None
        self._selected_vy = None
        self.intensity_canvas.clear_crosshair()
        self.lifetime_canvas.clear_crosshair()
        self.hist_canvas.ax.clear()
        self.hist_canvas.draw()
        self.btn_export_data.setEnabled(False)
        self.lbl_pixel_info.setText("Click on the map to select a pixel")

    # ═══════════════════════════════════════════════════════════════════════
    # SINGLE-PIXEL LIVE ACQUISITION
    # ═══════════════════════════════════════════════════════════════════════

    def _open_labjack(self):
        """Open LabJack on demand for single-pixel galvo control."""
        if self.lj_handle is not None:
            return True
        try:
            from labjack import ljm
            self.lj_handle = ljm.openS("T7", "ANY", "ANY")
            return True
        except Exception as e:
            QMessageBox.critical(self, "LabJack Error",
                                 f"Could not open LabJack:\n{e}")
            return False

    def _close_labjack(self):
        """Close LabJack after single-pixel acquisition."""
        if self.lj_handle is not None:
            try:
                from labjack import ljm
                ljm.close(self.lj_handle)
            except Exception:
                pass
            self.lj_handle = None

    def _move_galvo_to_pixel(self):
        """Move galvo to the pixel specified in the spinboxes."""
        if self._x_axis is None or self._y_axis is None:
            QMessageBox.warning(self, "No Scan Data",
                                "Load or run a scan first.")
            return

        px = self.sp_goto_x.value()
        py = self.sp_goto_y.value()
        self._select_pixel(px, py)

        if not self._open_labjack():
            return

        try:
            from labjack import ljm
            tdac_x = self.le_tdac_x.text().strip()
            tdac_y = self.le_tdac_y.text().strip()

            ljm.eWriteName(self.lj_handle, tdac_x, float(self._selected_vx))
            ljm.eWriteName(self.lj_handle, tdac_y, float(self._selected_vy))
            time.sleep(0.1)

            self.lbl_pixel_info.setText(
                f"✓ Galvo moved to pixel ({self._selected_px}, {self._selected_py})\n"
                f"Position: V=({self._selected_vx:.4f}, {self._selected_vy:.4f})")

        except Exception as e:
            QMessageBox.critical(self, "Galvo Move Error",
                                 f"Could not move galvo:\n{e}")
        finally:
            self._close_labjack()

    def _export_graphs(self):
        """Export the on-screen maps and histogram as publication-ready white-background PNGs."""
        save_dir = Path("./flim_graph")
        save_dir.mkdir(exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        exported = []

        def _export_canvas(canvas, path):
            """Temporarily swap to white theme, save, then restore dark."""
            orig_fc = canvas.fig.get_facecolor()
            orig_ax_fc = canvas.ax.get_facecolor()
            canvas.fig.set_facecolor('white')
            canvas.ax.set_facecolor('white')
            canvas.ax.title.set_color('black')
            canvas.ax.xaxis.label.set_color('black')
            canvas.ax.yaxis.label.set_color('black')
            canvas.ax.tick_params(colors='black', labelcolor='black')
            if getattr(canvas, 'cbar', None) is not None:
                canvas.cbar.ax.yaxis.set_tick_params(color='black', labelcolor='black')
                canvas.cbar.ax.yaxis.label.set_color('black')
                for lbl in canvas.cbar.ax.yaxis.get_ticklabels():
                    lbl.set_color('black')
            legend = canvas.ax.get_legend()
            if legend:
                for t in legend.get_texts():
                    t.set_color('black')
                legend.get_frame().set_facecolor('white')
                legend.get_frame().set_edgecolor('gray')

            canvas.fig.savefig(str(path), dpi=150, facecolor='white', bbox_inches="tight")

            # Restore dark theme
            canvas.fig.set_facecolor(orig_fc)
            canvas.ax.set_facecolor(orig_ax_fc)
            canvas.ax.title.set_color(TEXT_PRIMARY)
            canvas.ax.xaxis.label.set_color(TEXT_SECONDARY)
            canvas.ax.yaxis.label.set_color(TEXT_SECONDARY)
            canvas.ax.tick_params(colors=TEXT_SECONDARY, labelcolor=TEXT_SECONDARY)
            if getattr(canvas, 'cbar', None) is not None:
                canvas.cbar.ax.yaxis.set_tick_params(color=TEXT_SECONDARY, labelcolor=TEXT_SECONDARY)
                canvas.cbar.ax.yaxis.label.set_color(TEXT_SECONDARY)
                for lbl in canvas.cbar.ax.yaxis.get_ticklabels():
                    lbl.set_color(TEXT_SECONDARY)
            if legend:
                for t in legend.get_texts():
                    t.set_color(TEXT_PRIMARY)
                legend.get_frame().set_facecolor(BG_DARK)
                legend.get_frame().set_edgecolor(BORDER)
            canvas.draw_idle()

        # ── 1. Intensity map ──
        if self._intensity_map is not None and self.intensity_canvas.im is not None:
            p = save_dir / f"intensity_{timestamp}.png"
            _export_canvas(self.intensity_canvas, p)
            exported.append(p.name)

        # ── 2. Lifetime map ──
        if self._lifetime_map is not None and self.lifetime_canvas.im is not None:
            p = save_dir / f"lifetime_{timestamp}.png"
            _export_canvas(self.lifetime_canvas, p)
            exported.append(p.name)

        # ── 3. Histogram ──
        if self.hist_canvas.ax.has_data():
            p = save_dir / f"histogram_{timestamp}.png"
            _export_canvas(self.hist_canvas, p)
            exported.append(p.name)

        if exported:
            self.statusBar().showMessage(f"Saved to flim_graph/: {', '.join(exported)}")
        else:
            QMessageBox.warning(self, "Nothing to export",
                                "No scan data available to export.")

    def _export_histogram_data(self):
        """Export selected pixel histogram (and fit curve) as a CSV to flim_histogram_data/."""
        if self._selected_px is None or self._frame_3d is None or self._bin_edges is None:
            QMessageBox.warning(self, "No Data",
                                "Select a pixel with scan data loaded first.")
            return

        px, py = self._selected_px, self._selected_py
        histogram = self._frame_3d[py, px, :]
        bin_edges = self._bin_edges
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0

        # Run fit to get curve
        hw_delay = self.sp_hw_delay.value()
        fit_end = self.sp_fit_end.value()
        analyzer = FLIMAnalyzer(histogram, bin_edges,
                                fixed_peak_time_ps=hw_delay,
                                fit_end_ps=fit_end if fit_end > 0 else None)
        fit_result = analyzer.fit_exponential()

        # Build fit curve over all bins (NaN outside fit range)
        fit_counts = np.full(len(bin_centers), np.nan)
        if fit_result.get('fit_success'):
            A = fit_result['amplitude']
            tau = fit_result['tau_ps']
            B = fit_result['baseline']
            t0 = fit_result['peak_time_ps']
            fit_counts = A * np.exp(-(bin_centers - t0) / tau) + B
            fit_counts[bin_centers < t0] = np.nan

        save_dir = Path("./flim_histogram_data")
        save_dir.mkdir(exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        fname = save_dir / f"histogram_px{px}_{py}_{timestamp}.csv"

        with open(fname, 'w') as f:
            f.write(f"# FLIM Histogram Export\n")
            calib_c = self._calib_um_per_v()
            f.write(f"# Pixel: ({px}, {py})   Pos: ({self._selected_vx*calib_c:.2f}, {self._selected_vy*calib_c:.2f}) μm   Voltage: ({self._selected_vx:.4f}, {self._selected_vy:.4f}) V\n")
            f.write(f"# Hardware delay: {hw_delay} ps   Fit end: {fit_end} ps\n")
            if fit_result.get('fit_success'):
                tau_ns = fit_result['tau_ps'] / 1000.0
                f.write(f"# Fit: tau = {tau_ns:.3f} ns  A = {fit_result['amplitude']:.1f}  "
                        f"B = {fit_result['baseline']:.1f}  R2 = {fit_result['r_squared']:.4f}\n")
            else:
                f.write(f"# Fit: failed\n")
            f.write("bin_center_ps,bin_center_ns,counts,fit_counts\n")
            for bc, bc_ns, cnt, fc in zip(bin_centers,
                                          bin_centers / 1000.0,
                                          histogram,
                                          fit_counts):
                fc_str = f"{fc:.4f}" if np.isfinite(fc) else ""
                f.write(f"{bc:.2f},{bc_ns:.5f},{int(cnt)},{fc_str}\n")

        self.statusBar().showMessage(f"Saved to flim_histogram_data/: {fname.name}")

    def _start_pixel_scan(self):
        if self._selected_vx is None:
            QMessageBox.warning(self, "No pixel",
                                "Click on the map first.")
            return
        if not self.tagger:
            QMessageBox.critical(self, "Error",
                                 "TimeTagger not connected.")
            return
        if self._pixel_worker and self._pixel_worker.isRunning():
            return
        if not self._open_labjack():
            return

        dwell_s = self.sp_pixel_dwell.value() / 1000.0
        continuous = self.chk_continuous_pixel.isChecked()

        # Swap the map canvas to show "Single-pixel mode" indicator
        self._show_single_pixel_map()

        self._pixel_worker = SinglePixelWorker(
            tagger=self.tagger,
            lj_handle=self.lj_handle,
            volt_x=self._selected_vx,
            volt_y=self._selected_vy,
            dwell_s=dwell_s,
            continuous=continuous,
            laser_ch=self.sp_laser_ch.value(),
            detector_ch=self.sp_detector_ch.value(),
            laser_freq=self.sp_laser_freq.value() * 1e6,
            n_bins=self.sp_n_bins.value(),
            binwidth=self.sp_binwidth.value(),
            tdac_x=self.le_tdac_x.text().strip(),
            tdac_y=self.le_tdac_y.text().strip(),
        )
        self._pixel_worker.update.connect(self._on_pixel_update)
        self._pixel_worker.finished.connect(self._on_pixel_finished)
        self._pixel_worker.error.connect(self._on_pixel_error)
        self._pixel_worker.start()

        self.btn_start_pixel.setVisible(False)
        self.btn_stop_pixel.setEnabled(True)
        self.btn_stop_pixel.setVisible(True)
        self.btn_start_scan.setEnabled(False)
        self.lbl_pixel_info.setText(
            f"Acquiring at pixel ({self._selected_px}, {self._selected_py}) …")

    def _stop_pixel_scan(self):
        if self._pixel_worker:
            self._pixel_worker.stop_acquisition()
            self.btn_stop_pixel.setEnabled(False)
            # Wait for the worker thread to finish
            self._pixel_worker.wait()

    def _show_single_pixel_map(self):
        """During single-pixel acquisition, overlay the lifetime map
        with the selected pixel highlighted, replacing the lifetime canvas."""
        if self._lifetime_map is not None:
            masked = np.where(self._lifetime_map > 0,
                              self._lifetime_map, np.nan)
            cmap = self.cmb_cmap.currentText()
            x_um, y_um = self._axes_um()
            self.lifetime_canvas.update_image(
                masked, cmap=cmap,
                x_axis=x_um, y_axis=y_um,
                label="Lifetime (ps)",
                title=f"Single-Pixel: ({self._selected_px}, {self._selected_py})")

    def _on_pixel_update(self, histogram, bin_edges, stats):
        """Live update of histogram during acquisition."""
        hw_delay = self.sp_hw_delay.value()
        fit_end = self.sp_fit_end.value()
        analyzer = FLIMAnalyzer(histogram, bin_edges,
                                fixed_peak_time_ps=hw_delay,
                                fit_end_ps=fit_end if fit_end > 0 else None)
        fit_single = analyzer.fit_exponential()
        basic = analyzer.get_statistics()

        tau_str = (f"τ = {fit_single['tau_ps']:.0f} ps  "
                   f"(R² = {fit_single['r_squared']:.4f})"
                   if fit_single['fit_success'] else "fitting…")
        self.lbl_pixel_info.setText(
            f"Pixel ({self._selected_px}, {self._selected_py})  "
            f"V=({self._selected_vx:.4f}, {self._selected_vy:.4f})\n"
            f"Photons: {basic['total_photons']:,}  |  {tau_str}")

        self.hist_canvas.plot_histogram(
            histogram, bin_edges, fit_single,
            title=f"Pixel ({self._selected_px}, {self._selected_py}) — LIVE")

    def _on_pixel_finished(self, histogram, bin_edges, stats):
        hw_delay = self.sp_hw_delay.value()
        fit_end = self.sp_fit_end.value()
        analyzer = FLIMAnalyzer(histogram, bin_edges,
                                fixed_peak_time_ps=hw_delay,
                                fit_end_ps=fit_end if fit_end > 0 else None)
        fit_single = analyzer.fit_exponential()
        fit_double = analyzer.fit_biexponential()
        basic = analyzer.get_statistics()

        unit = "ns" if self._use_nanoseconds else "ps"
        scale = 1000.0 if self._use_nanoseconds else 1.0

        lines = [
            f"Pixel ({self._selected_px}, {self._selected_py})  "
            f"V=({self._selected_vx:.4f}, {self._selected_vy:.4f})",
            f"Photons: {basic['total_photons']:,}",
        ]
        if fit_single['fit_success']:
            tau_display = fit_single['tau_ps'] / scale
            lines.append(
                f"Single exp: τ = {tau_display:.3f} {unit}  "
                f"(R² = {fit_single['r_squared']:.4f})")
        if fit_double['fit_success']:
            tau1_display = fit_double['tau1_ps'] / scale
            tau2_display = fit_double['tau2_ps'] / scale
            tau_avg_display = fit_double.get('tau_avg_ps', 0) / scale
            lines.append(
                f"Bi-exp: τ₁={tau1_display:.3f}  "
                f"τ₂={tau2_display:.3f}  "
                f"τ_avg={tau_avg_display:.3f} {unit}  "
                f"(R²={fit_double['r_squared']:.4f})")
        self.lbl_pixel_info.setText("\n".join(lines))

        best_fit = fit_single if fit_single['fit_success'] else None
        self.hist_canvas.plot_histogram(
            histogram, bin_edges, best_fit,
            title=f"Pixel ({self._selected_px}, {self._selected_py})",
            use_nanoseconds=self._use_nanoseconds)

        self.btn_stop_pixel.setVisible(False)
        self.btn_start_pixel.setVisible(True)
        self.btn_start_pixel.setEnabled(True)
        self.btn_start_scan.setEnabled(True)
        
        # Properly clean up the worker thread
        if self._pixel_worker:
            self._pixel_worker.update.disconnect()
            self._pixel_worker.finished.disconnect()
            self._pixel_worker.error.disconnect()
            self._pixel_worker.wait()
            self._pixel_worker = None
        
        self._close_labjack()

    def _on_pixel_error(self, msg):
        self.lbl_pixel_info.setText(f"✗ Error: {msg}")
        self.btn_stop_pixel.setVisible(False)
        self.btn_start_pixel.setVisible(True)
        self.btn_start_pixel.setEnabled(True)
        self.btn_start_scan.setEnabled(True)
        
        # Properly clean up the worker thread
        if self._pixel_worker:
            self._pixel_worker.update.disconnect()
            self._pixel_worker.finished.disconnect()
            self._pixel_worker.error.disconnect()
            self._pixel_worker.wait()
            self._pixel_worker = None
        
        self._close_labjack()
        QMessageBox.critical(self, "Pixel Scan Error", msg)

    # ═══════════════════════════════════════════════════════════════════════
    # FILE MENU
    # ═══════════════════════════════════════════════════════════════════════

    def _open_npz(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open FLIM NPZ", "./flim_data",
            "NumPy NPZ (*.npz);;All Files (*)")
        if not path:
            return
        try:
            data = np.load(path)
            if "flim_lifetime_map_ps" not in data:
                QMessageBox.warning(
                    self, "Not a FLIM scan",
                    f"File does not contain flim_lifetime_map_ps.\n"
                    f"Keys: {list(data.files)}")
                return
            self._lifetime_map = data["flim_lifetime_map_ps"]
            self._intensity_map = data.get("flim_intensity_map")
            self._bin_edges = data.get("flim_bin_edges_ps")
            self._frame_3d = data.get("flim_frame_3d")
            self._x_axis = data.get("x_axis_volts")
            self._y_axis = data.get("y_axis_volts")
            if self._x_axis is not None:
                self.sp_goto_x.setRange(0, len(self._x_axis) - 1)
            if self._y_axis is not None:
                self.sp_goto_y.setRange(0, len(self._y_axis) - 1)

            # Recompute lifetime map using hardware delay + background correction
            if self._frame_3d is not None and self._bin_edges is not None:
                hw_delay = self.sp_hw_delay.value()
                fit_end = self.sp_fit_end.value()
                from flim_engine import NativeFlimWrapper
                n_bins = self._frame_3d.shape[2]
                binwidth = int(round((self._bin_edges[-1] - self._bin_edges[0]) / n_bins)) if n_bins > 0 else 200
                ny, nx = self._frame_3d.shape[:2]
                tmp_flim = NativeFlimWrapper.__new__(NativeFlimWrapper)
                tmp_flim.n_bins = n_bins
                tmp_flim.binwidth = binwidth
                tmp_flim.nx_pixels = nx
                tmp_flim.ny_pixels = ny
                recomputed_lt, recomputed_int = tmp_flim.extract_per_pixel_lifetimes(
                    frame_3d=self._frame_3d,
                    bidirectional=self.chk_bidir.isChecked(),
                    hardware_delay_ps=hw_delay if hw_delay > 0 else None,
                    fit_end_ps=fit_end if fit_end > 0 else None
                )
                self._lifetime_map = recomputed_lt
                if recomputed_int is not None:
                    self._intensity_map = recomputed_int

            self.intensity_canvas.clear_image()
            self.lifetime_canvas.clear_image()
            self._show_map()
            self.statusBar().showMessage(f"Loaded: {path}")
            self.btn_export_graphs.setEnabled(True)

            # Update nx/ny spinboxes from loaded data
            ny, nx = self._lifetime_map.shape
            self.sp_nx.setValue(nx)
            self.sp_ny.setValue(ny)
            if self._x_axis is not None and len(self._x_axis) > 0:
                self.sp_xmin.setValue(float(self._x_axis[0]))
                self.sp_xmax.setValue(float(self._x_axis[-1]))
            if self._y_axis is not None and len(self._y_axis) > 0:
                self.sp_ymin.setValue(float(self._y_axis[0]))
                self.sp_ymax.setValue(float(self._y_axis[-1]))
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))

    def _save_image_png(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Image", "flim_image.png",
            "PNG (*.png);;All Files (*)")
        if path:
            self.lifetime_canvas.fig.savefig(
                path, dpi=200, facecolor=BG_DARK, bbox_inches="tight")
            self.statusBar().showMessage(f"Saved: {path}")
