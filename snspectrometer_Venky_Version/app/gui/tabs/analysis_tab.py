"""Analysis tab: TTbin replay with transport controls, evolving plots, re-analysis and plugins."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import numpy as np
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QProgressBar, QPushButton, QSlider, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ...analysis.plugins import list_plugins, get_plugin
from ...analysis.replay_session import ReplaySession, speed_presets
from ...measurements.base import Snapshot
from ...measurements.registry import get_measurement_class, list_measurement_types
from ...models.channels import DetectorMap
from ...models.results import MeasurementResult, load_json
from ...models.units import format_time_ps
from ...plotting.panel import MeasurementPlotPanel
from ..widgets.common import KeyValueTable, TextDialog, scroll, show_error
from ..widgets.config_form import ConfigForm
from ..widgets.help_panel import HelpPanel

EVOLUTION_MODES = ["replace plot", "accumulate plot (overlay history)", "show history (slider)", "difference between updates"]


class AnalysisTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.session: Optional[ReplaySession] = None
        self.labels: dict[int, str] = {}
        self.wavelengths: dict[int, float] = {}
        self.panels: dict[str, MeasurementPlotPanel] = {}
        self.forms: dict[str, ConfigForm] = {}
        self._prev_snap: dict[str, Snapshot] = {}

        # ---------------- left
        left = QWidget(); ll = QVBoxLayout(left)
        fbox = QGroupBox("Raw TTbin"); fl = QVBoxLayout(fbox)
        r = QHBoxLayout(); self.file_edit = QLineEdit(); self.file_edit.setPlaceholderText("measurement.ttbin (header of a split sequence, or a single part)")
        b_open = QPushButton("Open…"); b_open.clicked.connect(self.open_dialog); b_load = QPushButton("Load"); b_load.clicked.connect(lambda: self.open_file(self.file_edit.text()))
        r.addWidget(self.file_edit); r.addWidget(b_open); r.addWidget(b_load); fl.addLayout(r)
        self.recent = QComboBox(); self.recent.addItem("recent files…"); self.recent.addItems(controller.ui_prefs.recent_files); self.recent.currentTextChanged.connect(lambda t: self.file_edit.setText(t) if t.endswith(".ttbin") else None)
        fl.addWidget(self.recent)
        self.file_info = KeyValueTable(); self.file_info.setMaximumHeight(170); fl.addWidget(self.file_info)
        ll.addWidget(fbox)
        mbox = QGroupBox("Measurements to compute from the stream"); ml = QVBoxLayout(mbox)
        r2 = QHBoxLayout(); self.type_combo = QComboBox()
        for cls in list_measurement_types():
            if cls.type_name != "raw_stream":
                self.type_combo.addItem(cls.display_name, cls.type_name)
        b_add = QPushButton("Add"); b_add.clicked.connect(self.add_measurement); b_clear = QPushButton("Remove all"); b_clear.clicked.connect(self.clear_measurements)
        r2.addWidget(self.type_combo); r2.addWidget(b_add); r2.addWidget(b_clear); ml.addLayout(r2)
        self.forms_tabs = QTabWidget(); ml.addWidget(self.forms_tabs)
        ll.addWidget(mbox)
        tbox = QGroupBox("Replay transport"); tl = QFormLayout(tbox)
        self.speed = QComboBox()
        for lab, v in speed_presets():
            self.speed.addItem(lab, v)
        self.speed.setCurrentText("1x")
        self.custom_speed = QDoubleSpinBox(); self.custom_speed.setRange(0.0, 10000.0); self.custom_speed.setDecimals(2); self.custom_speed.setSpecialValueText("use preset"); self.custom_speed.setValue(0.0)
        sp = QHBoxLayout(); sp.addWidget(self.speed); sp.addWidget(QLabel("custom:")); sp.addWidget(self.custom_speed)
        tl.addRow("Speed", sp)
        self.speed_note = QLabel("1x = real time; Max = as fast as possible; speeds below 0.1x are not supported by the library and are clamped."); self.speed_note.setWordWrap(True); tl.addRow(self.speed_note)
        self.begin = QDoubleSpinBox(); self.begin.setRange(0, 1e7); self.begin.setSuffix(" s"); self.dur = QDoubleSpinBox(); self.dur.setRange(0, 1e7); self.dur.setSuffix(" s"); self.dur.setSpecialValueText("all")
        bd = QHBoxLayout(); bd.addWidget(QLabel("begin")); bd.addWidget(self.begin); bd.addWidget(QLabel("duration")); bd.addWidget(self.dur); tl.addRow("Window", bd)
        row = QHBoxLayout()
        self.b_play = QPushButton("▶ Play"); self.b_pause = QPushButton("⏸ Pause"); self.b_resume = QPushButton("Resume"); self.b_stop = QPushButton("⏹ Stop"); self.b_restart = QPushButton("↺ Restart")
        for b in (self.b_play, self.b_pause, self.b_resume, self.b_stop, self.b_restart):
            row.addWidget(b)
        tl.addRow(row)
        self.position = QSlider(Qt.Horizontal); self.position.setRange(0, 1000); self.position.setEnabled(False)
        self.pos_label = QLabel("position: — / —   elapsed — remaining —")
        self.progress = QProgressBar(); self.progress.setRange(0, 1000)
        tl.addRow(self.position); tl.addRow(self.pos_label); tl.addRow(self.progress)
        self.evolution = QComboBox(); self.evolution.addItems(EVOLUTION_MODES); tl.addRow("Evolution view", self.evolution)
        self.history_slider = QSlider(Qt.Horizontal); self.history_slider.setRange(0, 0); tl.addRow("History", self.history_slider)
        ll.addWidget(tbox)
        rbox = QGroupBox("Results / re-analysis"); rl = QVBoxLayout(rbox)
        r3 = QHBoxLayout(); b_save = QPushButton("Save results (JSON+NPZ, with provenance)"); b_save.clicked.connect(self.save_results); r3.addWidget(b_save); rl.addLayout(r3)
        r4 = QHBoxLayout(); self.plugin_combo = QComboBox(); self.plugin_target = QComboBox(); b_plugin = QPushButton("Run plugin"); b_plugin.clicked.connect(self.run_plugin)
        r4.addWidget(QLabel("Plugin")); r4.addWidget(self.plugin_combo); r4.addWidget(QLabel("on")); r4.addWidget(self.plugin_target); r4.addWidget(b_plugin); rl.addLayout(r4)
        self.plugin_form_host = QVBoxLayout(); rl.addLayout(self.plugin_form_host)
        self.plugin_form: Optional[ConfigForm] = None
        b_load_res = QPushButton("Load saved result (JSON) for plugin analysis…"); b_load_res.clicked.connect(self.load_result); rl.addWidget(b_load_res)
        ll.addWidget(rbox)
        self.status = QLabel(""); self.status.setWordWrap(True); ll.addWidget(self.status)
        ll.addWidget(HelpPanel("""Offline analysis replays the raw TTbin through TimeTaggerVirtual into the same measurement
