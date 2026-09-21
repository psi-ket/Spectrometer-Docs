"""Coincidences, coincidence matrix, g2 and JSI measurements."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from swabian_backend import measurements as M
from swabian_backend import virtual_channels as V

from ..analysis.statistics import g2_normalization, accidental_counts_per_bin, window_mask
from .base import BaseMeasurement, FieldSpec, MeasurementConfig, PlotSpec, Snapshot, ValidationContext
from .registry import register


# =========================================================================== full coincidences
@dataclass
class CoincidenceConfig(MeasurementConfig):
    channels: list[int] = field(default_factory=list)
    order: int = 2
    window_ps: int = 1000
    timestamp_mode: str = "Last"
    include_channels: list[int] = field(default_factory=list)
    exclude_channels: list[int] = field(default_factory=list)
    max_groups: int = 256
    trace_binwidth_ms: float = 100.0
    trace_n_values: int = 600

    FIELDS = [
        FieldSpec("channels", "Detector channels", "channels"),
        FieldSpec("order", "Coincidence order (n-fold)", "int", minimum=2, maximum=64),
        FieldSpec("window_ps", "Coincidence window", "int", unit="ps", minimum=1),
        FieldSpec("timestamp_mode", "Virtual channel timestamp", "choice", choices=list(V.COINCIDENCE_TIMESTAMP_MODES), advanced=True),
        FieldSpec("include_channels", "Inclusion set (groups must contain all)", "channels_optional", advanced=True),
        FieldSpec("exclude_channels", "Exclusion set (groups must not contain)", "channels_optional", advanced=True),
        FieldSpec("max_groups", "Maximum number of groups", "int", minimum=1, maximum=100000, help="Guard against combinatorial explosion"),
        FieldSpec("trace_binwidth_ms", "Trace bin width", "float", unit="ms", minimum=1.0),
        FieldSpec("trace_n_values", "Trace bins", "int", minimum=2, maximum=100000),
    ]

    def channels_used(self) -> list[int]:
        return list(self.channels)

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if self.channels and self.order > len(self.channels):
            p.append(f"Order {self.order} exceeds the number of selected channels ({len(self.channels)})")
        n = V.count_coincidence_groups(len(self.channels), self.order)
        if n > self.max_groups:
            p.append(f"{n} coincidence groups exceed the configured maximum of {self.max_groups}; raise the limit or filter channels")
        if len(set(self.channels)) > 64:
            p.append("Coincidences supports at most 64 unique channels")
        return p

    def planned_groups(self) -> list[tuple[int, ...]]:
        if not self.channels or self.order > len(self.channels):
            return []
        return V.coincidence_groups(self.channels, int(self.order), self.include_channels or None, self.exclude_channels or None, int(self.max_groups))


@register
class CoincidenceMeasurement(BaseMeasurement):
    type_name = "coincidences"
    display_name = "Full Coincidence"
    category = "basic"
    ConfigClass = CoincidenceConfig
    HELP = """What it measures
  n-fold coincidences between all combinations of the selected detector channels. Each
  combination becomes a software virtual channel (official Coincidences class; the manual
  states this is processed in software, not on the FPGA). The virtual channels are counted with
  Countrate (totals and average rate) and Counter (rate vs time), all on the same synchronized tags.
Quantities (labelled explicitly)
  raw coincidence count  = Countrate.getCountsTotal() on the virtual channel
  coincidence rate (cps) = Countrate.getData() on the virtual channel
  No normalisation is applied here; use the Coincidence Analysis plugin for normalised values.
Combinatorics
  C(N, order) groups are created. The preview shows the number before starting; the maximum
  group count guards against exponential growth.
