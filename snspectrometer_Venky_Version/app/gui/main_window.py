"""Main window: header status, managed tabs, status bar, menus."""
from __future__ import annotations

import base64
import time

from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QAction, QKeySequence
from qtpy.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox, QProgressBar, QStatusBar, QTabWidget, QVBoxLayout, QWidget

from .. import APP_NAME, __version__
from .tab_manager import TabManager
from .tabs.advanced_tab import AdvancedTab
from .tabs.analysis_tab import AnalysisTab
from .tabs.channels_tab import ChannelsTab
from .tabs.combiner_tab import CombinerTab
from .tabs.delay_cal_tab import DelayCalibrationTab
from .tabs.experiments_tab import ExperimentsTab
from .tabs.groups_tab import GroupsTab
from .tabs.hardware_tab import HardwareTab
from .tabs.logs_tab import LogsTab
from .tabs.measurement_tab import JSITab, MeasurementTab
from .tabs.overview_tab import OverviewTab
from .tabs.raw_stream_tab import RawStreamTab
from .tabs.sequence_tab import SequenceTab
from .tabs.settings_tab import SettingsTab
from .tabs.sync_tab import SyncTab
from .widgets.common import TextDialog, show_error


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 950)
        # header
        header = QWidget(); hl = QHBoxLayout(header); hl.setContentsMargins(8, 4, 8, 4)
        self.lbl_exp = QLabel(); self.lbl_run = QLabel(); self.lbl_hw = QLabel(); self.lbl_sync = QLabel()
        for w in (self.lbl_exp, self.lbl_run, self.lbl_hw, self.lbl_sync):
            w.setStyleSheet("padding: 2px 10px; border: 1px solid #bbb; border-radius: 4px; background: #f6f6f6;")
            hl.addWidget(w)
        hl.addStretch(1)
        self.tabs = QTabWidget(); self.tabs.setMovable(True)
        self.tab_manager = TabManager(self.tabs, controller.ui_prefs, self._on_tabs_changed)
        c = controller
        tm = self.tab_manager
        tm.register("overview", "Overview", lambda: OverviewTab(c))
        tm.register("hardware", "Hardware", lambda: HardwareTab(c))
        tm.register("channels", "Channels", lambda: ChannelsTab(c))
        tm.register("sync", "Synchronization", lambda: SyncTab(c), "advanced")
        tm.register("measurements", "Measurements", lambda: GroupsTab(c))
        tm.register("countrate", "Count Rate", lambda: MeasurementTab(c, "countrate"))
        tm.register("counter", "Count Trace", lambda: MeasurementTab(c, "counter"))
        tm.register("gated_counter", "Count Between Markers", lambda: MeasurementTab(c, "gated_counter"))
        tm.register("histogram", "Histogram", lambda: MeasurementTab(c, "histogram"))
        tm.register("coincidences", "Coincidences", lambda: MeasurementTab(c, "coincidences"))
        tm.register("coincidence_matrix", "Coincidence Matrix", lambda: MeasurementTab(c, "coincidence_matrix"), "advanced")
        tm.register("g2", "G2", lambda: MeasurementTab(c, "g2"))
        tm.register("jsi", "JSI", lambda: JSITab(c), "advanced")
        tm.register("advanced", "Advanced", lambda: AdvancedTab(c), "advanced")
        tm.register("analysis", "Analysis", lambda: AnalysisTab(c))
        tm.register("raw_stream", "Raw Stream", lambda: RawStreamTab(c), "developer")
        tm.register("combiner", "TTbin Utility", lambda: CombinerTab(c), "advanced")
        tm.register("delay_cal", "Delay Calibration", lambda: DelayCalibrationTab(c), "advanced")
        tm.register("sequences", "Sequences", lambda: SequenceTab(c), "advanced")
        tm.register("experiments", "Experiments", lambda: ExperimentsTab(c))
        tm.register("logs", "Logs", lambda: LogsTab(c))
        tm.register("settings", "Settings", lambda: SettingsTab(c, tm))
        if not controller.ui_prefs.tab_order and not controller.ui_prefs.hidden_tabs:
            tm.apply_mode(controller.settings.app_mode)
        else:
            tm.rebuild()
        central = QWidget(); cl = QVBoxLayout(central); cl.setContentsMargins(0, 0, 0, 0); cl.addWidget(header); cl.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        # status bar
        sb = QStatusBar(); self.setStatusBar(sb)
        self.status_label = QLabel("ready"); self.task_bar = QProgressBar(); self.task_bar.setMaximumWidth(220); self.task_bar.setRange(0, 1000); self.task_bar.hide(); self.task_label = QLabel("")
        sb.addWidget(self.status_label, 1); sb.addPermanentWidget(self.task_label); sb.addPermanentWidget(self.task_bar)
        self._build_menus()
        # signals
        c.error_reported.connect(lambda t, r, d, s: show_error(self, t, r, d, s))
        c.status_message.connect(self._status)
        c.task_progress.connect(self._task_progress)
        c.task_finished.connect(lambda name, _: (self.task_bar.hide(), self.task_label.setText("")))
        c.hardware_event.connect(lambda *_: self._update_header())
        c.measurement_event.connect(lambda *_: self._update_header())
        c.experiment_changed.connect(self._update_header)
        c.sync_changed.connect(lambda *_: self._update_header())
        c.health.connect(lambda _: self._update_header())
        c.log_message.connect(self._on_log)
        self._restore_geometry()
        self._update_header()
        QTimer.singleShot(200, self._startup)

    # ------------------------------------------------------------------ menus
    def _build_menus(self) -> None:
        m = self.menuBar()
        fm = m.addMenu("&File")
        self._act(fm, "New experiment…", self.new_experiment, "Ctrl+N")
        self._act(fm, "Open experiment…", self.open_experiment, "Ctrl+O")
        self._act(fm, "Save experiment", lambda: self._status(f"Saved {self.controller.save_experiment()}"), "Ctrl+S")
        self._act(fm, "Save experiment as…", self.save_experiment_as)
        fm.addSeparator()
        self._act(fm, "Open TTbin in Analysis…", self.open_ttbin)
        fm.addSeparator()
        self._act(fm, "Exit", self.close, "Ctrl+Q")
        vm = m.addMenu("&View")
        mode_menu = vm.addMenu("Application mode")
        for mode in ("BASIC", "ADVANCED", "DEVELOPER"):
            self._act(mode_menu, mode.title(), lambda _=False, mo=mode: self.set_mode(mo))
        self._act(vm, "Restore hidden tabs", self.tab_manager.restore_hidden)
        self._act(vm, "Reset UI layout", self.tab_manager.reset_layout)
        self._act(vm, "Hide current tab", self.hide_current_tab)
        hm = m.addMenu("&Hardware")
        self._act(hm, "Refresh discovery", self.controller.discover_async, "F5")
        self._act(hm, "Connect first USB Time Tagger", lambda: self.controller.connect_local_async(""))
        self._act(hm, "Connect simulator (no hardware)", lambda: self.controller.connect_simulator_async())
        self._act(hm, "Stop all acquisitions", self.stop_all)
        helpm = m.addMenu("&Help")
        self._act(helpm, "Diagnostics", self.show_diagnostics)
        self._act(helpm, "About", self.about)

    def _act(self, menu, text, fn, shortcut=None) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(fn)
        menu.addAction(a)
        return a

    # ------------------------------------------------------------------ actions
    def _startup(self) -> None:
        c = self.controller
        c.discover_async()
        if c.interrupted_runs:
            QMessageBox.warning(self, "Interrupted runs", f"{len(c.interrupted_runs)} run(s) were active when the application last terminated. They are marked INTERRUPTED (see Experiments tab).")
        if c.settings.auto_connect_simulator_when_no_hardware:
            QTimer.singleShot(4000, self._maybe_simulator)

    def _maybe_simulator(self) -> None:
        c = self.controller
        if not c.device_ids() and not c.hardware.discovered and not c.hardware.servers and c.api.available:
            if QMessageBox.question(self, "No hardware detected", "No Time Tagger was found. Connect the simulator (synthetic dataset through the real Swabian engine)?") == QMessageBox.Yes:
                c.connect_simulator_async()

    def set_mode(self, mode: str) -> None:
        s = self.controller.settings
        s.app_mode = mode
        self.controller.update_settings(s)
        self.tab_manager.apply_mode(mode)

    def hide_current_tab(self) -> None:
        w = self.tabs.currentWidget()
        for d in self.tab_manager.defs:
            if d.widget is w:
                self.tab_manager.hide(d.id)

    def new_experiment(self) -> None:
        name, ok = QInputDialog.getText(self, "New experiment", "Experiment name:")
        if ok and name:
            self.controller.new_experiment(name)
            self._status(f"New experiment {name}")

    def open_experiment(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open experiment", self.controller.settings.data_directory, "JSON (*.json)")
        if path:
            try:
                self.controller.load_experiment(path)
            except Exception as exc:
                show_error(self, "Open experiment", str(exc))

    def save_experiment_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save experiment as", "experiment.json", "JSON (*.json)")
        if path:
            self.controller.save_experiment(path)

    def open_ttbin(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open TTbin", self.controller.settings.data_directory, "TTbin (*.ttbin)")
        if path:
            self.open_in_analysis(path)

    def open_in_analysis(self, path: str) -> None:
        self.tab_manager.select("analysis")
        tab = self.tab_manager.widget("analysis")
        tab.file_edit.setText(path)
        tab.open_file(path)

    def stop_all(self) -> None:
        if QMessageBox.question(self, "Stop all", "Stop all running acquisitions?") == QMessageBox.Yes:
            self.controller.measurements.stop_all("stopped by user (stop all)")

    def show_diagnostics(self) -> None:
        import json
        TextDialog("Diagnostics", json.dumps(self.controller.diagnostics(), indent=2, default=str), self).exec()

    def about(self) -> None:
        QMessageBox.about(self, "About", f"<b>{APP_NAME} {__version__}</b><br>Scientific SNSPD spectrometer application on the Swabian Instruments Time Tagger Python API.<br>Library: {self.controller.api.version or 'not available'}")

    # ------------------------------------------------------------------ status
    def _status(self, text: str) -> None:
        self.status_label.setText(text)

    def _task_progress(self, name: str, frac: float, msg: str) -> None:
        self.task_bar.show(); self.task_bar.setValue(int(frac * 1000)); self.task_label.setText(f"{name} {msg}".strip())

    def _on_log(self, entry: dict) -> None:
        if entry["level"] in ("WARNING", "ERROR", "CRITICAL"):
            self.status_label.setText(f"[{entry['level']}] {entry['message'][:160]}")

    def _update_header(self) -> None:
        c = self.controller
        e = c.experiment
        self.lbl_exp.setText(f"Experiment: {e.experiment_name} ({e.experiment_id})")
        active = c.active_groups()
        self.lbl_run.setText("Run: " + (", ".join(f"{g.name} {g.status.value} {g.elapsed_s():.0f}s" for g in active) if active else "idle"))
        devs = c.device_ids()
        self.lbl_hw.setText("Hardware: " + (", ".join(devs) if devs else "no device") + (" | library MISSING" if not c.api.available else ""))
        states = []
        for d in devs:
            try:
                states.append(f"{d.split(':')[0]} {c.sync_state(d).value}")
            except Exception:
                pass
        self.lbl_sync.setText("Sync: " + (", ".join(states) if states else "—"))

    def _on_tabs_changed(self) -> None:
        self.controller.save_ui_prefs()

    # ------------------------------------------------------------------ geometry
    def _restore_geometry(self) -> None:
        p = self.controller.ui_prefs
        try:
            if p.window_geometry:
                self.restoreGeometry(base64.b64decode(p.window_geometry))
            if p.window_state:
                self.restoreState(base64.b64decode(p.window_state))
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        c = self.controller
        if c.active_groups():
            if QMessageBox.question(self, "Acquisition active", "Measurements are running. Stop them and exit?") != QMessageBox.Yes:
                event.ignore(); return
        c.ui_prefs.window_geometry = base64.b64encode(bytes(self.saveGeometry())).decode()
        c.ui_prefs.window_state = base64.b64encode(bytes(self.saveState())).decode()
        c.ui_prefs.tab_order = self.tab_manager.order()
        c.shutdown()
        event.accept()
