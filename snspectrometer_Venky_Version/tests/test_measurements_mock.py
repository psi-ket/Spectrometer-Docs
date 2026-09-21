"""Hardware-abstraction tests with the mock backend (no library, no hardware)."""
from __future__ import annotations

import time

import numpy as np
import pytest

from swabian_backend import devices as D, configuration as C, measurements as M, virtual_channels as V, replay as RP
from app.hardware.manager import HardwareManager
from app.measurements import create_measurement, list_measurement_types, get_measurement_class
from app.measurements.base import ValidationContext
from app.measurements.group import MeasurementGroup, RawRecordingConfig
from app.measurements.manager import MeasurementManager
from app.models.channels import DetectorChannel, DetectorMap
from app.models.experiment import ExperimentConfig
from app.models.sync_state import SyncState, RunStatus
from app.storage.run_store import RunDirectory, find_interrupted_runs
from app.synchronization.sync_manager import SyncManager


def test_discovery_and_connection(mock_api):
    devs = D.scan_local_devices()
    assert devs and devs[0].serial == "MOCK-0001" and "mock" in devs[0].model
    hw = HardwareManager()
    info = hw.discover()
    assert info["local"][0]["serial"] == "MOCK-0001"
    rec = hw.connect_local("MOCK-0001")
    h = rec.handle
    assert h.channels_rising == list(range(1, 9)) and h.connection_type == D.ConnectionType.LOCAL_USB
    assert h.capabilities["trigger_level"] and h.capabilities["impedance"] and h.capabilities["event_divider"]
    st = rec.channel_states[1]
    assert st.trigger_level_v == 0.5 and st.trigger_level_range_v == (-2.5, 2.5)
    hw.disconnect(h.id)
    assert not hw.devices


def test_channel_configuration_readback_and_validation(mock_api):
    h = D.open_local("MOCK-0001")
    st, changes = C.apply_channel_settings(h, 2, trigger_level_v=0.0512, impedance="HIGH_Z", input_delay_ps=-250, deadtime_ps=5000, event_divider=4)
    assert all(c.ok for c in changes)
    assert abs(st.trigger_level_v - 0.05) < 1e-9  # DAC discretisation in the mock
    assert st.impedance == "HIGH_Z" and st.input_delay_ps == -250 and st.deadtime_ps == 5000 and st.event_divider == 4
    st, changes = C.apply_channel_settings(h, 2, trigger_level_v=9.0, event_divider=70000, deadtime_ps=-1)
    assert all(not c.ok for c in changes)
    h.close()


def test_virtual_tagger_has_no_event_divider(mock_api):
    h = D.open_virtual("x.ttbin")
    assert h.is_virtual and not h.capabilities["event_divider"] and not h.capabilities["trigger_level"]
    st, changes = C.apply_channel_settings(h, 1, event_divider=2)
    assert changes and not changes[0].ok and "unsupported" in changes[0].error
    h.close()


def test_network_multi_server_channel_map_and_sync_state(mock_api):
    h = D.open_network(["a:41101", "b:41101"])
    assert h.connection_type == D.ConnectionType.NETWORK and len(h.sub_devices) == 2
    assert h.sub_devices[1].channel_map[3] == 1003 and h.source_index(1003) == 1
    state, reason = SyncManager().compute_state(h)
    assert state == SyncState.SYNC_REQUIRED
    ctx = MeasurementManager.validation_context(h, state)
    assert ctx.cross_device([1, 1003]) and not ctx.sync_allows_cross_device
    cfg = get_measurement_class("g2").ConfigClass(channel_1=1, channel_2=1003, binwidth_ps=10, n_bins=100)
    assert any("synchronized" in p for p in cfg.validate(ctx))
    h.close()


def test_measurement_configs_validate(mock_api):
    ctx = ValidationContext(available_channels=[1, 2, 3, 4], library_capabilities={"CorrelationPairs": True})
    for cls in list_measurement_types():
        cfg = cls.ConfigClass()
        assert isinstance(cfg.validate(ctx), list)
    bad = get_measurement_class("histogram").ConfigClass(click_channel=9, binwidth_ps=0)
    p = bad.validate(ctx)
    assert any("not available" in x for x in p) and any(">=" in x for x in p)
    coinc = get_measurement_class("coincidences").ConfigClass(channels=[1, 2, 3, 4], order=2, max_groups=3)
    assert any("exceed" in x for x in coinc.validate(ctx))
    coinc.max_groups = 10
    assert len(coinc.planned_groups()) == 6
    assert V.count_coincidence_groups(8, 3) == 56 and V.count_combinations(10) == 1023
    assert V.coincidence_groups([1, 2, 3, 4], 2, include=[1]) == [(1, 2), (1, 3), (1, 4)]
    assert V.coincidence_groups([1, 2, 3, 4], 2, exclude=[4]) == [(1, 2), (1, 3), (2, 3)]


def test_gated_counter_adapter_fallback(mock_api):
    h = D.open_local("MOCK-0001")
    g = M.make_gated_counter(h.tagger, [1, 2], 3, None, 10)
    assert g.impl_name == "CountBetweenMarkers" and g.getData().shape == (2, 10)
    h.close()


