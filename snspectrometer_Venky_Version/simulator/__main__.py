"""CLI: generate synthetic demo datasets.

    python -m simulator --scenario spectrometer_jsi --duration 10 --out ./demo_datasets
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .demo_dataset import SCENARIOS, generate_dataset


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate synthetic SNSPD datasets with the Swabian engine")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="spectrometer_jsi")
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--out", default="demo_datasets")
    ap.add_argument("--all", action="store_true", help="generate every scenario")
    args = ap.parse_args()
    names = sorted(SCENARIOS) if args.all else [args.scenario]
    for n in names:
        scen = SCENARIOS[n](args.duration)
        meta = generate_dataset(scen, Path(args.out), progress_cb=lambda f, m: print(f"\r{n}: {m} {f * 100:5.1f}%", end=""))
        print(f"\n{meta['file']}: {meta['scan']['n_events']} events, {meta['scan']['duration_ps'] / 1e12:.2f} s, channels {meta['scan']['channels']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
