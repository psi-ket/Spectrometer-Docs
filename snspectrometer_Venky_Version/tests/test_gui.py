"""GUI tests (offscreen): tab manager, config form, settings/preset stores, sequence runner."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

qtpy = pytest.importorskip("qtpy")
try:
    from app import qtbootstrap  # noqa: F401
    from qtpy.QtWidgets import QApplication, QTabWidget, QLabel
except Exception as exc:  # pragma: no cover
    pytest.skip(f"no Qt binding: {exc}", allow_module_level=True)

from app.gui.tab_manager import TabManager
from app.gui.widgets.config_form import ConfigForm
from app.measurements import get_measurement_class
from app.models.presets import Preset, DevicePreset, ChannelPreset
from app.storage.preset_store import PresetStore
from app.storage.settings_store import AppSettings, SettingsStore, UIPreferences
from app.utilities.sequence_runner import Sequence, SequenceRunner, SequenceStep


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_tab_manager_show_hide_reorder_reset(qapp):
    prefs = UIPreferences()
    tabs = QTabWidget()
    tm = TabManager(tabs, prefs, lambda: None)
    for tid in ("a", "b", "c"):
        tm.register(tid, tid.upper(), lambda t=tid: QLabel(t))
    tm.rebuild()
    assert tabs.count() == 3
    tm.hide("b")
    assert tabs.count() == 2 and "b" in prefs.hidden_tabs
    tm.apply_layout(["c", "a", "b"], ["b"])
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["C", "A"]
    tm.restore_hidden()
    assert tabs.count() == 3
    tm.apply_mode("BASIC")
    assert tabs.count() == 0  # none of a/b/c are basic tabs
    tm.reset_layout()
    assert tabs.count() == 3 and [tabs.tabText(i) for i in range(3)] == ["A", "B", "C"]


def test_config_form_roundtrip(qapp):
    cls = get_measurement_class("g2")
    form = ConfigForm(cls.ConfigClass.FIELDS, lambda: [(1, "A"), (2, "B")])
    form.set_values({"channel_1": 2, "channel_2": None, "binwidth_ps": 25, "n_bins": 123, "background_mode": "sideband_mean", "background_window_ps": 300})
    v = form.values()
    assert v["channel_1"] == 2 and v["channel_2"] is None and v["binwidth_ps"] == 25 and v["n_bins"] == 123 and v["background_mode"] == "sideband_mean"
    cfg = cls.ConfigClass.from_dict(v)
    assert cfg.channels_used() == [2]
    form2 = ConfigForm(get_measurement_class("coincidences").ConfigClass.FIELDS, lambda: [(1, "A"), (2, "B"), (3, "C")])
    form2.set_values({"channels": [1, 3]})
    assert form2.values()["channels"] == [1, 3]


def test_settings_and_ui_prefs_are_separate(tmp_path):
    store = SettingsStore(tmp_path)
    s = AppSettings(data_directory=str(tmp_path / "d"), plot_refresh_hz=10)
    store.save_settings(s)
    u = UIPreferences(hidden_tabs=["jsi"]); u.add_recent("file", "a.ttbin"); u.add_recent("file", "b.ttbin"); u.add_recent("file", "a.ttbin")
    store.save_ui(u)
    assert store.load_settings().plot_refresh_hz == 10
    assert store.load_ui().recent_files == ["a.ttbin", "b.ttbin"] and store.load_ui().hidden_tabs == ["jsi"]
    assert store.settings_path != store.ui_path
    assert AppSettings(raw_recording_policy="bad").validate()


def test_preset_store_crud(tmp_path):
    ps = PresetStore(tmp_path)
    p = Preset("Default", devices=[DevicePreset("S1", "Ultra", {"1": ChannelPreset("D1", 800.0)})])
    ps.save(p)
    assert ps.list() == ["Default"]
    ps.duplicate("Default", "Copy"); ps.rename("Copy", "Renamed")
    assert sorted(ps.list()) == ["Default", "Renamed"]
    exp = ps.export("Renamed", tmp_path / "exp.json")
    ps.delete("Renamed")
    ps.import_file(exp, "Imported")
    assert ps.load("Imported").devices[0].channels["1"].wavelength_nm == 800.0


def test_sequence_runner_skip_and_conditions():
    events = []
    calls = []

    def h_ok(step, runner):
        calls.append(step.name); return {"ok": True}

    def h_slow(step, runner):
        t0 = time.time()
        while time.time() - t0 < 2 and not runner.skip_requested:
            time.sleep(0.02)
        calls.append(step.name); return {}

    runner = SequenceRunner({"a": h_ok, "slow": h_slow}, lambda cond, step: (cond != "fail", "forced"), lambda e, p: events.append(e))
    seq = Sequence("s", [SequenceStep("one", "a"), SequenceStep("two", "slow"), SequenceStep("three", "a", preconditions=["fail"]), SequenceStep("four", "a")])
    runner.run(seq)
    time.sleep(0.3); runner.skip()
    t0 = time.time()
    while runner.state == "running" and time.time() - t0 < 5:
        time.sleep(0.05)
    assert calls == ["one", "two"]
    assert seq.steps[1].status == "skipped" and seq.steps[2].status == "blocked" and seq.steps[3].status == "pending"
    assert "finished" in events