Output
  arrays: groups[n_groups, order], virtual_channels[n_groups], counts_total[n_groups],
          rates_cps[n_groups], trace_time_s, trace_cps[n_groups, n]"""

    def _build(self, tagger: Any) -> None:
        cfg: CoincidenceConfig = self.config
        groups = cfg.planned_groups()
        if not groups:
            raise ValueError("No coincidence groups to measure")
        self.groups = groups
        self.coinc, self.vch = V.make_coincidences(tagger, groups, int(cfg.window_ps), cfg.timestamp_mode)
        self.vchannels = [self.coinc]
        self.countrate = M.make_countrate(tagger, self.vch)
        self.counter = M.make_counter(tagger, self.vch, int(round(cfg.trace_binwidth_ms * 1e9)), int(cfg.trace_n_values))
        self.objects = [self.countrate, self.counter]
        self.impl_notes = [f"Coincidences virtual channels: {len(self.vch)} groups, window {cfg.window_ps} ps, timestamp {cfg.timestamp_mode}"]

    def group_labels(self) -> list[str]:
        return [" & ".join(self.label(c) for c in g) for g in self.groups]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        cfg: CoincidenceConfig = self.config
        totals = np.asarray(self.countrate.getCountsTotal(), dtype=np.int64)
        rates = np.asarray(self.countrate.getData(), dtype=float)
        trace = np.asarray(self.counter.getDataNormalized(True), dtype=float)
        index = np.asarray(self.counter.getIndex(), dtype=np.int64)
        snap.arrays = {
            "groups": np.asarray(self.groups, dtype=np.int64),
            "virtual_channels": np.asarray(self.vch, dtype=np.int64),
            "counts_total": totals,
            "rates_cps": rates,
            "trace_time_s": index / 1e12,
            "trace_cps": trace,
        }
        snap.labels = {"channel_labels": self.group_labels(), "short_labels": ["&".join(str(c) for c in g) for g in self.groups], "quantity_counts": "raw coincidence count", "quantity_rates": "coincidence rate (cps)"}
        snap.scalars = {"n_groups": len(self.groups), "order": cfg.order, "window_ps": cfg.window_ps, "counts_total": {lab: int(t) for lab, t in zip(self.group_labels(), totals)}, "rates_cps": {lab: float(r) for lab, r in zip(self.group_labels(), rates)}}

    def plot_specs(self) -> list[PlotSpec]:
        return [
            PlotSpec("bars", "Raw coincidence counts per group", y_keys=("counts_total",), y_label="raw coincidence count", y_unit="counts", series_label_key="channel_labels", extra={"short_label_key": "short_labels"}),
            PlotSpec("lines", "Coincidence rate vs time", x_key="trace_time_s", y_keys=("trace_cps",), x_label="Time", x_unit="s", y_label="coincidence rate", y_unit="cps", series_label_key="channel_labels"),
        ]

    def units(self) -> dict[str, str]:
        return {"counts_total": "counts", "rates_cps": "cps", "trace_time_s": "s", "trace_cps": "cps"}


# =========================================================================== coincidence matrix (CorrelationPairs)
@dataclass
class CoincidenceMatrixConfig(MeasurementConfig):
    channels: list[int] = field(default_factory=list)
    binwidth_ps: int = 100
    n_bins: int = 400
    window_ps: int = 1000
    window_center_ps: int = 0
    exclude_self_coincidences: bool = True

    FIELDS = [
        FieldSpec("channels", "Detector channels", "channels"),
        FieldSpec("binwidth_ps", "Bin width", "int", unit="ps", minimum=1),
        FieldSpec("n_bins", "Bins per pair histogram", "int", minimum=2, maximum=1_000_000),
        FieldSpec("window_ps", "Integration window (full width)", "int", unit="ps", minimum=1, help="Bins with |delay - center| <= window/2 are summed into the matrix element"),
        FieldSpec("window_center_ps", "Window center", "int", unit="ps"),
        FieldSpec("exclude_self_coincidences", "Exclude self coincidences on diagonal", "bool", advanced=True),
    ]

    def channels_used(self) -> list[int]:
        return list(self.channels)

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if len(self.channels) < 2:
            p.append("At least two channels are needed for a coincidence matrix")
        if not ctx.library_capabilities.get("CorrelationPairs", True):
            p.append("CorrelationPairs is not available in the installed Swabian library")
        n = len(self.channels)
        if n * n * self.n_bins > 200_000_000:
            p.append("CorrelationPairs would allocate more than 200 million bins")
        return p


@register
class CoincidenceMatrixMeasurement(BaseMeasurement):
    type_name = "coincidence_matrix"
    display_name = "Coincidence Matrix"
    category = "basic"
    ConfigClass = CoincidenceMatrixConfig
    HELP = """What it measures
  N x N matrix of pair coincidences from the official CorrelationPairs class, which returns an
  N x N x n_bins histogram of time differences (first index start channel, second stop channel).
Matrix elements (all stored; the display lets you choose)
  pair coincidence count = sum of histogram bins inside the integration window
  pair coincidence rate  = count / capture duration (cps)
  correlation peak       = maximum bin value inside the window
  The diagonal contains auto-correlations (self coincidences excluded by default).
