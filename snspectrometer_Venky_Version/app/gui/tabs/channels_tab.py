"""Channels tab: per-channel detector mapping + hardware configuration table + presets."""
from __future__ import annotations

from typing import Any, Optional

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QSlider)

from ...models.channels import DetectorChannel, IMPEDANCE_CHOICES, ROLE_CHOICES
from ...models.presets import PresetMapping
from ...models.units import format_rate
from ..widgets.common import confirm, show_error, TextDialog
from ..widgets.device_selector import DeviceSelector

COLS = ["Device", "Ch", "Name", "Wavelength [nm]", "BW [nm]", "Role", "Enabled", "Trigger [V]", "Impedance", "Delay [ps]", "Deadtime [ps]", "Divider", "Live cps", "Applied", "Status"]


class ChannelsTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.device = DeviceSelector(controller)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(self.on_item_changed)
        self._loading = False
        # trigger slider helper
        trig = QGroupBox("Trigger level (selected channel)"); tl = QHBoxLayout(trig)
        self.slider = QSlider(Qt.Horizontal); self.slider.setRange(-2500, 2500)
        self.trig_spin = QDoubleSpinBox(); self.trig_spin.setDecimals(4); self.trig_spin.setRange(-10, 10); self.trig_spin.setSingleStep(0.005); self.trig_spin.setSuffix(" V")
        self.trig_range = QLabel("range: n/a")
        b_apply_trig = QPushButton("Apply trigger"); b_apply_trig.clicked.connect(self.apply_trigger)
        tl.addWidget(self.slider, 1); tl.addWidget(self.trig_spin); tl.addWidget(self.trig_range); tl.addWidget(b_apply_trig)
        self.slider.valueChanged.connect(lambda v: self.trig_spin.setValue(v / 1000.0))
        self.trig_spin.valueChanged.connect(lambda v: (self.slider.blockSignals(True), self.slider.setValue(int(v * 1000)), self.slider.blockSignals(False)))
        self.table.currentCellChanged.connect(lambda *_: self.sync_trigger_widgets())
        # buttons
        row = QHBoxLayout()
        b_apply = QPushButton("Apply selected rows to hardware"); b_apply.clicked.connect(lambda: self.apply_rows(selected_only=True))
        b_apply_all = QPushButton("Apply all"); b_apply_all.clicked.connect(lambda: self.apply_rows(selected_only=False))
        b_read = QPushButton("Read back from hardware"); b_read.clicked.connect(self.read_back)
        b_test = QPushButton("Toggle test signal"); b_test.clicked.connect(self.toggle_test_signal)
        b_sort = QPushButton("Sort by wavelength"); b_sort.clicked.connect(lambda: self.refresh(sort_wl=True))
        for b in (b_apply, b_apply_all, b_read, b_test, b_sort):
            row.addWidget(b)
        row.addStretch(1)
        # presets
        pbox = QGroupBox("Presets"); pl = QVBoxLayout(pbox)
        self.preset_list = QListWidget()
        pr = QHBoxLayout()
        for text, fn in (("Save current as…", self.preset_save), ("Load / apply", self.preset_apply), ("Compare vs hardware", self.preset_compare), ("Duplicate", self.preset_duplicate), ("Rename", self.preset_rename), ("Delete", self.preset_delete), ("Export…", self.preset_export), ("Import…", self.preset_import)):
            b = QPushButton(text); b.clicked.connect(fn); pr.addWidget(b)
        pl.addWidget(self.preset_list); pl.addLayout(pr)
        self.preset_note = QLabel("Presets store per-device serial + channel settings. Applying to a different device uses an explicit mapping (device serial → connected device, channel → channel)."); self.preset_note.setWordWrap(True)
        pl.addWidget(self.preset_note)
        top = QHBoxLayout(); top.addWidget(QLabel("<b>Device</b>")); top.addWidget(self.device); top.addStretch(1)
        self.status = QLabel(""); self.status.setWordWrap(True)
        main = QWidget(); ml = QVBoxLayout(main); ml.addLayout(top); ml.addWidget(self.table); ml.addWidget(trig); ml.addLayout(row); ml.addWidget(self.status)
        split = QSplitter(Qt.Vertical); split.addWidget(main); split.addWidget(pbox); split.setSizes([650, 200])
        lay = QVBoxLayout(self); lay.addWidget(split)
        self._rates: dict[int, float] = {}
        self._rate_meas = None
        self._rate_dev = None
        self.device.currentIndexChanged.connect(lambda _: self.refresh())
        controller.detector_map_changed.connect(self.refresh)
        controller.hardware_event.connect(lambda ev, p: self.refresh() if ev in ("channel_configured", "connected", "disconnected") else None)
        controller.health.connect(self.on_health)
        self.refresh()
        self.refresh_presets()

    # ------------------------------------------------------------------ table
    def _dev(self) -> Optional[str]:
        return self.device.device_id()

    def refresh(self, sort_wl: bool = False) -> None:
        dev = self._dev()
        self._loading = True
        dm = self.controller.detector_map()
        chans = dm.sorted_by_wavelength(dev, enabled_only=False) if (sort_wl and dev) else (dm.by_device(dev) if dev else [])
        states = self.controller.channel_state_dicts(dev) if dev else {}
        caps = self.controller.handle(dev).capabilities if dev else {}
        self.table.setRowCount(len(chans))
        for i, dc in enumerate(chans):
            st = states.get(dc.physical_channel, {})
            self._set(i, 0, dc.tagger_id, editable=False); self._set(i, 1, str(dc.physical_channel), editable=False)
            self._set(i, 2, dc.detector_name); self._set(i, 3, "" if dc.wavelength_nm is None else f"{dc.wavelength_nm:g}"); self._set(i, 4, "" if dc.wavelength_bandwidth_nm is None else f"{dc.wavelength_bandwidth_nm:g}")
            self._combo(i, 5, ROLE_CHOICES, dc.role)
            self._check(i, 6, dc.detector_enabled)
            self._set(i, 7, "" if dc.trigger_level_v is None else f"{dc.trigger_level_v:.4f}", editable=bool(caps.get("trigger_level")))
            self._combo(i, 8, ("",) + IMPEDANCE_CHOICES, dc.impedance or "", enabled=bool(caps.get("impedance")))
            self._set(i, 9, str(dc.input_delay_ps)); self._set(i, 10, "" if dc.deadtime_ps is None else str(dc.deadtime_ps)); self._set(i, 11, str(dc.event_divider), editable=bool(caps.get("event_divider")))
            r = self._rates.get(dc.physical_channel) if dev == self._rate_dev else None
            self._set(i, 12, format_rate(r) if r is not None else "—", editable=False)
            applied = []
            if st.get("trigger_level_v") is not None:
                applied.append(f"trig {st['trigger_level_v']:.4f} V")
            if st.get("impedance"):
                applied.append({"50_OHM": "50 Ω", "HIGH_Z": "High-Z"}[st["impedance"]])
            if st.get("input_delay_ps") is not None:
                applied.append(f"delay {st['input_delay_ps']} ps")
            if st.get("deadtime_ps") is not None:
                applied.append(f"dead {st['deadtime_ps']} ps")
            if st.get("event_divider") is not None:
                applied.append(f"div {st['event_divider']}")
            if st.get("hardware_delay_compensation_ps") is not None:
                applied.append(f"hwcomp {st['hardware_delay_compensation_ps']} ps")
            if st.get("test_signal"):
                applied.append("TEST SIGNAL ON")
            self._set(i, 13, ", ".join(applied), editable=False)
            unsupported = st.get("unsupported", [])
            errs = st.get("errors", {})
            self._set(i, 14, ("unsupported: " + ",".join(unsupported) if unsupported else "OK") + ("; errors: " + "; ".join(f"{k}: {v}" for k, v in errs.items()) if errs else ""), editable=False)
        self._loading = False
        self.sync_trigger_widgets()

    def _set(self, r: int, c: int, text: str, editable: bool = True) -> None:
        it = QTableWidgetItem(text)
        if not editable:
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, c, it)

    def _combo(self, r: int, c: int, choices, value, enabled: bool = True) -> None:
        cb = QComboBox(); cb.addItems([str(x) for x in choices]); cb.setCurrentText(str(value)); cb.setEnabled(enabled)
        cb.currentTextChanged.connect(lambda _t, r=r: self._row_to_model(r))
        self.table.setCellWidget(r, c, cb)

    def _check(self, r: int, c: int, value: bool) -> None:
        cb = QCheckBox(); cb.setChecked(bool(value)); cb.toggled.connect(lambda _v, r=r: self._row_to_model(r))
        self.table.setCellWidget(r, c, cb)

    def _row_channel(self, r: int) -> Optional[DetectorChannel]:
        dev = self.table.item(r, 0).text(); ch = int(self.table.item(r, 1).text())
        return self.controller.detector_map().get_physical(dev, ch)

    def _row_to_model(self, r: int) -> Optional[DetectorChannel]:
        if self._loading:
            return None
        dc = self._row_channel(r)
        if dc is None:
            return None

        def num(c, cast, default=None):
            t = self.table.item(r, c).text().strip()
            if not t:
                return default
            try:
                return cast(t)
            except ValueError:
                return default

        dc.detector_name = self.table.item(r, 2).text().strip()
        dc.wavelength_nm = num(3, float); dc.wavelength_bandwidth_nm = num(4, float)
        dc.role = self.table.cellWidget(r, 5).currentText(); dc.detector_enabled = self.table.cellWidget(r, 6).isChecked()
        dc.trigger_level_v = num(7, float); imp = self.table.cellWidget(r, 8).currentText(); dc.impedance = imp or None
        dc.input_delay_ps = num(9, int, 0) or 0; dc.deadtime_ps = num(10, int); dc.event_divider = num(11, int, 1) or 1
        problems = dc.validate()
        self.status.setText("; ".join(problems) if problems else "")
        return dc

    def on_item_changed(self, item: QTableWidgetItem) -> None:
        if not self._loading:
            self._row_to_model(item.row())

    def sync_trigger_widgets(self) -> None:
        r = self.table.currentRow()
        dev = self._dev()
        if r < 0 or dev is None:
            return
        dc = self._row_channel(r)
        if dc is None:
            return
        st = self.controller.channel_state_dicts(dev).get(dc.physical_channel, {})
        rng = st.get("trigger_level_range_v")
        if rng:
            self.slider.setRange(int(rng[0] * 1000), int(rng[1] * 1000)); self.trig_spin.setRange(rng[0], rng[1]); self.trig_range.setText(f"range: {rng[0]:.3f} … {rng[1]:.3f} V")
        else:
            self.trig_range.setText("range: n/a")
        v = dc.trigger_level_v if dc.trigger_level_v is not None else st.get("trigger_level_v")
        if v is not None:
            self.trig_spin.setValue(float(v))

    # ------------------------------------------------------------------ hardware actions
    def _confirm_active(self, dev: str) -> bool:
        if self.controller.is_acquiring(dev):
            return confirm(self, "Acquisition active", "An acquisition is running on this device. Changing hardware settings now affects the running measurement. Apply anyway?", dangerous=True)
        return True

    def apply_rows(self, selected_only: bool) -> None:
        dev = self._dev()
        if dev is None:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}) if selected_only else list(range(self.table.rowCount()))
        if not rows:
            show_error(self, "No rows selected", "Select one or more channel rows."); return
        if not self._confirm_active(dev):
            return
        msgs = []
        for r in rows:
            dc = self._row_to_model(r) or self._row_channel(r)
            if dc is None:
                continue
            try:
                changes = self.controller.apply_channel(dc, force=True)
                for ch in changes:
                    msgs.append(f"ch {ch.channel} {ch.field}: {'OK -> ' + str(ch.applied) if ch.ok else 'FAILED: ' + ch.error}")
            except Exception as exc:
                msgs.append(f"ch {dc.physical_channel}: {exc}")
        self.status.setText("\n".join(msgs[-12:]))
        self.refresh()

    def apply_trigger(self) -> None:
        r = self.table.currentRow(); dev = self._dev()
        if r < 0 or dev is None:
            return
        dc = self._row_channel(r)
        if dc is None or not self._confirm_active(dev):
            return
        dc.trigger_level_v = float(self.trig_spin.value())
        try:
            changes = self.controller.apply_channel(dc, {"trigger_level_v"}, force=True)
            self.status.setText("; ".join(f"{c.field}: {'OK -> ' + str(c.applied) if c.ok else 'FAILED: ' + c.error}" for c in changes))
        except Exception as exc:
            show_error(self, "Trigger level", str(exc))
        self.refresh()

    def read_back(self) -> None:
        dev = self._dev()
        if dev:
            self.controller.read_channel_states(dev)
            self.refresh()

    def toggle_test_signal(self) -> None:
        r = self.table.currentRow(); dev = self._dev()
        if r < 0 or dev is None:
            return
        dc = self._row_channel(r)
        st = self.controller.channel_state_dicts(dev).get(dc.physical_channel, {})
        cur = bool(st.get("test_signal"))
        if not confirm(self, "Test signal", f"{'Disable' if cur else 'Enable'} the internal test signal on channel {dc.physical_channel}? (Not available on virtual devices.)"):
            return
        try:
            ch = self.controller.hardware.set_test_signal(dev, dc.physical_channel, not cur)
            self.status.setText("; ".join(f"{c.field}: {'OK' if c.ok else c.error}" for c in ch))
        except Exception as exc:
            show_error(self, "Test signal", str(exc))
        self.refresh()

    def on_health(self, _health: dict) -> None:
        dev = self._dev()
        if dev is None:
            return
        if self._rate_dev != dev or self._rate_meas is None:
            try:
                from swabian_backend import measurements as M
                h = self.controller.handle(dev)
                chans = self.controller.detector_map().physical_channels(dev, enabled_only=False) or list(h.channels_rising)
                self._rate_meas = (M.make_countrate(h.tagger, chans), chans) if chans else None
                self._rate_dev = dev
            except Exception:
                self._rate_meas = None
        if self._rate_meas:
            try:
                rates = self._rate_meas[0].getData()
                self._rates = {ch: float(r) for ch, r in zip(self._rate_meas[1], rates)}
                self._rate_meas[0].clear()
                for i in range(self.table.rowCount()):
                    ch = int(self.table.item(i, 1).text())
                    r = self._rates.get(ch)
                    self.table.item(i, 12).setText(format_rate(r) if r is not None else "—")
            except Exception:
                pass

    # ------------------------------------------------------------------ presets
    def refresh_presets(self) -> None:
        self.preset_list.clear()
        for n in self.controller.presets.list():
            self.preset_list.addItem(n)

    def _preset_name(self) -> Optional[str]:
        it = self.preset_list.currentItem()
        return it.text() if it else None

    def preset_save(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if ok and name:
            self.controller.presets.save(self.controller.preset_from_current(name))
            self.refresh_presets()

    def _mapping_dialog(self, preset) -> Optional[PresetMapping]:
        mapping = self.controller.default_mapping(preset)
        devs = self.controller.device_ids()
        if not devs:
            show_error(self, "No device", "Connect a device before applying a preset."); return None
        for dp in preset.devices:
            target = mapping.device_map.get(dp.serial)
            choice, ok = QInputDialog.getItem(self, "Preset device mapping", f"Preset device serial {dp.serial} ({dp.model}) → connected device:", devs, devs.index(target) if target in devs else 0, False)
            if not ok:
                return None
            mapping.device_map[dp.serial] = choice
        return mapping

    def preset_apply(self) -> None:
        name = self._preset_name()
        if not name:
            return
        preset = self.controller.presets.load(name)
        mapping = self._mapping_dialog(preset)
        if mapping is None:
            return
        push = confirm(self, "Apply preset", "Also push the hardware settings (trigger, impedance, delay, dead time, divider) to the device(s) now?")
        for dev in set(mapping.device_map.values()):
            if push and not self._confirm_active(dev):
                return
        changed, warnings = self.controller.apply_preset(preset, mapping, push, force=True)
        self.status.setText(f"Preset '{name}': {len(changed)} channels updated" + ("; warnings: " + "; ".join(warnings) if warnings else ""))
        self.refresh()

    def preset_compare(self) -> None:
        name = self._preset_name()
        if not name:
            return
        preset = self.controller.presets.load(name)
        mapping = self._mapping_dialog(preset)
        if mapping is None:
            return
        diffs = self.controller.compare_preset(preset, mapping)
        text = "\n".join(f"{d.device_id} ch {d.channel} {d.field}: preset {d.preset_value}  |  hardware {d.current_value}" for d in diffs) or "No differences between preset and read-back hardware state."
        TextDialog(f"Preset '{name}' vs hardware", text, self).exec()

    def preset_duplicate(self) -> None:
        name = self._preset_name()
        if name:
            self.controller.presets.duplicate(name); self.refresh_presets()

    def preset_rename(self) -> None:
        name = self._preset_name()
        if not name:
            return
        new, ok = QInputDialog.getText(self, "Rename preset", "New name:", text=name)
        if ok and new:
            self.controller.presets.rename(name, new); self.refresh_presets()

    def preset_delete(self) -> None:
        name = self._preset_name()
        if name and confirm(self, "Delete preset", f"Delete preset '{name}'?", dangerous=True):
            self.controller.presets.delete(name); self.refresh_presets()

    def preset_export(self) -> None:
        name = self._preset_name()
        if not name:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export preset", f"{name}.json", "JSON (*.json)")
        if path:
            self.controller.presets.export(name, path)

    def preset_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import preset", "", "JSON (*.json)")
        if path:
            try:
                self.controller.presets.import_file(path); self.refresh_presets()
            except Exception as exc:
                show_error(self, "Import failed", str(exc))
