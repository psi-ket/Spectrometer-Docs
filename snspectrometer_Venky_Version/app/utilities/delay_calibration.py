"""Automatic channel delay calibration (manual 3.1.3 workflow).

1. Select a reference channel and the channels to calibrate.
2. Optionally reset input delays to zero (the manual's recipe requires it).
3. Run Correlation(reference, channel) for each channel inside one
   SynchronizedMeasurements group for a fixed duration.
4. Delay = count-weighted centre of the correlation histogram (optionally inside a
   window around the maximum).
5. The user accepts/rejects each delay; accepted ones are applied with setInputDelay().
6. Re-run the correlations to verify the peaks are centred at zero.

Sign convention (verified on the library): Correlation(channel_1=ref, channel_2=ch)
histograms t_ref - t_ch, so a peak at +d means ``ch`` fires earlier and needs
``setInputDelay(ch, +d)``; this matches the manual's example.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from swabian_backend.devices import TaggerHandle
from swabian_backend import measurements as M
from swabian_backend.synchronization import SyncGroup

from ..analysis.statistics import weighted_center, window_mask
from ..analysis.peaks import fwhm_discrete

log = logging.getLogger("snspec.delaycal")


@dataclass
class ChannelDelayResult:
    channel: int
    index_ps: np.ndarray
    counts: np.ndarray
    delay_ps: float
    peak_ps: float
    fwhm_ps: float
    total_counts: int
    previous_delay_ps: int
    proposed_delay_ps: int
    significant: bool = True
    significance_note: str = ""
    accepted: bool = False
    applied_delay_ps: Optional[int] = None
    after_index_ps: Optional[np.ndarray] = None
    after_counts: Optional[np.ndarray] = None
    residual_ps: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "delay_ps": self.delay_ps, "peak_ps": self.peak_ps, "fwhm_ps": self.fwhm_ps, "total_counts": self.total_counts, "significant": self.significant, "significance_note": self.significance_note, "previous_delay_ps": self.previous_delay_ps, "proposed_delay_ps": self.proposed_delay_ps, "accepted": self.accepted, "applied_delay_ps": self.applied_delay_ps, "residual_ps": self.residual_ps}


class DelayCalibration:
    def __init__(self, handle: TaggerHandle, reference_channel: int, channels: list[int], binwidth_ps: int = 10, n_bins: int = 4000, duration_s: float = 2.0, window_ps: int = 0, reset_delays_first: bool = True):
        self.handle = handle
        self.reference = int(reference_channel)
        self.channels = [int(c) for c in channels if int(c) != int(reference_channel)]
        self.binwidth_ps = int(binwidth_ps)
        self.n_bins = int(n_bins)
        self.duration_s = float(duration_s)
        self.window_ps = int(window_ps)
        self.reset_delays_first = bool(reset_delays_first)
        self.results: dict[int, ChannelDelayResult] = {}
        self.log: list[str] = []

    def _measure(self, progress: Optional[Callable[[float], None]] = None, stop_flag: Optional[Callable[[], bool]] = None) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        tagger = self.handle.tagger
        sync = SyncGroup(tagger)
        corrs = {ch: M.make_correlation(sync.proxy, self.reference, ch, self.binwidth_ps, self.n_bins) for ch in self.channels}
        sync.start_for(int(self.duration_s * 1e12), clear=True)
        t0 = time.time()
        while sync.is_running():
            if stop_flag is not None and stop_flag():
                sync.stop()
                break
            if progress is not None:
                progress(min(1.0, (time.time() - t0) / self.duration_s))
            time.sleep(0.05)
        sync.wait_until_finished(2000)
        out = {}
        for ch, c in corrs.items():
            out[ch] = (np.asarray(c.getIndex(), dtype=np.int64), np.asarray(c.getData(), dtype=np.int64))
        return out

    def measure(self, progress: Optional[Callable[[float], None]] = None, stop_flag: Optional[Callable[[], bool]] = None) -> dict[int, ChannelDelayResult]:
        tagger = self.handle.tagger
        previous = {}
        for ch in [self.reference] + self.channels:
            try:
                previous[ch] = int(tagger.getInputDelay(ch))
            except Exception:
                previous[ch] = 0
        if self.reset_delays_first:
            for ch in [self.reference] + self.channels:
                tagger.setInputDelay(ch, 0)
            self.log.append("Input delays reset to 0 before measuring (manual 3.1.3)")
        data = self._measure(progress, stop_flag)
        self.results = {}
        for ch, (idx, cnt) in data.items():
            peak = float(idx[int(np.argmax(cnt))]) if cnt.size else 0.0
            mask = window_mask(idx, peak, self.window_ps) if self.window_ps > 0 else None
            d = weighted_center(idx, cnt, mask)
            base = 0 if self.reset_delays_first else previous.get(ch, 0)
            proposed = int(round(base + d)) if np.isfinite(d) else previous.get(ch, 0)
            r = ChannelDelayResult(ch, idx, cnt, d, peak, fwhm_discrete(idx, cnt), int(cnt.sum()), previous.get(ch, 0), proposed)
            r.significant, r.significance_note = self._significance(cnt)
            if not r.significant:
                r.accepted = False
            self.results[ch] = r
            self.log.append(f"channel {ch}: delay {d:.1f} ps (peak {peak:.0f} ps, {int(cnt.sum())} counts) -> proposed setInputDelay({ch}, {proposed})")
        if self.reset_delays_first:
            # restore previous delays until the user explicitly applies new ones
            for ch, d in previous.items():
                try:
                    tagger.setInputDelay(ch, d)
                except Exception:
                    pass
        return self.results

    @staticmethod
    def _significance(counts: np.ndarray, n_sigma: float = 5.0) -> tuple[bool, str]:
        """Is the maximum bin significantly above the histogram background (Poisson)?"""
        c = np.asarray(counts, dtype=float)
        if c.size < 10 or c.sum() < 20:
            return False, f"too few counts ({int(c.sum())}) for a reliable delay"
        peak = float(c.max())
        bg = float(np.median(c))
        sigma = max(1.0, np.sqrt(bg))
        if peak < bg + n_sigma * sigma:
            return False, f"no significant correlation peak (max {peak:.0f} vs background {bg:.1f} +/- {sigma:.1f})"
        return True, f"peak {peak:.0f} above background {bg:.1f} ({(peak - bg) / sigma:.1f} sigma)"

    def apply(self, accepted: dict[int, bool]) -> dict[int, int]:
        tagger = self.handle.tagger
        applied = {}
        for ch, ok in accepted.items():
            r = self.results.get(ch)
            if r is None:
                continue
            r.accepted = bool(ok)
            if ok:
                tagger.setInputDelay(ch, int(r.proposed_delay_ps))
                r.applied_delay_ps = int(tagger.getInputDelay(ch))
                applied[ch] = r.applied_delay_ps
                self.log.append(f"applied setInputDelay({ch}, {r.proposed_delay_ps}); readback {r.applied_delay_ps}")
                log.info("Delay calibration applied on %s channel %s: %s ps", self.handle.id, ch, r.applied_delay_ps)
        return applied

    def verify(self, progress: Optional[Callable[[float], None]] = None, stop_flag: Optional[Callable[[], bool]] = None) -> dict[int, float]:
        data = self._measure(progress, stop_flag)
        residuals = {}
        for ch, (idx, cnt) in data.items():
            r = self.results.get(ch)
            if r is None:
                continue
            peak = float(idx[int(np.argmax(cnt))]) if cnt.size else 0.0
            mask = window_mask(idx, peak, self.window_ps) if self.window_ps > 0 else None
            r.after_index_ps, r.after_counts = idx, cnt
            r.residual_ps = weighted_center(idx, cnt, mask)
            residuals[ch] = r.residual_ps
            self.log.append(f"verify channel {ch}: residual {r.residual_ps:.1f} ps")
        return residuals

    def report(self) -> dict[str, Any]:
        return {"reference_channel": self.reference, "binwidth_ps": self.binwidth_ps, "n_bins": self.n_bins, "duration_s": self.duration_s, "window_ps": self.window_ps, "reset_delays_first": self.reset_delays_first, "channels": {str(ch): r.to_dict() for ch, r in self.results.items()}, "log": list(self.log)}