def test_group_lifecycle_saves_results_and_metadata(mock_api, tmp_path):
    hw = HardwareManager()
    rec = hw.connect_local("MOCK-0001")
    exp = ExperimentConfig(experiment_name="mock run")
    exp.detector_map.ensure_device_channels(rec.handle.id, rec.handle.channels_rising)
    exp.detector_map.get_physical(rec.handle.id, 1).wavelength_nm = 800.0
    mgr = MeasurementManager("test")
    events = []
    mgr.listeners.append(lambda ev, g: events.append(ev))
    meas = [mgr.build_measurement(rec.handle, exp.detector_map, "countrate", {"channels": [1, 2]}, "rates"),
            mgr.build_measurement(rec.handle, exp.detector_map, "g2", {"channel_1": 1, "channel_2": 2, "binwidth_ps": 100, "n_bins": 200}, "g2"),
            mgr.build_measurement(rec.handle, exp.detector_map, "coincidences", {"channels": [1, 2, 3], "order": 2, "window_ps": 1000}, "coinc"),
            mgr.build_measurement(rec.handle, exp.detector_map, "coincidence_matrix", {"channels": [1, 2, 3], "binwidth_ps": 100, "n_bins": 50, "window_ps": 500}, "matrix"),
            mgr.build_measurement(rec.handle, exp.detector_map, "jsi", {"signal_channels": [1], "idler_channels": [2, 3], "binwidth_ps": 100, "n_bins": 50, "window_ps": 500}, "jsi")]
    group = mgr.create_group("run1", rec.handle, meas, exp, SyncState.SYNCED, 0.3, RawRecordingConfig(enabled=True, max_file_size_bytes=10_000), str(tmp_path))
    items = group.preflight()
    assert group.preflight_ok, [i.label for i in items if not i.ok]
    group.prepare(); group.arm(); group.start()
    assert group.status == RunStatus.RUNNING and group.run_dir.status_path.exists()
    t0 = time.time()
    while group.status.is_active and time.time() - t0 < 5:
        group.poll(); time.sleep(0.05)
    assert group.status == RunStatus.COMPLETED, group.status_reason
    assert "finished" in events
    meta = group.run_dir.read_metadata()
    assert meta["status"] == "COMPLETED" and len(meta["measurements"]) == 5 and meta["raw_recording"]["enabled"]
    assert meta["experiment"]["detector_map"]["channels"][0]["wavelength_nm"] == 800.0
    assert meta["raw_files"] and meta["tagger_configuration"]["mock"] is True
    assert len(group.run_dir.result_files()) == 5
    g2 = group.results[meas[1].measurement_id]
    assert "g2_app" in g2.arrays and g2.scalars["normalization_equation"]
    jsi = group.results[meas[4].measurement_id]
    assert jsi.arrays["jsi_raw"].shape == (1, 2)
    # pause/resume path on a second group
    g2b = mgr.create_group("run2", rec.handle, [mgr.build_measurement(rec.handle, exp.detector_map, "counter", {"channels": [1], "binwidth_ms": 10, "n_values": 10}, "c")], exp, SyncState.SYNCED, None, RawRecordingConfig(), str(tmp_path))
    g2b.prepare(); g2b.arm(); g2b.start(); g2b.pause()
    assert g2b.status == RunStatus.PAUSED
    g2b.resume(); assert g2b.status == RunStatus.RUNNING
    g2b.stop("test")
    assert g2b.status == RunStatus.COMPLETED and g2b.status_reason == "test"
    hw.disconnect_all()


def test_preflight_blocks_bad_config(mock_api, tmp_path):
    h = D.open_local("MOCK-0001")
    m = create_measurement("histogram", {"click_channel": None, "binwidth_ps": 10, "n_bins": 10})
    g = MeasurementGroup("bad", h, [m], 1.0)
    g.preflight()
    assert not g.preflight_ok
    with pytest.raises(RuntimeError):
        g.prepare()
    h.close()


def test_interrupted_run_detection(tmp_path):
    rd = RunDirectory.create(tmp_path, "EXP", "run")
    rd.write_status("RUNNING", "", 0.5, {"group_id": "x"})
    rd.write_metadata({"status": "RUNNING"})
    # fake a dead pid
    import json
    st = json.loads(rd.status_path.read_text()); st["pid"] = 999_999_999; rd.status_path.write_text(json.dumps(st))
    found = find_interrupted_runs(tmp_path, mark=True)
    assert len(found) == 1
    assert rd.read_status()["status"] == "INTERRUPTED" and rd.read_metadata()["status"] == "INTERRUPTED"


def test_merge_and_recorder_wrappers(mock_api, tmp_path):
    h = D.open_local("MOCK-0001")
    from swabian_backend.recorder import Recorder, ttbin_sequence_files
    r = Recorder(h.tagger, str(tmp_path / "rec"), [1, 2], 1000)
    assert r.filename.endswith("rec.ttbin") and r.total_events() == 42
    (tmp_path / "rec.1.ttbin").write_bytes(b"x"); (tmp_path / "rec.2.ttbin").write_bytes(b"x")
    assert [p.split("\\")[-1].split("/")[-1] for p in ttbin_sequence_files(r.filename)] == ["rec.ttbin", "rec.1.ttbin", "rec.2.ttbin"]
    out = RP.merge_files(str(tmp_path / "merged"), [r.filename], [0], [0], False)
    assert out.endswith("merged.ttbin")
    with pytest.raises(ValueError):
        RP.merge_files(str(tmp_path / "m2"), [r.filename], [0, 1], [0], False)
    assert RP.normalise_speed(0.01) == (0.1, RP.normalise_speed(0.01)[1]) and RP.normalise_speed(-3)[0] == -1.0
    h.close()