Axes
  Rows/columns are ordered as selected; with wavelengths mapped, axes are labelled in nm.
Output
  arrays: channels[N], wavelengths_nm[N] (NaN if unknown), index_ps[n_bins], histograms[N,N,n_bins],
          matrix_counts[N,N], matrix_rate_cps[N,N], matrix_peak[N,N]"""

    def _build(self, tagger: Any) -> None:
        cfg: CoincidenceMatrixConfig = self.config
        self.pairs = M.make_correlation_pairs(tagger, cfg.channels, int(cfg.binwidth_ps), int(cfg.n_bins))
        self.countrate = M.make_countrate(tagger, cfg.channels)
        self.objects = [self.pairs, self.countrate]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        cfg: CoincidenceMatrixConfig = self.config
        data = self.pairs.getDataObject()
        hist = np.asarray(data.getCounts(bool(cfg.exclude_self_coincidences)), dtype=np.int64)
        index = np.asarray(self.pairs.getIndex(), dtype=np.int64)
        mask = window_mask(index, cfg.window_center_ps, cfg.window_ps)
        counts = hist[:, :, mask].sum(axis=2)
        dur_s = snap.capture_duration_ps / 1e12
        rate = counts / dur_s if dur_s > 0 else np.full_like(counts, np.nan, dtype=float)
        peak = hist[:, :, mask].max(axis=2) if mask.any() else np.zeros_like(counts)
        singles = np.asarray(self.countrate.getCountsTotal(), dtype=np.int64)
        wl = np.array([self.wavelengths.get(int(c), np.nan) for c in cfg.channels], dtype=float)
        snap.arrays = {"channels": np.asarray(cfg.channels, dtype=np.int64), "wavelengths_nm": wl, "index_ps": index, "histograms": hist, "matrix_counts": counts, "matrix_rate_cps": rate, "matrix_peak": peak, "singles_total": singles}
        snap.labels = {"channel_labels": [self.label(c) for c in cfg.channels]}
        snap.scalars = {"window_ps": cfg.window_ps, "window_center_ps": cfg.window_center_ps, "bins_in_window": int(mask.sum()), "total_pair_counts_in_window": int(counts.sum()), "quantity": "raw pair coincidence counts within window"}

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("matrix", "Coincidence matrix", y_keys=("matrix_counts", "matrix_rate_cps", "matrix_peak"), x_label="Stop channel", y_label="Start channel", series_label_key="channel_labels", extra={"wavelength_key": "wavelengths_nm"})]

    def units(self) -> dict[str, str]:
        return {"index_ps": "ps", "histograms": "counts", "matrix_counts": "counts", "matrix_rate_cps": "cps", "matrix_peak": "counts", "wavelengths_nm": "nm"}


# =========================================================================== g2
@dataclass
class G2Config(MeasurementConfig):
    channel_1: Optional[int] = None
    channel_2: Optional[int] = None
    binwidth_ps: int = 100
    n_bins: int = 2000
    background_window_ps: int = 0
    background_mode: str = "none"

    FIELDS = [
        FieldSpec("channel_1", "Channel 1 (stop)", "channel"),
        FieldSpec("channel_2", "Channel 2 (start); same or unused = auto-correlation", "channel_optional"),
        FieldSpec("binwidth_ps", "Bin width", "int", unit="ps", minimum=1),
        FieldSpec("n_bins", "Number of bins", "int", minimum=2, maximum=50_000_000),
        FieldSpec("background_mode", "Background estimate", "choice", choices=["none", "poisson_accidentals", "sideband_mean"], help="Explicit, stored transformation. 'poisson_accidentals' uses N1*N2*binwidth/duration; 'sideband_mean' averages bins outside the exclusion window"),
        FieldSpec("background_window_ps", "Sideband exclusion window (full width around 0)", "int", unit="ps", minimum=0),
    ]

    def channels_used(self) -> list[int]:
        return [c for c in (self.channel_1, self.channel_2) if c is not None]

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if self.channel_1 is None:
            p.append("Channel 1 is required")
        return p


@register
class G2Measurement(BaseMeasurement):
    type_name = "g2"
    display_name = "g²(τ)"
    category = "basic"
    ConfigClass = G2Config
    HELP = """What it measures
  Histogram of time differences between two channels over positive and negative delays
  (official Correlation class). channel_2 unused or equal to channel_1 gives the auto-correlation.
