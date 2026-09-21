"""Integration tests that need the real Swabian library (virtual tagger, no hardware).

Skipped automatically when the library is not installed.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from swabian_backend import devices as D, replay as RP
from app.analysis.replay_session import ReplaySession
from app.measurements import create_measurement
from app.measurements.group import RawRecordingConfig
from app.measurements.manager import MeasurementManager
from app.models.channels import DetectorMap
from app.models.experiment import ExperimentConfig
from app.models.sync_state import SyncState, RunStatus
from app.utilities.delay_calibration import DelayCalibration
from app.utilities.ttbin_combiner import CombinerJob


def test_scan_and_replay_roundtrip(real_api, demo_ttbin):
    scan = RP.scan_file(demo_ttbin["file"])
    assert scan.n_events > 10_000 and set(scan.channels) >= {1, 2, 3, 4}
    assert 1.5e12 < scan.duration_ps < 2.5e12
    s = ReplaySession(demo_ttbin["file"])
    s.scan_in_background()
    s.add_measurement(create_measurement("g2", {"channel_1": 4, "channel_2": 1, "binwidth_ps": 50, "n_bins": 400}))
    s.add_measurement(create_measurement("countrate", {"channels": [1, 2, 3, 4]}))
    s.arm(); s.play(-1)
    t0 = time.time()
    while not s.is_finished() and time.time() - t0 < 30:
        s.poll(); time.sleep(0.05)
    assert s.state == "finished"
    res = s.build_results("SRC", "test")
    g2 = res[0]
    assert g2.scalars["peak_position_ps"] == 2200  # baked-in delay of channel 4 vs channel 1
    assert g2.provenance["analysis_type"] == "replay:g2" and g2.raw_files[0] == demo_ttbin["file"]
    assert np.nanmax(np.abs(g2.arrays["g2_app"] - g2.arrays["g2_library"])) < 1e-6
    s.close()


def test_pause_resume_replay(real_api, demo_ttbin):
    s = ReplaySession(demo_ttbin["file"])
    s.add_measurement(create_measurement("countrate", {"channels": [1]}))
    s.arm(); s.play(5.0)
    time.sleep(0.3)
    pos = s.pause()
    assert s.state == "paused" and pos > 0
    s.resume()
    t0 = time.time()
    while not s.is_finished() and time.time() - t0 < 30:
        time.sleep(0.05)
    assert s.state == "finished"
    s.close()


def test_group_on_simulated_device(real_api, demo_ttbin, tmp_path):
    from simulator.device import SimulatedDevice
    dev = SimulatedDevice(demo_ttbin["file"]); dev.start()
    try:
        h = dev.handle
        exp = ExperimentConfig(experiment_name="sim")
        dm = DetectorMap.from_dict(demo_ttbin["detector_map"])
        for c in dm:
            c.tagger_id = h.id
        exp.detector_map = DetectorMap(list(dm))
        mgr = MeasurementManager("test")
        meas = [mgr.build_measurement(h, exp.detector_map, "coincidence_matrix", {"channels": [1, 2, 3, 4], "binwidth_ps": 100, "n_bins": 200, "window_ps": 6000}, "m"),
                mgr.build_measurement(h, exp.detector_map, "gated_counter", {"click_channels": [1], "begin_channel": 8, "n_values": 20}, "g")]
        group = mgr.create_group("simrun", h, meas, exp, SyncState.SYNCED, 1.0, RawRecordingConfig(enabled=True), str(tmp_path))
        group.preflight(); group.prepare(); group.arm(); group.start()
        t0 = time.time()
        while group.status.is_active and time.time() - t0 < 15:
            group.poll(); time.sleep(0.1)
        assert group.status == RunStatus.COMPLETED
        m = group.results[meas[0].measurement_id].arrays["matrix_counts"]
        assert m[0, 3] > 10 * m[0, 1] and abs(m[0, 3] - m[3, 0]) < 5  # strong (1,4) pair, symmetric
        assert group.run_dir.raw_files()
        meta = group.run_dir.read_metadata()
        assert meta["measurements"][1]["configuration"]["implementation_notes"]
    finally:
        dev.close()


def test_delay_calibration_zeroes_residuals(real_api, demo_ttbin):
    from simulator.device import SimulatedDevice
    dev = SimulatedDevice(demo_ttbin["file"]); dev.start()
    try:
        dc = DelayCalibration(dev.handle, 1, [4], binwidth_ps=10, n_bins=2000, duration_s=1.0, window_ps=600)
        res = dc.measure()
        assert res[4].significant and abs(res[4].delay_ps + 2200) < 60
        dc.apply({4: True})
        resid = dc.verify()
        assert abs(resid[4]) < 60
    finally:
        dev.close()


def test_combiner_merges_with_channel_offsets(real_api, demo_ttbin, tmp_path):
    job = CombinerJob(output=str(tmp_path / "merged.ttbin"))
    a = job.add(demo_ttbin["file"]); b = job.add(demo_ttbin["file"])
    a.preview(); b.preview(); b.channel_offset = 100
    assert not job.validate()
    out = job.run()
    scan = RP.scan_file(out)
    assert 101 in scan.counts_per_channel and 1 in scan.counts_per_channel
