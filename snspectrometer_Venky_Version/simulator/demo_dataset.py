"""Synthetic dataset generator built on the Swabian Experimental signal generators.

Mechanism (verified on library 2.21.2):
  * ``Experimental.ExponentialSignalGenerator(tagger, rate, base_channel, seed)`` adds a
    Poisson event stream to a *base* (physical-looking) channel. Two generators with the same
    rate and seed emit identical event sequences, so placing them on two channels yields
    perfectly correlated photon pairs (SPDC-like).
  * ``Experimental.TransformEfficiency / TransformGaussianBroadening / TransformDeadtime``
    with ``copy=False`` act in place on a channel: detector efficiency, timing jitter, dead time.
  * Dark counts are an extra Poisson generator on the same channel.
  * Per-channel delays are baked into the data by recording delayed channels into separate
    files and merging them with ``mergeStreamFiles`` time offsets (the manual documents this
    function). Everything is recorded with the official ``FileWriter``.

These classes are marked *Experimental* by Swabian and are used only here.
Nothing in the simulator is required for real hardware operation.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

from swabian_backend import get_api
from swabian_backend.replay import merge_files, scan_file
from swabian_backend.recorder import ttbin_sequence_files

log = logging.getLogger("snspec.simulator")


@dataclass
class SimDetector:
    channel: int
    name: str
    wavelength_nm: Optional[float] = None
    role: str = "unassigned"
    efficiency: float = 0.8
    dark_cps: float = 200.0
    jitter_ps: float = 40.0
    deadtime_ps: float = 20_000.0
    delay_ps: int = 0
    singles_cps: float = 0.0  # uncorrelated background photons

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SimPair:
    channel_a: int
    channel_b: int
    rate_cps: float
    seed: int


@dataclass
class SimPeriodic:
    channel: int
    name: str
    period_ps: int
    role: str = "marker"


@dataclass
class SimScenario:
    name: str
    duration_s: float
    detectors: list[SimDetector]
    pairs: list[SimPair]
    periodic: list[SimPeriodic] = field(default_factory=list)
    description: str = ""
    pump_wavelength_nm: Optional[float] = None
    stream_block_latency_ms: int = 20

    def channels(self) -> list[int]:
        return sorted({d.channel for d in self.detectors} | {p.channel for p in self.periodic})

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "duration_s": self.duration_s, "description": self.description, "pump_wavelength_nm": self.pump_wavelength_nm,
            "detectors": [d.to_dict() for d in self.detectors],
            "pairs": [asdict(p) for p in self.pairs],
            "periodic": [asdict(p) for p in self.periodic],
        }


# --------------------------------------------------------------------------- scenarios
def scenario_two_detector(duration_s: float = 10.0, delay_ps: int = 1500, pair_rate: float = 50_000.0) -> SimScenario:
    det = [
        SimDetector(1, "SNSPD-A", 810.0, "signal", 0.85, 150.0, 35.0, 20_000.0, 0, 5_000.0),
        SimDetector(2, "SNSPD-B", 810.0, "idler", 0.75, 250.0, 45.0, 20_000.0, delay_ps, 4_000.0),
    ]
    return SimScenario("two_detector_pairs", duration_s, det, [SimPair(1, 2, pair_rate, 11)], description="Two correlated SNSPD channels (photon pairs) with a 1.5 ns cable delay on channel 2, jitter, dark counts and uncorrelated singles.")


def scenario_four_detector(duration_s: float = 10.0) -> SimScenario:
    det = [
        SimDetector(1, "SNSPD-01", 780.0, "signal", 0.85, 100.0, 30.0, 20_000.0, 0, 3_000.0),
        SimDetector(2, "SNSPD-02", 800.0, "signal", 0.80, 120.0, 30.0, 20_000.0, 800, 3_000.0),
        SimDetector(3, "SNSPD-03", 820.0, "idler", 0.78, 150.0, 35.0, 20_000.0, -600, 3_000.0),
        SimDetector(4, "SNSPD-04", 840.0, "idler", 0.70, 200.0, 40.0, 20_000.0, 2200, 3_000.0),
    ]
    pairs = [SimPair(1, 4, 30_000.0, 21), SimPair(2, 3, 30_000.0, 22), SimPair(1, 3, 6_000.0, 23), SimPair(2, 4, 6_000.0, 24)]
    per = [SimPeriodic(8, "MARKER-1kHz", 1_000_000_000, "marker")]
    return SimScenario("four_detector_coincidences", duration_s, det, pairs, per, description="Four wavelength-mapped detectors with strong (1,4)/(2,3) and weak cross pairs, per-channel delays and a 1 kHz marker channel for gated counting.")


def scenario_spectrometer(duration_s: float = 10.0, n_signal: int = 4, n_idler: int = 4, pump_nm: float = 405.0, center_signal_nm: float = 790.0, center_idler_nm: float = 830.0, step_nm: float = 5.0, pump_bandwidth_nm: float = 4.0, phase_matching_nm: float = 12.0, peak_pair_rate: float = 25_000.0) -> SimScenario:
    """SPDC-like joint distribution: pair rate ~ exp(-((ls+li-2*l0)/pump_bw)^2) * exp(-((ls-li-(cs-ci))/pm_bw)^2)."""
    det: list[SimDetector] = []
    for i in range(n_signal):
        wl = center_signal_nm + (i - (n_signal - 1) / 2) * step_nm
        det.append(SimDetector(i + 1, f"S{i + 1}", round(wl, 3), "signal", 0.8 - 0.03 * i, 100.0 + 20 * i, 35.0, 20_000.0, 300 * i, 2_000.0))
    for j in range(n_idler):
        wl = center_idler_nm + (j - (n_idler - 1) / 2) * step_nm
        det.append(SimDetector(n_signal + j + 1, f"I{j + 1}", round(wl, 3), "idler", 0.75 - 0.03 * j, 150.0 + 20 * j, 40.0, 20_000.0, -250 * j + 1000, 2_000.0))
    pairs: list[SimPair] = []
    l0 = (center_signal_nm + center_idler_nm) / 2.0
    seed = 100
    for i in range(n_signal):
        for j in range(n_idler):
            ls = det[i].wavelength_nm
            li = det[n_signal + j].wavelength_nm
            amp = math.exp(-(((ls + li) / 2 - l0) / pump_bandwidth_nm) ** 2) * math.exp(-(((ls - li) - (center_signal_nm - center_idler_nm)) / phase_matching_nm) ** 2)
            rate = peak_pair_rate * amp
            if rate >= 50.0:
                seed += 1
                pairs.append(SimPair(det[i].channel, det[n_signal + j].channel, rate, seed))
    per = [SimPeriodic(n_signal + n_idler + 1, "SYNC-100kHz", 10_000_000, "sync"), SimPeriodic(n_signal + n_idler + 2, "MARKER-1kHz", 1_000_000_000, "marker")]
    return SimScenario("spectrometer_jsi", duration_s, det, pairs, per, description=f"{n_signal} signal + {n_idler} idler wavelength-mapped SNSPDs with an SPDC-like anti-correlated joint spectrum, a 100 kHz sync and a 1 kHz marker channel.", pump_wavelength_nm=pump_nm)


SCENARIOS: dict[str, Callable[..., SimScenario]] = {
    "two_detector_pairs": scenario_two_detector,
    "four_detector_coincidences": scenario_four_detector,
    "spectrometer_jsi": scenario_spectrometer,
}


# --------------------------------------------------------------------------- generation
def generate_dataset(scenario: SimScenario, output_dir: str | Path, progress_cb: Optional[Callable[[float, str], None]] = None, stop_flag: Optional[Callable[[], bool]] = None) -> dict[str, Any]:
    """Generate ``<output_dir>/<scenario.name>.ttbin`` (+ metadata JSON). Runs in real time."""
    api = get_api()
    TT = api.require()
    E = getattr(TT, "Experimental", None)
    if E is None:
        raise RuntimeError("The installed Swabian library has no Experimental signal generators; simulator datasets cannot be generated")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"{scenario.name}.ttbin"
    work = out_dir / f"_{scenario.name}_parts"
    work.mkdir(exist_ok=True)

    tagger = TT.createTimeTaggerVirtual()
    keep: list[Any] = []
    try:
        # pair sources: identical Poisson sequences on both channels
        for p in scenario.pairs:
            keep.append(E.ExponentialSignalGenerator(tagger, float(p.rate_cps), int(p.channel_a), int(p.seed)))
            keep.append(E.ExponentialSignalGenerator(tagger, float(p.rate_cps), int(p.channel_b), int(p.seed)))
        seed = 5000
        for d in scenario.detectors:
            seed += 1
            if d.efficiency < 1.0:
                keep.append(E.TransformEfficiency(tagger, int(d.channel), float(d.efficiency), False, seed))
            if d.singles_cps > 0:
                seed += 1
                keep.append(E.ExponentialSignalGenerator(tagger, float(d.singles_cps), int(d.channel), seed))
            if d.dark_cps > 0:
                seed += 1
                keep.append(E.ExponentialSignalGenerator(tagger, float(d.dark_cps), int(d.channel), seed))
            if d.jitter_ps > 0:
                seed += 1
                keep.append(E.TransformGaussianBroadening(tagger, int(d.channel), float(d.jitter_ps) * 1e-12, False, seed))
            if d.deadtime_ps > 0:
                keep.append(E.TransformDeadtime(tagger, int(d.channel), float(d.deadtime_ps) * 1e-12, False))
        for per in scenario.periodic:
            keep.append(E.PatternSignalGenerator(tagger, [int(per.period_ps)], True, 0, 0, int(per.channel)))

        # recording: undelayed channels together, each delayed channel separately
        writers: list[tuple[Any, str, int]] = []
        undelayed = [c for c in scenario.channels() if next((d.delay_ps for d in scenario.detectors if d.channel == c), 0) == 0]
        delayed = [d for d in scenario.detectors if d.delay_ps != 0]
        if undelayed:
            fn = str(work / "base.ttbin")
            writers.append((TT.FileWriter(tagger, fn, undelayed), fn, 0))
        for d in delayed:
            fn = str(work / f"ch{d.channel}.ttbin")
            writers.append((TT.FileWriter(tagger, fn, [int(d.channel)]), fn, int(d.delay_ps)))
        t0 = time.time()
        tagger.run(1.0)
        while time.time() - t0 < scenario.duration_s:
            if stop_flag is not None and stop_flag():
                break
            time.sleep(0.1)
            if progress_cb is not None:
                progress_cb(min(1.0, (time.time() - t0) / scenario.duration_s), "generating")
        for w, _, _ in writers:
            w.stop()
        tagger.stop()
        total_events = sum(int(w.getTotalEvents()) for w, _, _ in writers)
    finally:
        keep.clear()
        TT.freeTimeTagger(tagger)

    if progress_cb is not None:
        progress_cb(1.0, "merging")
    inputs = [fn for _, fn, _ in writers]
    offsets = [off for _, _, off in writers]
    if len(inputs) == 1 and offsets[0] == 0:
        # nothing to merge: rename sequence into final name
        for f in ttbin_sequence_files(inputs[0]):
            src = Path(f)
            dst = out_dir / src.name.replace("base", scenario.name, 1)
            src.replace(dst)
    else:
        merge_files(str(final_path), inputs, [0] * len(inputs), offsets, False)
        for fn in inputs:
            for f in ttbin_sequence_files(fn):
                try:
                    Path(f).unlink()
                except OSError:
                    pass
    try:
        work.rmdir()
    except OSError:
        pass
    scan = scan_file(str(final_path))
    meta = {
        "generator": "snspectrometer.simulator.demo_dataset",
        "swabian_library_version": api.version,
        "scenario": scenario.to_dict(),
        "file": str(final_path),
        "files": ttbin_sequence_files(str(final_path)),
        "events_recorded": total_events,
        "scan": scan.to_dict(),
        "detector_map": detector_map_dict(scenario, "SIM:" + scenario.name),
        "note": "Synthetic data generated with Swabian Experimental signal generators; not a physical measurement.",
    }
    with open(out_dir / f"{scenario.name}.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    log.info("Generated dataset %s: %d events, %.2f s", final_path, scan.n_events, scan.duration_ps / 1e12)
    return meta


def detector_map_dict(scenario: SimScenario, device_id: str) -> dict[str, Any]:
    chans = []
    for d in scenario.detectors:
        chans.append({"tagger_id": device_id, "physical_channel": d.channel, "detector_name": d.name, "wavelength_nm": d.wavelength_nm, "wavelength_bandwidth_nm": 1.0, "detector_enabled": True, "role": d.role, "input_delay_ps": 0, "event_divider": 1, "notes": f"simulated: delay {d.delay_ps} ps, efficiency {d.efficiency}, dark {d.dark_cps} cps"})
    for p in scenario.periodic:
        chans.append({"tagger_id": device_id, "physical_channel": p.channel, "detector_name": p.name, "wavelength_nm": None, "detector_enabled": True, "role": p.role, "input_delay_ps": 0, "event_divider": 1, "notes": f"periodic {p.period_ps} ps"})
    return {"channels": chans}


def dataset_metadata_path(dataset_file: str | Path) -> Path:
    p = Path(dataset_file)
    return p.with_suffix(".json")


def load_dataset_metadata(dataset_file: str | Path) -> dict[str, Any]:
    p = dataset_metadata_path(dataset_file)
    if p.exists():
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}
