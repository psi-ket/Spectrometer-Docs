"""Settings tab: application settings, tab visibility and diagnostics."""
from __future__ import annotations

import json
from dataclasses import asdict

from qtpy.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget, QListWidget, QListWidgetItem)
from qtpy.QtCore import Qt

from ...storage.settings_store import AppSettings
from ..widgets.common import KeyValueTable, show_error, scroll


class SettingsTab(QWidget):
    def __init__(self, controller, tab_manager, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.tab_manager = tab_manager
        s = controller.settings
        box = QGroupBox("Application settings"); f = QFormLayout(box)
        self.pkg = self._path_row(s.swabian_package_path, "directory"); self.data = self._path_row(s.data_directory, "directory"); self.raw = self._path_row(s.raw_directory, "directory"); self.res = self._path_row(s.results_directory, "directory"); self.presets = self._path_row(s.presets_directory, "directory"); self.demo = self._path_row(s.demo_directory, "directory")
        self.autosave = QCheckBox(); self.autosave.setChecked(s.autosave)
        self.policy = QComboBox(); self.policy.addItems(["ALWAYS", "ASK", "NEVER"]); self.policy.setCurrentText(s.raw_recording_policy)
        self.refresh = QDoubleSpinBox(); self.refresh.setRange(0.5, 60); self.refresh.setValue(s.plot_refresh_hz); self.refresh.setSuffix(" Hz")
        self.health = QDoubleSpinBox(); self.health.setRange(0.2, 60); self.health.setValue(s.health_poll_s); self.health.setSuffix(" s")
        self.level = QComboBox(); self.level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"]); self.level.setCurrentText(s.log_level)
        self.mem = QSpinBox(); self.mem.setRange(64, 65536); self.mem.setValue(s.max_ui_memory_mb); self.mem.setSuffix(" MB")
        self.theme = QComboBox(); self.theme.addItems(["system", "light", "dark"]); self.theme.setCurrentText(s.theme)
        self.mode = QComboBox(); self.mode.addItems(["BASIC", "ADVANCED", "DEVELOPER"]); self.mode.setCurrentText(s.app_mode)
        self.dur = QDoubleSpinBox(); self.dur.setRange(0, 1e6); self.dur.setValue(s.default_duration_s); self.dur.setSuffix(" s")
        self.maxfile = QSpinBox(); self.maxfile.setRange(1, 100000); self.maxfile.setValue(s.default_max_file_size_mb); self.maxfile.setSuffix(" MB")
        self.autosim = QCheckBox(); self.autosim.setChecked(s.auto_connect_simulator_when_no_hardware)
        self.scan_net = QCheckBox(); self.scan_net.setChecked(s.scan_network_on_startup)
        for lab, w in (("Swabian Python package path (optional)", self.pkg), ("Default data directory", self.data), ("Default raw TTbin directory (empty = inside run)", self.raw), ("Default result directory (empty = inside run)", self.res), ("Presets directory", self.presets), ("Demo dataset directory", self.demo), ("Autosave results", self.autosave), ("Raw TTbin policy", self.policy), ("Plot refresh", self.refresh), ("Health poll interval", self.health), ("Log level", self.level), ("Max UI memory buffer", self.mem), ("Theme", self.theme), ("Application mode", self.mode), ("Default acquisition duration", self.dur), ("Default max TTbin file size", self.maxfile), ("Auto-connect simulator when no hardware", self.autosim), ("Scan network servers on startup", self.scan_net)):
            f.addRow(lab, w)
        b_apply = QPushButton("Apply & save settings"); b_apply.clicked.connect(self.apply)
        f.addRow(b_apply)
        tabs = QGroupBox("Tab visibility (check = visible; drag to reorder)"); tl = QVBoxLayout(tabs)
        self.tab_list = QListWidget(); self.tab_list.setDragDropMode(QListWidget.InternalMove)
        tl.addWidget(self.tab_list)
        tr = QHBoxLayout()
        b_tabs = QPushButton("Apply tab layout"); b_tabs.clicked.connect(self.apply_tabs); b_reset = QPushButton("Reset UI layout"); b_reset.clicked.connect(self.reset_tabs)
        tr.addWidget(b_tabs); tr.addWidget(b_reset); tl.addLayout(tr)
        diag = QGroupBox("Diagnostics"); dl = QVBoxLayout(diag); self.diag = KeyValueTable(); dl.addWidget(self.diag)
        inner = QWidget(); il = QVBoxLayout(inner); il.addWidget(box); il.addWidget(tabs); il.addWidget(diag)
        lay = QVBoxLayout(self); lay.addWidget(scroll(inner))
        self.refresh_tabs(); self.refresh_diag()

    def _path_row(self, value: str, kind: str) -> QWidget:
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(0, 0, 0, 0)
        e = QLineEdit(value); b = QPushButton("…"); b.setMaximumWidth(30)
        b.clicked.connect(lambda: e.setText(QFileDialog.getExistingDirectory(self, "Choose directory", e.text() or ".") or e.text()))
        l.addWidget(e); l.addWidget(b); w.edit = e
        return w

    def apply(self) -> None:
        s = AppSettings(swabian_package_path=self.pkg.edit.text(), data_directory=self.data.edit.text(), raw_directory=self.raw.edit.text(), results_directory=self.res.edit.text(), presets_directory=self.presets.edit.text(), demo_directory=self.demo.edit.text(), autosave=self.autosave.isChecked(), raw_recording_policy=self.policy.currentText(), plot_refresh_hz=self.refresh.value(), health_poll_s=self.health.value(), log_level=self.level.currentText(), max_ui_memory_mb=self.mem.value(), theme=self.theme.currentText(), app_mode=self.mode.currentText(), default_duration_s=self.dur.value(), default_max_file_size_mb=self.maxfile.value(), simulator_dataset=self.controller.settings.simulator_dataset, auto_connect_simulator_when_no_hardware=self.autosim.isChecked(), scan_network_on_startup=self.scan_net.isChecked())
        try:
            self.controller.update_settings(s)
        except Exception as exc:
            show_error(self, "Settings", str(exc)); return
        self.tab_manager.apply_mode(s.app_mode)
        self.refresh_tabs()

    def refresh_tabs(self) -> None:
        self.tab_list.clear()
        for tid, title, visible in self.tab_manager.layout():
            it = QListWidgetItem(title); it.setData(Qt.UserRole, tid); it.setFlags(it.flags() | Qt.ItemIsUserCheckable); it.setCheckState(Qt.Checked if visible else Qt.Unchecked)
            self.tab_list.addItem(it)

    def apply_tabs(self) -> None:
        order = []; hidden = []
        for i in range(self.tab_list.count()):
            it = self.tab_list.item(i); tid = it.data(Qt.UserRole); order.append(tid)
            if it.checkState() != Qt.Checked:
                hidden.append(tid)
        self.tab_manager.apply_layout(order, hidden)

    def reset_tabs(self) -> None:
        self.tab_manager.reset_layout(); self.refresh_tabs()

    def refresh_diag(self) -> None:
        d = self.controller.diagnostics()
        flat = {k: (json.dumps(v, default=str) if isinstance(v, dict) else v) for k, v in d.items()}
        self.diag.set_data(flat)
