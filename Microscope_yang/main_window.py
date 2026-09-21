"""
main_window.py — PyQt5 Main Window with parameter controls and matplotlib image canvas.
"""

import os
import numpy as np
from pathlib import Path

import time
from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette, QDoubleValidator, QIntValidator
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QSpinBox, QDoubleSpinBox,
    QCheckBox, QPushButton, QComboBox, QProgressBar,
    QFileDialog, QMessageBox, QSplitter, QFrame, QSizePolicy,
    QAction, QMenuBar, QStatusBar, QScrollArea,
)

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from scan_engine import ScanConfig, ScanWorker


# ═══════════════════════════════════════════════════════════════════════════
# COLOUR PALETTE (dark-theme constants)
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


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL QSS
# ═══════════════════════════════════════════════════════════════════════════

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
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
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
    padding: 4px 8px;
    min-height: 24px;
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
    padding: 8px 20px;
    min-height: 28px;
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
QPushButton#abortBtn {{
    background-color: {DANGER};
}}
QPushButton#abortBtn:hover {{
    background-color: #e05560;
}}
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    text-align: center;
    min-height: 20px;
    color: {TEXT_PRIMARY};
    font-size: 11px;
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
# MATPLOTLIB CANVAS WIDGET
# ═══════════════════════════════════════════════════════════════════════════