Normalisation (explicit, stored with the result)
  g2_app(tau) = counts(tau) * T / (binwidth * N1 * N2), with T = capture duration and N1, N2 the
  total events on the two channels measured on the same synchronized tags (Countrate). This is the
  same expression the library uses for Correlation.getDataNormalized(), which is stored as
  g2_library for comparison (they agree once the stream is idle; during fast replay a live
  snapshot may show a transient difference because counts and totals are read a moment apart). It assumes the histogram span is much shorter than T (manual note).
Accidentals / background
  accidentals_per_bin = N1 * N2 * binwidth / T (Poisson estimate). Background subtraction is
  optional and the subtracted values are stored; raw counts are never modified.
Interpretation
  The application reports g2(0), the peak position and the counts only. It does not claim
  bunching or antibunching; use the analysis plugins for fits and confidence intervals.
Output
  arrays: index_ps, counts, g2_app, g2_library, accidentals_per_bin, background_per_bin, counts_bg_subtracted
  scalars: N1, N2, capture_duration_s, g2_zero, peak_position_ps, normalization_equation"""

    def _build(self, tagger: Any) -> None:
        cfg: G2Config = self.config
        ch2 = cfg.channel_2 if cfg.channel_2 is not None else cfg.channel_1
        self.corr = M.make_correlation(tagger, int(cfg.channel_1), int(ch2), int(cfg.binwidth_ps), int(cfg.n_bins))
        chans = [int(cfg.channel_1)] if int(ch2) == int(cfg.channel_1) else [int(cfg.channel_1), int(ch2)]
        self.countrate = M.make_countrate(tagger, chans)
        self.rate_channels = chans
        self.objects = [self.corr, self.countrate]
        self.impl_notes = ["auto-correlation" if len(chans) == 1 else "cross-correlation"]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        cfg: G2Config = self.config
        counts = np.asarray(self.corr.getData(), dtype=np.int64)
        index = np.asarray(self.corr.getIndex(), dtype=np.int64)
        try:
            g2_lib = np.asarray(self.corr.getDataNormalized(), dtype=float)
        except Exception:
            g2_lib = np.full(counts.shape, np.nan)
        totals = np.asarray(self.countrate.getCountsTotal(), dtype=np.int64)
        n1 = int(totals[0]) if totals.size else 0
        n2 = int(totals[1]) if totals.size > 1 else n1
        T = snap.capture_duration_ps
        g2_app = g2_normalization(counts, cfg.binwidth_ps, T, n1, n2)
        acc = accidental_counts_per_bin(cfg.binwidth_ps, T, n1, n2)
        acc_arr = np.full(counts.shape, acc, dtype=float)
        if cfg.background_mode == "poisson_accidentals":
            bg = acc_arr
        elif cfg.background_mode == "sideband_mean":
            excl = window_mask(index, 0, cfg.background_window_ps) if cfg.background_window_ps > 0 else np.zeros(index.shape, bool)
            side = counts[~excl]
            bg = np.full(counts.shape, float(side.mean()) if side.size else 0.0)
        else:
            bg = np.zeros(counts.shape, dtype=float)
        zero_idx = int(np.argmin(np.abs(index))) if index.size else 0
        peak_idx = int(np.argmax(counts)) if counts.size else 0
        snap.arrays = {"index_ps": index, "counts": counts, "g2_app": g2_app, "g2_library": g2_lib, "accidentals_per_bin": acc_arr, "background_per_bin": bg, "counts_bg_subtracted": counts - bg}
        snap.labels = {"series": ["g2"]}
        snap.scalars = {
            "N1": n1, "N2": n2, "binwidth_ps": cfg.binwidth_ps, "total_counts": int(counts.sum()),
            "g2_zero": float(g2_app[zero_idx]) if g2_app.size else float("nan"),
            "g2_zero_bg_subtracted": float(g2_normalization(counts - bg, cfg.binwidth_ps, T, n1, n2)[zero_idx]) if g2_app.size else float("nan"),
            "counts_at_zero": int(counts[zero_idx]) if counts.size else 0,
            "peak_position_ps": int(index[peak_idx]) if index.size else 0, "peak_counts": int(counts[peak_idx]) if counts.size else 0,
            "accidentals_per_bin": float(acc), "background_mode": cfg.background_mode, "background_window_ps": cfg.background_window_ps,
            "normalization_equation": "g2(tau) = counts(tau) * T / (binwidth * N1 * N2)",
        }

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("histogram", "g²(τ)", x_key="index_ps", y_keys=("g2_app",), x_label="Delay τ", x_unit="ps", y_label="g²(τ)", y_unit="", extra={"alternatives": {"raw counts": "counts", "g² (app)": "g2_app", "g² (library)": "g2_library", "counts − background": "counts_bg_subtracted"}})]

    def units(self) -> dict[str, str]:
        return {"index_ps": "ps", "counts": "counts", "g2_app": "1", "g2_library": "1", "accidentals_per_bin": "counts", "background_per_bin": "counts", "counts_bg_subtracted": "counts"}


# =========================================================================== JSI
@dataclass
class JSIConfig(MeasurementConfig):
    signal_channels: list[int] = field(default_factory=list)
    idler_channels: list[int] = field(default_factory=list)
    binwidth_ps: int = 100
    n_bins: int = 400
    window_ps: int = 1000
    window_center_ps: int = 0
    accidental_mode: str = "none"
    normalization: str = "raw"

    FIELDS = [
        FieldSpec("signal_channels", "Signal detector channels", "channels"),
        FieldSpec("idler_channels", "Idler detector channels", "channels"),
        FieldSpec("binwidth_ps", "Bin width", "int", unit="ps", minimum=1),
        FieldSpec("n_bins", "Bins per pair histogram", "int", minimum=2),
        FieldSpec("window_ps", "Coincidence window (full width)", "int", unit="ps", minimum=1),
        FieldSpec("window_center_ps", "Window center", "int", unit="ps"),
        FieldSpec("accidental_mode", "Accidental estimate", "choice", choices=["none", "poisson_accidentals", "sideband_mean"], help="Stored explicitly; raw JSI is kept"),
        FieldSpec("normalization", "Display normalisation", "choice", choices=["raw", "per_second", "max", "sum"], help="Presentation only; raw matrix always stored"),
    ]

    def channels_used(self) -> list[int]:
        return list(self.signal_channels) + list(self.idler_channels)

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if not self.signal_channels or not self.idler_channels:
            p.append("Select at least one signal and one idler channel")
        if set(self.signal_channels) & set(self.idler_channels):
            p.append("A channel cannot be both signal and idler")
        if not ctx.library_capabilities.get("CorrelationPairs", True):
            p.append("CorrelationPairs is not available in the installed Swabian library")
        return p


@register
class JSIMeasurement(BaseMeasurement):
    type_name = "jsi"
    display_name = "Joint Spectral Intensity"
    category = "advanced"
    ConfigClass = JSIConfig
    HELP = """What it measures
  A wavelength-space joint spectral intensity reconstructed at application level from
  detector-pair coincidences. The Time Tagger measures time tags only: the wavelength axes come
  from the detector calibration (detector map), not from the instrument.