classes used live (Countrate, Counter, Histogram, Correlation, CorrelationPairs, Coincidences ...).
Any parameter (bin width, pairs, coincidence window, normalisation, background) can be changed and the
file re-analysed; the raw file is never modified. Pause stops the replay and resumes by re-queuing the file
from the paused position (a small overlap of already-buffered tags can occur). Results are saved next to
the file (analysis/) with provenance: source file(s), parameters, library and application versions."""))
        ll.addStretch(1)

        # ---------------- right
        right = QWidget(); rr = QVBoxLayout(right)
        self.plot_tabs = QTabWidget(); rr.addWidget(self.plot_tabs, 1)
        self.scalars = KeyValueTable(); self.scalars.setMaximumHeight(160); rr.addWidget(self.scalars)
        split = QSplitter(Qt.Horizontal); split.addWidget(scroll(left)); split.addWidget(right); split.setSizes([520, 900])
        lay = QVBoxLayout(self); lay.addWidget(split)

        self.b_play.clicked.connect(self.play); self.b_pause.clicked.connect(self.pause); self.b_resume.clicked.connect(self.resume); self.b_stop.clicked.connect(self.stop); self.b_restart.clicked.connect(self.restart)
        self.speed.currentIndexChanged.connect(self.change_speed); self.custom_speed.valueChanged.connect(self.change_speed)
        self.history_slider.valueChanged.connect(self.show_history)
        self.evolution.currentIndexChanged.connect(lambda _: self._refresh_plots())
        self.plugin_combo.currentIndexChanged.connect(self._build_plugin_form)
        self.plugin_target.currentIndexChanged.connect(self._refresh_plugins)
        controller.replay_snapshots.connect(self.on_snapshots)
        controller.replay_event.connect(self.on_replay_event)
        self.loaded_results: dict[str, MeasurementResult] = {}
        self._refresh_plugins()

    # ------------------------------------------------------------------ file
    def open_dialog(self) -> None:
        start = self.controller.ui_prefs.default_directories.get("ttbin", self.controller.settings.data_directory)
        path, _ = QFileDialog.getOpenFileName(self, "Open TTbin", start, "TTbin (*.ttbin)")
        if path:
            self.controller.ui_prefs.default_directories["ttbin"] = str(Path(path).parent)
            self.file_edit.setText(path); self.open_file(path)

    def _labels_from_run(self, path: str) -> None:
        """If the file lives in a run directory, use its measurement.json detector mapping for labels."""
        self.labels, self.wavelengths = {}, {}
        p = Path(path)
        meta_path = p.parent.parent / "measurement.json" if p.parent.name == "raw" else p.parent / "measurement.json"
        candidates = [meta_path, p.with_suffix(".json")]
        for mp in candidates:
            if mp.exists():
                try:
                    meta = load_json(mp)
                    dm = meta.get("experiment", {}).get("detector_map") or meta.get("detector_map")
                    if dm:
                        for ch in DetectorMap.from_dict(dm):
                            self.labels[ch.physical_channel] = ch.label
                            if ch.wavelength_nm is not None:
                                self.wavelengths[ch.physical_channel] = float(ch.wavelength_nm)
                        return
                except Exception:
                    continue

    def open_file(self, path: str) -> None:
        if not path or not Path(path).exists():
            show_error(self, "File not found", path); return
        self.close_session()
        try:
            self.session = self.controller.open_replay(path, int(self.begin.value() * 1e12), int(self.dur.value() * 1e12) if self.dur.value() > 0 else -1)
        except Exception as exc:
            show_error(self, "Could not open TTbin", str(exc), "", "The file must be written by FileWriter; TimeTaggerVirtual needs a valid license (free, acquired once with hardware attached)."); return
        self._labels_from_run(path)
        self.file_info.set_data({"file": path, "channels": self.session.handle.channels_all, "scan": "running in background…"})
        self.status.setText("Opened. Add measurements, then Play.")
        for f in self.forms.values():
            f.refresh_channels()

    def on_replay_event(self, ev: str, payload: dict) -> None:
        if self.session is None or payload.get("session_id") != self.session.session_id:
            return
        if ev == "scanned":
            scan = payload["scan"]
            self.file_info.set_data({"file": self.session.filename, "files": len(scan.files), "channels": scan.channels, "events": scan.n_events, "duration": format_time_ps(scan.duration_ps), "counts per channel": scan.counts_per_channel, "overflow blocks": scan.n_overflow_begin, "missed events": scan.missed_events_total, "config keys": list(scan.configuration.keys())[:8], "marker": scan.last_marker})

    def close_session(self) -> None:
        if self.session is not None:
            self.controller.close_replay(self.session.session_id)
            self.session = None
        self.plot_tabs.clear(); self.panels = {}; self.forms_tabs.clear(); self.forms = {}; self.plugin_target.clear()

    def channel_provider(self) -> list[tuple[int, str]]:
        chans = self.session.handle.channels_all if self.session else []
        return [(c, self.labels.get(c, f"Ch {c}")) for c in chans]

    # ------------------------------------------------------------------ measurements
    def add_measurement(self) -> None:
        if self.session is None:
            show_error(self, "No file", "Open a TTbin first."); return
        tn = self.type_combo.currentData()
        cls = get_measurement_class(tn)
        form = ConfigForm(cls.ConfigClass.FIELDS, self.channel_provider)
        form.set_values(asdict(cls.ConfigClass()))
        key = f"{tn}_{len(self.forms)}"
        self.forms[key] = form
        w = QWidget(); l = QVBoxLayout(w); l.addWidget(form); l.addWidget(HelpPanel(cls.HELP)); self.forms_tabs.addTab(scroll(w), cls.display_name)

    def clear_measurements(self) -> None:
        self.forms_tabs.clear(); self.forms = {}
        if self.session is not None:
            self.session.clear_measurements()
        self.plot_tabs.clear(); self.panels = {}

    def _arm(self) -> bool:
        if self.session is None:
            show_error(self, "No file", "Open a TTbin first."); return False
        if not self.forms:
            show_error(self, "No measurements", "Add at least one measurement to compute."); return False
        self.session.clear_measurements()
        self.plot_tabs.clear(); self.panels = {}; self.plugin_target.clear(); self._prev_snap = {}
        for key, form in self.forms.items():
            tn = key.rsplit("_", 1)[0]
            try:
                m = self.controller.replay_measurement(self.session, tn, form.values(), get_measurement_class(tn).display_name, self.labels, self.wavelengths)
            except Exception as exc:
                show_error(self, "Measurement configuration", str(exc)); return False
            panel = MeasurementPlotPanel(m.plot_specs()); self.panels[m.measurement_id] = panel
            self.plot_tabs.addTab(panel, m.name)
            self.plugin_target.addItem(m.name, m.measurement_id)
        try:
            self.session.arm()
        except Exception as exc:
            show_error(self, "Arming failed", str(exc)); return False
        return True

    # ------------------------------------------------------------------ transport
    def _speed_value(self) -> float:
        return float(self.custom_speed.value()) if self.custom_speed.value() > 0 else float(self.speed.currentData())

    def play(self) -> None:
        if self.session is None:
            return
        if self.session.state in ("idle", "stopped", "finished"):
            if not self._arm():
                return
        note = self.session.play(self._speed_value())
        self.status.setText("Playing" + (f" ({note})" if note else ""))

    def pause(self) -> None:
        if self.session and self.session.state == "playing":
            pos = self.session.pause(); self.status.setText(f"Paused at {format_time_ps(pos)}")
            self.history_slider.setRange(0, max(0, len(next(iter(self.session.history.values()), [])) - 1))
            self.history_slider.setValue(self.history_slider.maximum())

    def resume(self) -> None:
        if self.session and self.session.state == "paused":
            note = self.session.resume(); self.status.setText("Resumed" + (f" ({note})" if note else ""))

    def stop(self) -> None:
        if self.session:
            self.session.stop(); self.status.setText("Stopped (measurements keep their accumulated data)")

    def restart(self) -> None:
        if self.session:
            if self.session.state in ("idle",) and not self._arm():
                return
            note = self.session.restart(self._speed_value()); self.status.setText("Restarted" + (f" ({note})" if note else ""))

    def change_speed(self) -> None:
        if self.session and self.session.state in ("playing", "paused"):
            note = self.session.set_speed(self._speed_value())
            if note:
                self.status.setText(note)

    # ------------------------------------------------------------------ updates
    def on_snapshots(self, snaps: dict) -> None:
        if self.session is None or self.session.session_id not in snaps:
            return
        self._refresh_plots(snaps[self.session.session_id])
        pos = self.session.position_ps(); total = self.session.total_duration_ps
        prog = self.session.progress()
        if prog is not None:
            self.progress.setValue(int(prog * 1000)); self.position.setValue(int(prog * 1000))
        rem = self.session.remaining_ps()
        self.pos_label.setText(f"position: {format_time_ps(pos)} / {format_time_ps(total) if total else '—'}   elapsed {format_time_ps(pos)}   remaining {format_time_ps(rem) if rem is not None else '—'}   speed {self.session.source.speed}x   state {self.session.state}")
        if self.session.state == "finished":
            self.status.setText("Replay finished. Save results or change parameters and restart.")

    def _refresh_plots(self, snaps: Optional[dict] = None) -> None:
        if self.session is None:
            return
        snaps = snaps or self.session.last_snapshots
        mode = self.evolution.currentIndex()
        for mid, snap in snaps.items():
            panel = self.panels.get(mid)
            if panel is None:
                continue
            shown = snap
            if mode == 1:  # accumulate: overlay history for 1-D line/histogram plots
                hist = list(self.session.history.get(mid, []))
                shown = self._overlay(snap, hist[::max(1, len(hist) // 6)])
            elif mode == 3:  # difference
                prev = self._prev_snap.get(mid)
                if prev is not None:
                    shown = self._difference(snap, prev)
                self._prev_snap[mid] = snap
            panel.update_snapshot(shown)
        if snaps:
            first = next(iter(snaps.values()))
            self.scalars.set_data({k: v for k, v in first.scalars.items() if not isinstance(v, dict) or len(v) < 8})

    @staticmethod
    def _overlay(snap: Snapshot, history: list[Snapshot]) -> Snapshot:
        out = Snapshot(snap.wall_time, snap.capture_duration_ps, dict(snap.arrays), dict(snap.scalars), snap.finished, dict(snap.labels))
        for key, arr in snap.arrays.items():
            a = np.asarray(arr)
            if a.ndim == 1 and a.size > 8 and key not in ("index_ps", "time_s", "trace_time_s") and a.dtype.kind in "if":
                rows = [np.asarray(h.arrays.get(key, a), dtype=float) for h in history if h.arrays.get(key) is not None and np.asarray(h.arrays[key]).shape == a.shape]
                if rows:
                    out.arrays[key] = np.vstack(rows + [a.astype(float)])
                    out.labels["channel_labels"] = [f"t={h.scalars.get('replay_position_s', 0):.1f}s" for h in history if h.arrays.get(key) is not None and np.asarray(h.arrays[key]).shape == a.shape] + ["current"]
        return out

    @staticmethod
    def _difference(snap: Snapshot, prev: Snapshot) -> Snapshot:
        out = Snapshot(snap.wall_time, snap.capture_duration_ps, {}, dict(snap.scalars), snap.finished, dict(snap.labels))
        for key, arr in snap.arrays.items():
            a = np.asarray(arr)
            b = prev.arrays.get(key)
            if b is not None and np.asarray(b).shape == a.shape and a.dtype.kind in "if" and key not in ("index_ps", "time_s", "trace_time_s", "signal_wavelengths_nm", "idler_wavelengths_nm", "wavelengths_nm", "channels", "groups", "virtual_channels"):
                out.arrays[key] = a.astype(float) - np.asarray(b, dtype=float)
            else:
                out.arrays[key] = a
        out.scalars["view"] = "difference to previous update"
        return out

    def show_history(self, idx: int) -> None:
        if self.session is None or self.evolution.currentIndex() != 2:
            return
        for mid, panel in self.panels.items():
            hist = list(self.session.history.get(mid, []))
            if 0 <= idx < len(hist):
                panel.update_snapshot(hist[idx])
                self.scalars.set_data({"history index": idx, **{k: v for k, v in hist[idx].scalars.items() if not isinstance(v, dict)}})

    # ------------------------------------------------------------------ results & plugins
    def save_results(self) -> None:
        if self.session is None or not self.session.measurements:
            show_error(self, "Nothing to save", "Run a replay first."); return
        default = str(Path(self.session.filename).parent.parent / "analysis") if Path(self.session.filename).parent.name == "raw" else str(Path(self.session.filename).parent / "analysis")
        directory = QFileDialog.getExistingDirectory(self, "Save analysis results to", default) or default
        try:
            paths = self.session.save_results(directory, "", self.controller.diagnostics()["application"])
        except Exception as exc:
            show_error(self, "Save failed", str(exc)); return
        self.status.setText("Saved: " + ", ".join(p.name for p in paths))

    def _current_result(self) -> Optional[MeasurementResult]:
        mid = self.plugin_target.currentData()
        if mid in self.loaded_results:
            return self.loaded_results[mid]
        if self.session is None:
            return None
        for m in self.session.measurements:
            if m.measurement_id == mid:
                return self.session.build_results()[self.session.measurements.index(m)]
        return None

    def _refresh_plugins(self) -> None:
        res_type = None
        mid = self.plugin_target.currentData()
        if mid in self.loaded_results:
            res_type = self.loaded_results[mid].measurement_type
        elif self.session:
            for m in self.session.measurements:
                if m.measurement_id == mid:
                    res_type = m.type_name
        self.plugin_combo.blockSignals(True); self.plugin_combo.clear()
        for p in list_plugins(res_type):
            self.plugin_combo.addItem(p.display_name, p.name)
        self.plugin_combo.blockSignals(False)
        self._build_plugin_form()

    def _build_plugin_form(self) -> None:
        if self.plugin_form is not None:
            self.plugin_form_host.removeWidget(self.plugin_form); self.plugin_form.deleteLater(); self.plugin_form = None
        name = self.plugin_combo.currentData()
        if not name:
            return
        cls = type(get_plugin(name))
        self.plugin_form = ConfigForm(cls.parameters, self.channel_provider)
        self.plugin_form.set_values(cls.default_parameters())
        self.plugin_form_host.addWidget(self.plugin_form)

    def run_plugin(self) -> None:
        res = self._current_result()
        name = self.plugin_combo.currentData()
        if res is None or not name:
            show_error(self, "No result", "Run a replay (or load a saved result) and pick a plugin."); return
        params = self.plugin_form.values() if self.plugin_form else {}
        try:
            out = self.controller.run_plugin(res, name, params)
        except Exception as exc:
            show_error(self, "Plugin failed", str(exc)); return
        panel = MeasurementPlotPanel(out.plots) if out.plots else None
        if panel is not None:
            snap = Snapshot(0.0, int(res.metadata.get("capture_duration_ps", 0)), out.arrays, out.scalars, True, res.metadata.get("labels", {}))
            panel.update_snapshot(snap)
            self.plot_tabs.addTab(panel, f"{out.plugin}")
            self.plot_tabs.setCurrentWidget(panel)
        text = json.dumps({"scalars": out.scalars, "warnings": out.warnings, "provenance": out.provenance}, indent=2, default=str)
        dlg = TextDialog(f"Analysis output: {name}", text, self)
        dlg.exec()
        if self.session is not None or res.raw_files:
            base = Path(res.raw_files[0]).parent.parent / "analysis" if res.raw_files and Path(res.raw_files[0]).parent.name == "raw" else Path(self.controller.settings.data_directory) / "analysis"
            try:
                p = out.save(base, res); self.status.setText(f"Analysis saved: {p}")
            except Exception as exc:
                self.status.setText(f"Analysis not saved: {exc}")

    def load_result(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load result JSON", self.controller.settings.data_directory, "JSON (*.json)")
        if not path:
            return
        try:
            res = MeasurementResult.load(path)
        except Exception as exc:
            show_error(self, "Load failed", str(exc)); return
        self.loaded_results[res.measurement_id] = res
        self.plugin_target.addItem(f"{res.measurement_type} ({Path(path).name})", res.measurement_id)
        self.plugin_target.setCurrentIndex(self.plugin_target.count() - 1)
        if res.metadata.get("missing_raw_files"):
            self.status.setText(f"⚠ referenced raw TTbin missing: {res.metadata['missing_raw_files']}")
        if res.metadata.get("integrity_warning"):
            self.status.setText(f"⚠ {res.metadata['integrity_warning']}")
