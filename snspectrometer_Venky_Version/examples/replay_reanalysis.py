"""Re-analyse a TTbin offline with different parameters (same measurement classes as live).

    python examples/replay_reanalysis.py path/to/raw.ttbin --binwidth 20 --bins 2000 --ch1 2 --ch2 1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.plugins import get_plugin
from app.analysis.replay_session import ReplaySession
from app.measurements import create_measurement


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ttbin")
    ap.add_argument("--ch1", type=int, default=2)
    ap.add_argument("--ch2", type=int, default=1)
    ap.add_argument("--binwidth", type=int, default=50)
    ap.add_argument("--bins", type=int, default=2000)
    ap.add_argument("--speed", type=float, default=-1.0, help="1 = real time, -1 = as fast as possible")
    args = ap.parse_args()
    s = ReplaySession(args.ttbin)
    scan = s.scan_in_background()
    print(f"file: {scan.n_events} events, {scan.duration_ps / 1e12:.3f} s, channels {scan.channels}")
    s.add_measurement(create_measurement("g2", {"channel_1": args.ch1, "channel_2": args.ch2, "binwidth_ps": args.binwidth, "n_bins": args.bins}, name="g2"))
    s.arm(); s.play(args.speed)
    while not s.is_finished():
        s.poll(); time.sleep(0.1)
        print(f"\rreplay {s.position_ps() / 1e12:6.2f} s / {s.total_duration_ps / 1e12:.2f} s", end="")
    res = s.build_results()[0]
    print("\npeak at", res.scalars["peak_position_ps"], "ps; g2(0) =", res.scalars["g2_zero"])
    out = get_plugin("g2_analysis").run(res, {"fit_model": "gaussian", "fit_window_ps": 2000, "background_mode": "stored", "zero_window_ps": 0, "sideband_exclusion_ps": 2000})
    print("fit:", out.scalars["fit"]["parameter_names"], [round(p, 2) for p in out.scalars["fit"]["parameters"]])
    paths = s.save_results(Path(args.ttbin).parent / "analysis")
    print("saved:", [str(p) for p in paths])
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