Method
  CorrelationPairs over signal + idler channels gives one delay histogram per (signal, idler)
  pair on the same synchronized tags. JSI_raw[i,j] = counts inside the coincidence window for
  signal detector i and idler detector j. Rows and columns are sorted by calibrated wavelength.
Accidentals (explicit, stored)
  poisson_accidentals: Ns_i * Ni_j * binwidth / T per bin, summed over the window bins.
  sideband_mean: mean of bins outside the window times number of window bins.
  JSI_corrected = JSI_raw - accidentals (never overwrites JSI_raw).
Normalisation
  Display only (raw / per second / max / sum); the equation used is stored in the scalars.
Assumptions
  No energy conservation, pump wavelength or SPDC model is assumed. Marginals are plain
  row/column sums of the corrected matrix.
Output
  arrays: signal_wavelengths_nm, idler_wavelengths_nm, signal_channels, idler_channels,
          jsi_raw, jsi_accidentals, jsi_corrected, marginal_signal, marginal_idler,
          index_ps, histograms (full CorrelationPairs data)"""

    def _sorted(self, chans: list[int]) -> list[int]:
        return sorted(chans, key=lambda c: (self.wavelengths.get(int(c), float("inf")), int(c)))

    def _build(self, tagger: Any) -> None:
        cfg: JSIConfig = self.config
        self.sig = self._sorted(list(cfg.signal_channels))
        self.idl = self._sorted(list(cfg.idler_channels))
        self.all_ch = self.sig + self.idl
        self.pairs = M.make_correlation_pairs(tagger, self.all_ch, int(cfg.binwidth_ps), int(cfg.n_bins))
        self.countrate = M.make_countrate(tagger, self.all_ch)
        self.objects = [self.pairs, self.countrate]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        cfg: JSIConfig = self.config
        data = self.pairs.getDataObject()
        hist = np.asarray(data.getCounts(True), dtype=np.int64)
        index = np.asarray(self.pairs.getIndex(), dtype=np.int64)
        mask = window_mask(index, cfg.window_center_ps, cfg.window_ps)
        n_win = int(mask.sum())
        singles = np.asarray(self.countrate.getCountsTotal(), dtype=np.int64)
        ns = len(self.sig)
        sub = hist[:ns, ns:, :]  # start = signal, stop = idler
        jsi_raw = sub[:, :, mask].sum(axis=2).astype(np.int64)
        T = snap.capture_duration_ps
        if cfg.accidental_mode == "poisson_accidentals":
            acc = np.outer(singles[:ns], singles[ns:]).astype(float) * float(cfg.binwidth_ps) / float(T) * n_win if T > 0 else np.zeros(jsi_raw.shape)
        elif cfg.accidental_mode == "sideband_mean":
            side = sub[:, :, ~mask]
            acc = side.mean(axis=2) * n_win if side.shape[2] > 0 else np.zeros(jsi_raw.shape)
        else:
            acc = np.zeros(jsi_raw.shape, dtype=float)
        corrected = jsi_raw - acc
        dur_s = T / 1e12
        if cfg.normalization == "per_second" and dur_s > 0:
            display = corrected / dur_s
            eq = "JSI_corrected / capture_duration_s"
        elif cfg.normalization == "max" and np.nanmax(np.abs(corrected)) > 0:
            display = corrected / np.nanmax(corrected)
            eq = "JSI_corrected / max(JSI_corrected)"
        elif cfg.normalization == "sum" and corrected.sum() != 0:
            display = corrected / corrected.sum()
            eq = "JSI_corrected / sum(JSI_corrected)"
        else:
            display = corrected.astype(float)
            eq = "JSI_corrected (raw counts minus accidentals)"
        swl = np.array([self.wavelengths.get(int(c), np.nan) for c in self.sig], dtype=float)
        iwl = np.array([self.wavelengths.get(int(c), np.nan) for c in self.idl], dtype=float)
        snap.arrays = {
            "signal_channels": np.asarray(self.sig, dtype=np.int64), "idler_channels": np.asarray(self.idl, dtype=np.int64),
            "signal_wavelengths_nm": swl, "idler_wavelengths_nm": iwl,
            "jsi_raw": jsi_raw, "jsi_accidentals": acc, "jsi_corrected": corrected, "jsi_display": display,
            "marginal_signal": corrected.sum(axis=1), "marginal_idler": corrected.sum(axis=0),
            "index_ps": index, "histograms": hist, "singles_total": singles,
        }
        pk = np.unravel_index(int(np.argmax(corrected)), corrected.shape) if corrected.size else (0, 0)
        snap.labels = {"signal_labels": [self.label(c) for c in self.sig], "idler_labels": [self.label(c) for c in self.idl]}
        snap.scalars = {
            "window_ps": cfg.window_ps, "window_center_ps": cfg.window_center_ps, "bins_in_window": n_win,
            "accidental_mode": cfg.accidental_mode, "normalization": cfg.normalization, "normalization_equation": eq,
            "total_raw_coincidences": int(jsi_raw.sum()), "peak_signal_index": int(pk[0]), "peak_idler_index": int(pk[1]),
            "peak_signal_wavelength_nm": float(swl[pk[0]]) if swl.size else float("nan"), "peak_idler_wavelength_nm": float(iwl[pk[1]]) if iwl.size else float("nan"),
        }

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("jsi", "Joint spectral intensity", x_key="idler_wavelengths_nm", y_keys=("jsi_display",), x_label="Idler wavelength", x_unit="nm", y_label="Signal wavelength", y_unit="nm", extra={"y_axis_key": "signal_wavelengths_nm", "alternatives": {"display": "jsi_display", "raw": "jsi_raw", "accidentals": "jsi_accidentals", "corrected": "jsi_corrected"}})]

    def units(self) -> dict[str, str]:
        return {"signal_wavelengths_nm": "nm", "idler_wavelengths_nm": "nm", "jsi_raw": "counts", "jsi_accidentals": "counts", "jsi_corrected": "counts", "index_ps": "ps", "histograms": "counts"}
