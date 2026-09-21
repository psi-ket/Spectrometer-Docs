"""Headless acquisition example (no GUI): connect, configure, run a synchronized group, save.

    python examples/headless_acquisition.py --serial ""              # first USB Time Tagger
    python examples/headless_acquisition.py --simulator              # synthetic data through the real engine
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.hardware.manager import HardwareManager
from app.measurements.group import RawRecordingConfig
from app.measurements.manager import MeasurementManager
from app.models.experiment import ExperimentConfig
from app.models.sync_state import SyncState
from app.synchronization.sync_manager import SyncManager
from app.utilities.logging_setup import setup_logging


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default="")
    ap.add_argument("--simulator", action="store_true")
    ap.add_argument("--duration", type=float, default=5.0)
    ap.add_argument("--out", default=str(Path.home() / "SNSPD_Data"))
    args = ap.parse_args()
    setup_logging(level="INFO")
    hw = HardwareManager()
    if args.simulator:
        from simulator.demo_dataset import scenario_two_detector, generate_dataset
        from simulator.device import SimulatedDevice
        demo = Path(args.out) / "demo"
        f = demo / "two_detector_pairs.ttbin"
        if not f.exists():
            generate_dataset(scenario_two_detector(6.0), demo)
        dev = SimulatedDevice(str(f)); dev.start()
        rec = hw.register_handle(dev.handle)
    else:
        hw.discover()
        rec = hw.connect_local(args.serial)
    h = rec.handle
    print("device:", h.describe())
    exp = ExperimentConfig(experiment_name="headless example")
    exp.detector_map.ensure_device_channels(h.id, h.channels_rising)
    for ch, wl in zip(h.channels_rising[:2], (810.0, 810.0)):
        dc = exp.detector_map.get_physical(h.id, ch); dc.wavelength_nm = wl; dc.trigger_level_v = 0.05
        if not h.is_virtual:
            hw.apply_detector_channel(dc)
    sync_state = SyncManager().compute_state(h)[0]
    mgr = MeasurementManager("example")
    a, b = h.channels_rising[0], h.channels_rising[1]
    meas = [mgr.build_measurement(h, exp.detector_map, "countrate", {"channels": [a, b]}, "rates"),
            mgr.build_measurement(h, exp.detector_map, "g2", {"channel_1": b, "channel_2": a, "binwidth_ps": 50, "n_bins": 1000}, "g2")]
    group = mgr.create_group("example", h, meas, exp, sync_state, args.duration, RawRecordingConfig(enabled=True), args.out)
    for it in group.preflight():
        print(it.label)
    group.prepare(); group.arm(); group.start()
    while group.status.is_active:
        group.poll(); time.sleep(0.2)
        snap = meas[1].last_snapshot
        if snap:
            print(f"\r{group.elapsed_s():5.1f} s  g2 peak {snap.scalars['peak_position_ps']} ps  N1={snap.scalars['N1']} N2={snap.scalars['N2']}", end="")
    print("\nstatus:", group.status.value, group.status_reason)
    print("run directory:", group.run_dir.path)
    hw.disconnect_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