class ImageCanvas(FigureCanvas):
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
        self.ax.set_xlabel("X voltage (V)", color=TEXT_SECONDARY, fontsize=10)
        self.ax.set_ylabel("Y voltage (V)", color=TEXT_SECONDARY, fontsize=10)
        self.ax.set_title("Scan Image", color=TEXT_PRIMARY, fontsize=12, fontweight="bold")
        self.ax.set_anchor('C')

    def update_image(self, data_2d, cmap="inferno", x_axis=None, y_axis=None):
        """Update the displayed image (ny, nx) array."""
        if x_axis is not None:
            self._x_axis = x_axis
        if y_axis is not None:
            self._y_axis = y_axis
        if self.im is None:
            extent = None
            if x_axis is not None and y_axis is not None:
                extent = [x_axis[0], x_axis[-1], y_axis[0], y_axis[-1]]
            self.im = self.ax.imshow(
                data_2d, cmap=cmap, origin="lower", aspect="equal",
                interpolation="nearest", extent=extent,
            )
            self.cbar = self.fig.colorbar(self.im, ax=self.ax, fraction=0.046, pad=0.04)
            self.cbar.ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
            self.cbar.outline.set_edgecolor(BORDER)
        else:
            self.im.set_data(data_2d)
            self.im.set_cmap(cmap)
            vmin = np.nanmin(data_2d) if np.any(data_2d) else 0
            vmax = np.nanmax(data_2d) if np.any(data_2d) else 1
            self.im.set_clim(vmin, vmax)
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

    def draw_crosshair(self, vx, vy):
        """Draw a crosshair at the given voltage coordinates."""
        if self._crosshair_h:
            self._crosshair_h.remove()
        if self._crosshair_v:
            self._crosshair_v.remove()
        self._crosshair_h = self.ax.axhline(vy, color='cyan', lw=0.8, ls='--')
        self._crosshair_v = self.ax.axvline(vx, color='cyan', lw=0.8, ls='--')
        self.draw_idle()

    def clear_crosshair(self):
        """Remove crosshair without clearing the image."""
        if self._crosshair_h:
            self._crosshair_h.remove()
            self._crosshair_h = None
        if self._crosshair_v:
            self._crosshair_v.remove()
            self._crosshair_v = None
        self.draw_idle()

    def _on_click(self, event):
        if event.inaxes != self.ax or self._x_axis is None or self._y_axis is None:
            return
        vx, vy = event.xdata, event.ydata
        px = int(np.argmin(np.abs(self._x_axis - vx)))
        py = int(np.argmin(np.abs(self._y_axis - vy)))
        snap_vx = float(self._x_axis[px])
        snap_vy = float(self._y_axis[py])
        self.draw_crosshair(snap_vx, snap_vy)
        self.pixel_clicked.emit(px, py, snap_vx, snap_vy)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Quantum Raster Scanner")
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)

        self._worker = None
        self._current_image = None  # (n_ch, ny, nx) full result
        self._current_x_axis = None
        self._current_y_axis = None
        self._selected_px = None
        self._selected_py = None
        self._selected_vx = None
        self._selected_vy = None
        self.lj_handle = None
        self.tagger = None

        self._build_menus()
        self._build_ui()
        
        self.statusBar().showMessage("Initializing TimeTagger...")
        self._init_timetagger()

    def _init_timetagger(self):
        try:
            import TimeTagger
            self.tagger = TimeTagger.createTimeTagger()
            self.statusBar().showMessage(f"TimeTagger connected: {self.tagger.getModel()} ({self.tagger.getSerial()})")
        except Exception as e:
            self.statusBar().showMessage(f"Failed to connect to TimeTagger: {e}")
            QMessageBox.warning(self, "TimeTagger Error", f"Could not connect to TimeTagger:\n{e}")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait()
        
        if self.tagger:
            try:
                import TimeTagger
                TimeTagger.freeTimeTagger(self.tagger)
            except Exception:
                pass
        
        super().closeEvent(event)

    # ── menus ──────────────────────────────────────────────────────────────

    def _build_menus(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")

        save_act = QAction("Save Image as PNG…", self)
        save_act.setShortcut("Ctrl+S")
        save_act.triggered.connect(self._save_image_png)
        file_menu.addAction(save_act)

        open_act = QAction("Open NPZ…", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._open_npz)
        file_menu.addAction(open_act)

        file_menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

    # ── build UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)

        # ---------- LEFT PANEL (parameters) ----------
        left_frame = QFrame()
        left_frame.setStyleSheet(f"background-color: {BG_PANEL};")
        left_frame.setFixedWidth(340)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_widget = QWidget()
        left_layout = QVBoxLayout(scroll_widget)
        left_layout.setContentsMargins(14, 10, 14, 10)
        left_layout.setSpacing(8)

        # Title
        title_lbl = QLabel("⚛  Scan Parameters")
        title_lbl.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY}; "
            f"padding: 6px 0 2px 0;"
        )
        left_layout.addWidget(title_lbl)

        # --- Scan Grid group ---
        grp_grid = QGroupBox("Scan Grid")
        fl_grid = QFormLayout(grp_grid)
        fl_grid.setLabelAlignment(Qt.AlignRight)
        self.sp_nx = self._spin(1, 10000, 100)
        self.sp_ny = self._spin(1, 10000, 100)
        self.chk_bidir = QCheckBox("Bidirectional")
        self.chk_bidir.setChecked(True)
        fl_grid.addRow("NX:", self.sp_nx)
        fl_grid.addRow("NY:", self.sp_ny)
        fl_grid.addRow(self.chk_bidir)
        left_layout.addWidget(grp_grid)

        # --- Timing group ---
        grp_time = QGroupBox("Timing")
        fl_time = QFormLayout(grp_time)
        fl_time.setLabelAlignment(Qt.AlignRight)
        self.sp_dwell = self._dspin(0.001, 10000, 4.0, 3, " ms")
        self.sp_settle = self._dspin(0.0, 10000, 0.0, 3, " ms")
        fl_time.addRow("Dwell:", self.sp_dwell)
        fl_time.addRow("Settle:", self.sp_settle)
        left_layout.addWidget(grp_time)

        # --- X Voltage Range ---
        grp_xv = QGroupBox("X Voltage Range")
        fl_xv = QFormLayout(grp_xv)
        fl_xv.setLabelAlignment(Qt.AlignRight)
        self.sp_xmin = self._dspin(-10, 10, -0.75, 4, " V")
        self.sp_xmax = self._dspin(-10, 10, 0.15, 4, " V")
        fl_xv.addRow("X Vmin:", self.sp_xmin)
        fl_xv.addRow("X Vmax:", self.sp_xmax)
        left_layout.addWidget(grp_xv)

        # --- Y Voltage Range ---
        grp_yv = QGroupBox("Y Voltage Range")
        fl_yv = QFormLayout(grp_yv)
        fl_yv.setLabelAlignment(Qt.AlignRight)
        self.sp_ymin = self._dspin(-10, 10, 0.15, 4, " V")
        self.sp_ymax = self._dspin(-10, 10, 0.90, 4, " V")
        fl_yv.addRow("Y Vmin:", self.sp_ymin)
        fl_yv.addRow("Y Vmax:", self.sp_ymax)
        left_layout.addWidget(grp_yv)

        # --- LabJack group ---
        grp_lj = QGroupBox("LabJack")
        fl_lj = QFormLayout(grp_lj)
        fl_lj.setLabelAlignment(Qt.AlignRight)
        self.le_tdac_x = QLineEdit("TDAC2")
        self.le_tdac_y = QLineEdit("TDAC3")
        self.le_marker_dio = QLineEdit("FIO1")
        self.sp_marker_pulse = self._dspin(0.001, 100, 0.1, 3, " ms")
        fl_lj.addRow("TDAC X:", self.le_tdac_x)
        fl_lj.addRow("TDAC Y:", self.le_tdac_y)
        fl_lj.addRow("Marker DIO:", self.le_marker_dio)
        fl_lj.addRow("Marker Pulse:", self.sp_marker_pulse)
        left_layout.addWidget(grp_lj)

        # --- TimeTagger group ---
        grp_tt = QGroupBox("TimeTagger")
        fl_tt = QFormLayout(grp_tt)
        fl_tt.setLabelAlignment(Qt.AlignRight)
        self.le_click_ch = QLineEdit("2")
        self.le_click_ch.setPlaceholderText("e.g. 1,2,3")
        self.sp_marker_ch = self._spin(1, 18, 4)
        self.sp_marker_trig = self._dspin(-5, 5, 0.2, 3, " V")
        self.sp_click_trig = self._dspin(-5, 5, 0.125, 4, " V")
        fl_tt.addRow("Click Channels:", self.le_click_ch)
        fl_tt.addRow("Marker Channel:", self.sp_marker_ch)
        fl_tt.addRow("Marker Trigger:", self.sp_marker_trig)
        fl_tt.addRow("Click Trigger:", self.sp_click_trig)
        left_layout.addWidget(grp_tt)

        # --- Actions ---
        grp_act = QGroupBox("Actions")
        act_layout = QVBoxLayout(grp_act)

        self.chk_continuous = QCheckBox("Continuous Scan")
        self.chk_continuous.setChecked(False)
        act_layout.addWidget(self.chk_continuous)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("▶  Start Scan")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self._start_scan)

        self.btn_abort = QPushButton("■  Stop Scan")
        self.btn_abort.setObjectName("abortBtn")
        self.btn_abort.setCursor(Qt.PointingHandCursor)
        self.btn_abort.setEnabled(False)
        self.btn_abort.clicked.connect(self._abort_scan)

        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_abort)
        act_layout.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat("%v / %m  pixels  (%p%)")
        act_layout.addWidget(self.progress)

        self.lbl_status = QLabel("Idle")
        self.lbl_status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 2px;")
        act_layout.addWidget(self.lbl_status)

        left_layout.addWidget(grp_act)
        left_layout.addStretch()

        scroll.setWidget(scroll_widget)

        left_container = QVBoxLayout(left_frame)
        left_container.setContentsMargins(0, 0, 0, 0)
        left_container.addWidget(scroll)

        # ---------- RIGHT PANEL (image + toolbar) ----------
        right_frame = QFrame()
        right_frame.setStyleSheet(f"background-color: {BG_DARK};")
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(8, 6, 8, 6)

        # Toolbar row: channel selector + colormap
        toolbar_row = QHBoxLayout()

        lbl_ch = QLabel("Channel:")
        lbl_ch.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
        self.cmb_channel = QComboBox()
        self.cmb_channel.addItem("Ch 0 (default)")
        self.cmb_channel.currentIndexChanged.connect(self._on_channel_changed)

        lbl_cm = QLabel("Colormap:")
        lbl_cm.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600;")
        self.cmb_cmap = QComboBox()
        for cm in ["inferno", "viridis", "hot", "magma", "plasma", "gray", "cividis", "turbo"]:
            self.cmb_cmap.addItem(cm)
        self.cmb_cmap.currentTextChanged.connect(self._on_cmap_changed)

        toolbar_row.addWidget(lbl_ch)
        toolbar_row.addWidget(self.cmb_channel)
        toolbar_row.addSpacing(20)
        toolbar_row.addWidget(lbl_cm)
        toolbar_row.addWidget(self.cmb_cmap)
        toolbar_row.addStretch()
        right_layout.addLayout(toolbar_row)

        # Matplotlib canvas
        self.canvas = ImageCanvas()
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.pixel_clicked.connect(self._on_pixel_clicked)
        right_layout.addWidget(self.canvas, stretch=1)

        # Action bar: pixel coordinate inputs + Move + Clear
        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(4, 4, 4, 4)
        action_bar.setSpacing(6)

        _btn_css = f"""
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

        lbl_px = QLabel("Pixel:")
        lbl_px.setStyleSheet(f"color: {TEXT_PRIMARY}; font-weight: 600; font-size: 12px;")
        self.sp_goto_x = QSpinBox()
        self.sp_goto_x.setRange(0, 9999)
        self.sp_goto_x.setPrefix("X: ")
        self.sp_goto_x.setFixedWidth(75)
        self.sp_goto_y = QSpinBox()
        self.sp_goto_y.setRange(0, 9999)
        self.sp_goto_y.setPrefix("Y: ")
        self.sp_goto_y.setFixedWidth(75)

        self.btn_move_galvo = QPushButton("\u25CE  Move")
        self.btn_move_galvo.setCursor(Qt.PointingHandCursor)
        self.btn_move_galvo.setEnabled(False)
        self.btn_move_galvo.clicked.connect(self._move_galvo_to_pixel)
        self.btn_move_galvo.setStyleSheet(_btn_css)

        self.btn_clear_crosshair = QPushButton("\u2715  Clear")
        self.btn_clear_crosshair.setCursor(Qt.PointingHandCursor)
        self.btn_clear_crosshair.clicked.connect(self._clear_selection)
        self.btn_clear_crosshair.setStyleSheet(_btn_css)

        action_bar.addStretch()
        action_bar.addWidget(lbl_px)
        action_bar.addWidget(self.sp_goto_x)
        action_bar.addWidget(self.sp_goto_y)
        action_bar.addWidget(self.btn_move_galvo)
        action_bar.addWidget(self.btn_clear_crosshair)
        action_bar.addStretch()
        right_layout.addLayout(action_bar)

        # Pixel info label
        self.lbl_pixel_info = QLabel("Click on the map to select a pixel")
        self.lbl_pixel_info.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; padding: 2px 6px;"
        )
        self.lbl_pixel_info.setWordWrap(True)
        right_layout.addWidget(self.lbl_pixel_info)

        # Matplotlib navigation toolbar
        self.nav_toolbar = NavigationToolbar(self.canvas, self)
        self.nav_toolbar.setStyleSheet(
            f"background-color: {BG_PANEL}; border-top: 1px solid {BORDER}; padding: 2px;"
        )
        right_layout.addWidget(self.nav_toolbar)

        # ---------- Splitter ----------
        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)

    # ── widget helpers ─────────────────────────────────────────────────────

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

    # ── build ScanConfig from widgets ──────────────────────────────────────

    def _build_config(self) -> ScanConfig:
        click_ch_text = self.le_click_ch.text().strip()
        click_channels = [int(c.strip()) for c in click_ch_text.split(",") if c.strip()]
        if not click_channels:
            click_channels = [2]

        return ScanConfig(
            nx=self.sp_nx.value(),
            ny=self.sp_ny.value(),
            dwell_s=self.sp_dwell.value() / 1000.0,          # ms → s
            settle_s=self.sp_settle.value() / 1000.0,
            x_vmin=self.sp_xmin.value(),
            x_vmax=self.sp_xmax.value(),
            y_vmin=self.sp_ymin.value(),
            y_vmax=self.sp_ymax.value(),
            bidirectional=self.chk_bidir.isChecked(),
            tdac_x=self.le_tdac_x.text().strip(),
            tdac_y=self.le_tdac_y.text().strip(),
            marker_dio=self.le_marker_dio.text().strip(),
            marker_pulse_s=self.sp_marker_pulse.value() / 1000.0,
            click_channels=click_channels,
            marker_channel=self.sp_marker_ch.value(),
            marker_trigger_level=self.sp_marker_trig.value(),
            click_trigger_level=self.sp_click_trig.value(),
            save_dir=".",
            continuous=self.chk_continuous.isChecked(),
        )

    # ── scan control ───────────────────────────────────────────────────────

    def _start_scan(self):
        if not self.tagger:
            QMessageBox.critical(self, "Error", "TimeTagger is not connected. Please restart or check connections.")
            return

        cfg = self._build_config()
        n_total = cfg.nx * cfg.ny

        self.progress.setMaximum(n_total)
        self.progress.setValue(0)
        self.lbl_status.setText("Scanning…")
        self.lbl_status.setStyleSheet(f"color: {WARNING}; font-size: 11px; padding: 2px;")
        self.btn_start.setEnabled(False)
        self.btn_abort.setEnabled(True)
        self.canvas.clear_image()

        self._worker = ScanWorker(cfg, self.tagger)
        self._worker.progress.connect(self._on_progress)
        self._worker.row_done.connect(self._on_row_done)
        self._worker.finished_scan.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _abort_scan(self):
        if self._worker:
            self._worker.abort()
            self.lbl_status.setText("Stopping…")
            self.btn_abort.setEnabled(False)

    # ── slots ──────────────────────────────────────────────────────────────

    def _on_progress(self, current, total):
        self.progress.setValue(current)

    def _on_row_done(self, row_idx, img_array):
        """Live image update after each completed row."""
        ch = self.cmb_channel.currentIndex()
        if ch >= img_array.shape[0]:
            ch = 0
        self.canvas.update_image(
            img_array[ch],
            cmap=self.cmb_cmap.currentText(),
        )
        self.lbl_status.setText(f"Row {row_idx + 1} complete")

    def _on_scan_finished(self, result: dict):
        self._current_image = result["image"]
        self._current_x_axis = result.get("x_axis")
        self._current_y_axis = result.get("y_axis")
        if self._current_x_axis is not None:
            self.sp_goto_x.setRange(0, len(self._current_x_axis) - 1)
        if self._current_y_axis is not None:
            self.sp_goto_y.setRange(0, len(self._current_y_axis) - 1)

        # Populate channel selector
        n_ch = self._current_image.shape[0]
        self.cmb_channel.blockSignals(True)
        self.cmb_channel.clear()
        for i in range(n_ch):
            self.cmb_channel.addItem(f"Ch {i}")
        self.cmb_channel.setCurrentIndex(0)
        self.cmb_channel.blockSignals(False)

        # Show final image
        self.canvas.clear_image()
        self.canvas.update_image(
            self._current_image[0],
            cmap=self.cmb_cmap.currentText(),
            x_axis=self._current_x_axis,
            y_axis=self._current_y_axis,
        )

        self.lbl_status.setText(f"✓ Scan frame complete — saved to {result['npz_path']}")
        self.lbl_status.setStyleSheet(f"color: {SUCCESS}; font-size: 11px; padding: 2px;")
        
        # If continuous, worker keeps running. If not, it stops, so we re-enable start
        if not self._worker.cfg.continuous or self._worker._abort:
            self.btn_start.setEnabled(True)
            self.btn_abort.setEnabled(False)
            if self._worker._abort:
                self.lbl_status.setText(f"✓ Scan stopped — partial/final saved to {result['npz_path']}")

        self.statusBar().showMessage(f"Saved: {result['npz_path']}  |  {result['meta_path']}")

    def _on_scan_error(self, msg):
        self.lbl_status.setText(f"✗ Error: {msg}")
        self.lbl_status.setStyleSheet(f"color: {DANGER}; font-size: 11px; padding: 2px;")
        self.btn_start.setEnabled(True)
        self.btn_abort.setEnabled(False)
        QMessageBox.critical(self, "Scan Error", msg)

    def _on_channel_changed(self, index):
        if self._current_image is not None and index < self._current_image.shape[0]:
            self.canvas.clear_image()
            self.canvas.update_image(
                self._current_image[index],
                cmap=self.cmb_cmap.currentText(),
                x_axis=self._current_x_axis,
                y_axis=self._current_y_axis,
            )

    def _on_cmap_changed(self, cmap_name):
        if self._current_image is not None:
            ch = self.cmb_channel.currentIndex()
            if ch < self._current_image.shape[0]:
                self.canvas.update_image(self._current_image[ch], cmap=cmap_name)

    # ── file menu actions ──────────────────────────────────────────────────

    def _save_image_png(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Image", "scan_image.png", "PNG (*.png);;All Files (*)",
        )
        if path:
            self.canvas.fig.savefig(path, dpi=200, facecolor=BG_DARK, bbox_inches="tight")
            self.statusBar().showMessage(f"Image saved to {path}")

    def _open_npz(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open NPZ Scan File", "", "NumPy NPZ (*.npz);;All Files (*)",
        )
        if not path:
            return
        try:
            data = np.load(path)
            if "counts_ch_y_x" in data:
                img = data["counts_ch_y_x"]
            elif "counts_ch_bin" in data:
                img = data["counts_ch_bin"]
            else:
                raise KeyError("NPZ does not contain counts_ch_y_x or counts_ch_bin")

            x_axis = data.get("x_axis_volts")
            y_axis = data.get("y_axis_volts")

            self._current_image = img
            self._current_x_axis = x_axis
            self._current_y_axis = y_axis
            if x_axis is not None:
                self.sp_goto_x.setRange(0, len(x_axis) - 1)
            if y_axis is not None:
                self.sp_goto_y.setRange(0, len(y_axis) - 1)

            n_ch = img.shape[0]
            self.cmb_channel.blockSignals(True)
            self.cmb_channel.clear()
            for i in range(n_ch):
                self.cmb_channel.addItem(f"Ch {i}")
            self.cmb_channel.setCurrentIndex(0)
            self.cmb_channel.blockSignals(False)

            self.canvas.clear_image()
            self.canvas.update_image(
                img[0], cmap=self.cmb_cmap.currentText(),
                x_axis=x_axis, y_axis=y_axis,
            )
            self.statusBar().showMessage(f"Loaded: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))

    # ── pixel selection & galvo move ───────────────────────────────────────

    def _select_pixel(self, px, py):
        """Update selection state, sync spinboxes, draw crosshair, update info label."""
        if self._current_x_axis is None or self._current_y_axis is None:
            return
        px = max(0, min(px, len(self._current_x_axis) - 1))
        py = max(0, min(py, len(self._current_y_axis) - 1))
        vx = float(self._current_x_axis[px])
        vy = float(self._current_y_axis[py])

        self._selected_px = px
        self._selected_py = py
        self._selected_vx = vx
        self._selected_vy = vy

        self.sp_goto_x.blockSignals(True)
        self.sp_goto_y.blockSignals(True)
        self.sp_goto_x.setValue(px)
        self.sp_goto_y.setValue(py)
        self.sp_goto_x.blockSignals(False)
        self.sp_goto_y.blockSignals(False)

        self.btn_move_galvo.setEnabled(True)
        self.canvas.draw_crosshair(vx, vy)

        intensity = ""
        if self._current_image is not None:
            ch = self.cmb_channel.currentIndex()
            if ch < self._current_image.shape[0]:
                intensity = f"  |  Intensity: {int(self._current_image[ch, py, px])}"
        self.lbl_pixel_info.setText(
            f"Pixel ({px}, {py})   V = ({vx:.4f}, {vy:.4f}) V{intensity}")

    def _on_pixel_clicked(self, px, py, vx, vy):
        self._select_pixel(px, py)

    def _clear_selection(self):
        """Clear crosshair and reset selection state."""
        self._selected_px = None
        self._selected_py = None
        self._selected_vx = None
        self._selected_vy = None
        self.canvas.clear_crosshair()
        self.btn_move_galvo.setEnabled(False)
        self.lbl_pixel_info.setText("Click on the map to select a pixel")

    def _open_labjack(self):
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
        if self.lj_handle is not None:
            try:
                from labjack import ljm
                ljm.close(self.lj_handle)
            except Exception:
                pass
            self.lj_handle = None

    def _move_galvo_to_pixel(self):
        """Read pixel coords from spinboxes, select that pixel, then move the galvo."""
        if self._current_x_axis is None or self._current_y_axis is None:
            QMessageBox.warning(self, "No Scan Data", "Load or run a scan first.")
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
                f"\u2713 Galvo moved to pixel ({self._selected_px}, {self._selected_py})   "
                f"V = ({self._selected_vx:.4f}, {self._selected_vy:.4f}) V")
        except Exception as e:
            QMessageBox.critical(self, "Galvo Move Error",
                                 f"Could not move galvo:\n{e}")
        finally:
            self._close_labjack()
