"""Histogram, Histogram2D/ND and TimeDifferences measurements."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from swabian_backend import measurements as M

from .base import BaseMeasurement, FieldSpec, MeasurementConfig, PlotSpec, Snapshot, ValidationContext
from .registry import register


# =========================================================================== histogram
@dataclass
class HistogramConfig(MeasurementConfig):
    click_channel: Optional[int] = None
    start_channel: Optional[int] = None
    binwidth_ps: int = 100
    n_bins: int = 2000
    center_zero: bool = False

    FIELDS = [
        FieldSpec("click_channel", "Click (stop) channel", "channel"),
        FieldSpec("start_channel", "Start / reference channel", "channel_optional", help="Unused -> auto-histogram (click channel against itself)"),
        FieldSpec("binwidth_ps", "Bin width", "int", unit="ps", minimum=1, maximum=10**12),
        FieldSpec("n_bins", "Number of bins", "int", minimum=1, maximum=50_000_000),
        FieldSpec("center_zero", "Negative range (use Correlation instead of Histogram)", "bool", help="Histogram only covers positive delays; enabling this uses the Correlation class to obtain +/- delays"),
    ]

    def channels_used(self) -> list[int]:
        return [c for c in (self.click_channel, self.start_channel) if c is not None]

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if self.click_channel is None:
            p.append("A click channel is required")
        return p


@register
class HistogramMeasurement(BaseMeasurement):
    type_name = "histogram"
    display_name = "Histogram"
    category = "basic"
    ConfigClass = HistogramConfig
    HELP = """What it measures
  Multiple-start / multiple-stop histogram of time differences between start and click events
  (official Histogram class). Only positive delays 0 .. binwidth*n_bins are accumulated.
  With "Negative range" enabled the official Correlation class is used instead, which covers
  +/- binwidth*n_bins/2 around zero (both channels act as start and stop).
Display modes
  counts (raw), counts/s (counts / capture duration), normalized (counts / max) and log Y
  are presentation-only; the stored data are raw integer counts plus the bin width and duration.
Output
  arrays: index_ps[n_bins], counts[n_bins]
  scalars: total_counts, peak_position_ps, peak_counts, capture_duration_s"""

    def _build(self, tagger: Any) -> None:
        cfg: HistogramConfig = self.config
        if cfg.center_zero:
            self.hist = M.make_correlation(tagger, int(cfg.click_channel), cfg.start_channel, int(cfg.binwidth_ps), int(cfg.n_bins))
            self.impl_notes = ["implementation: Correlation (negative and positive delays)"]
        else:
            self.hist = M.make_histogram(tagger, int(cfg.click_channel), cfg.start_channel, int(cfg.binwidth_ps), int(cfg.n_bins))
            self.impl_notes = ["implementation: Histogram (positive delays only)"]
        self.objects = [self.hist]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        counts = np.asarray(self.hist.getData(), dtype=np.int64)
        index = np.asarray(self.hist.getIndex(), dtype=np.int64)
        snap.arrays = {"index_ps": index, "counts": counts}
        total = int(counts.sum())
        peak = int(np.argmax(counts)) if counts.size else 0
        snap.scalars = {"total_counts": total, "peak_position_ps": int(index[peak]) if index.size else 0, "peak_counts": int(counts[peak]) if counts.size else 0, "binwidth_ps": int(self.config.binwidth_ps)}
        snap.labels = {"series": [f"{self.label(self.config.click_channel)} vs {self.label(self.config.start_channel) if self.config.start_channel is not None else 'self'}"]}

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("histogram", "Histogram", x_key="index_ps", y_keys=("counts",), x_label="Delay", x_unit="ps", y_label="Counts", y_unit="counts")]

    def units(self) -> dict[str, str]:
        return {"index_ps": "ps", "counts": "counts"}


# =========================================================================== histogram 2D
@dataclass
class Histogram2DConfig(MeasurementConfig):
    start_channel: Optional[int] = None
    stop_channel_1: Optional[int] = None
    stop_channel_2: Optional[int] = None
    binwidth_1_ps: int = 100
    binwidth_2_ps: int = 100
    n_bins_1: int = 200
    n_bins_2: int = 200

    FIELDS = [
        FieldSpec("start_channel", "Start channel", "channel"),
        FieldSpec("stop_channel_1", "Stop channel (axis 1)", "channel"),
        FieldSpec("stop_channel_2", "Stop channel (axis 2)", "channel"),
        FieldSpec("binwidth_1_ps", "Bin width axis 1", "int", unit="ps", minimum=1),
        FieldSpec("binwidth_2_ps", "Bin width axis 2", "int", unit="ps", minimum=1),
        FieldSpec("n_bins_1", "Bins axis 1", "int", minimum=1, maximum=20000),
        FieldSpec("n_bins_2", "Bins axis 2", "int", minimum=1, maximum=20000),
    ]

    def channels_used(self) -> list[int]:
        return [c for c in (self.start_channel, self.stop_channel_1, self.stop_channel_2) if c is not None]

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if None in (self.start_channel, self.stop_channel_1, self.stop_channel_2):
            p.append("Start and both stop channels are required")
        if self.n_bins_1 * self.n_bins_2 > 50_000_000:
            p.append("Histogram2D would allocate more than 50 million bins")
        return p


@register
class Histogram2DMeasurement(BaseMeasurement):
    type_name = "histogram_2d"
    display_name = "Histogram 2D"
    category = "advanced"
    ConfigClass = Histogram2DConfig
    HELP = """What it measures
  Two-dimensional histogram (official Histogram2D): for each start click, the first stop click on
  each stop channel defines the bin coordinate (single-start, single-stop per axis).
Output
  arrays: index_1_ps[n1], index_2_ps[n2], counts[n1, n2]"""

    def _build(self, tagger: Any) -> None:
        c: Histogram2DConfig = self.config
        self.h = M.make_histogram_2d(tagger, int(c.start_channel), int(c.stop_channel_1), int(c.stop_channel_2), int(c.binwidth_1_ps), int(c.binwidth_2_ps), int(c.n_bins_1), int(c.n_bins_2))
        self.objects = [self.h]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        counts = np.asarray(self.h.getData(), dtype=np.int64)
        snap.arrays = {"index_1_ps": np.asarray(self.h.getIndex_1(), dtype=np.int64), "index_2_ps": np.asarray(self.h.getIndex_2(), dtype=np.int64), "counts": counts}
        snap.scalars = {"total_counts": int(counts.sum())}

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("heatmap", "Histogram 2D", x_key="index_1_ps", y_keys=("counts",), x_label="Delay axis 1", x_unit="ps", y_label="Delay axis 2", y_unit="ps", extra={"y_axis_key": "index_2_ps", "value_label": "counts"})]

    def units(self) -> dict[str, str]:
        return {"index_1_ps": "ps", "index_2_ps": "ps", "counts": "counts"}


# =========================================================================== histogram ND
@dataclass
class HistogramNDConfig(MeasurementConfig):
    start_channel: Optional[int] = None
    stop_channels: list[int] = field(default_factory=list)
    binwidth_ps: int = 100
    n_bins: int = 100

    FIELDS = [
        FieldSpec("start_channel", "Start channel", "channel"),
        FieldSpec("stop_channels", "Stop channels (one per dimension)", "channels"),
        FieldSpec("binwidth_ps", "Bin width (all axes)", "int", unit="ps", minimum=1),
        FieldSpec("n_bins", "Bins per axis", "int", minimum=1, maximum=100000),
    ]

    def channels_used(self) -> list[int]:
        return ([self.start_channel] if self.start_channel is not None else []) + list(self.stop_channels)

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if self.start_channel is None:
            p.append("Start channel is required")
        if self.stop_channels and self.n_bins ** len(self.stop_channels) > 50_000_000:
            p.append("HistogramND would allocate more than 50 million bins")
        return p


@register
class HistogramNDMeasurement(BaseMeasurement):
    type_name = "histogram_nd"
    display_name = "Histogram ND"
    category = "advanced"
    ConfigClass = HistogramNDConfig
    HELP = """What it measures
  N-dimensional generalisation of Histogram2D (official HistogramND). The flattened data
  returned by the library are reshaped to n_bins per dimension (row-major, manual 5.5.3).
Output
  arrays: counts[n_bins, ..., n_bins], index_<dim>_ps"""

    def _build(self, tagger: Any) -> None:
        c: HistogramNDConfig = self.config
        n = len(c.stop_channels)
        self.h = M.make_histogram_nd(tagger, int(c.start_channel), c.stop_channels, [int(c.binwidth_ps)] * n, [int(c.n_bins)] * n)
        self.objects = [self.h]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        c: HistogramNDConfig = self.config
        n = len(c.stop_channels)
        flat = np.asarray(self.h.getData(), dtype=np.int64)
        arrays = {"counts": flat.reshape([int(c.n_bins)] * n)}
        for d in range(n):
            arrays[f"index_{d}_ps"] = np.asarray(self.h.getIndex(d), dtype=np.int64)
        snap.arrays = arrays
        snap.scalars = {"total_counts": int(flat.sum()), "dimensions": n}

    def plot_specs(self) -> list[PlotSpec]:
        if len(self.config.stop_channels) == 2:
            return [PlotSpec("heatmap", "Histogram ND (2 dims)", x_key="index_0_ps", y_keys=("counts",), x_label="Delay axis 1", x_unit="ps", y_label="Delay axis 2", y_unit="ps", extra={"y_axis_key": "index_1_ps"})]
        return [PlotSpec("lines", "Histogram ND projection (axis 0)", x_key="index_0_ps", y_keys=("projection_0",), x_label="Delay", x_unit="ps", y_label="Counts", y_unit="counts", extra={"projection_of": "counts"})]


# =========================================================================== time differences
@dataclass
class TimeDifferencesConfig(MeasurementConfig):
    click_channel: Optional[int] = None
    start_channel: Optional[int] = None
    next_channel: Optional[int] = None
    sync_channel: Optional[int] = None
    binwidth_ps: int = 100
    n_bins: int = 1000
    n_histograms: int = 1
    max_rollovers: int = 0

    FIELDS = [
        FieldSpec("click_channel", "Click channel", "channel"),
        FieldSpec("start_channel", "Start channel", "channel_optional"),
        FieldSpec("next_channel", "Next channel", "channel_optional", help="Increments the histogram index"),
        FieldSpec("sync_channel", "Sync channel", "channel_optional", help="Resets the histogram index"),
        FieldSpec("binwidth_ps", "Bin width", "int", unit="ps", minimum=1),
        FieldSpec("n_bins", "Bins per histogram", "int", minimum=1, maximum=10_000_000),
        FieldSpec("n_histograms", "Number of histograms", "int", minimum=1, maximum=1_000_000),
        FieldSpec("max_rollovers", "Maximum rollovers (0 = infinite)", "int", minimum=0),
    ]

    def channels_used(self) -> list[int]:
        return [c for c in (self.click_channel, self.start_channel, self.next_channel, self.sync_channel) if c is not None]

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if self.click_channel is None:
            p.append("Click channel is required")
        if self.n_bins * self.n_histograms > 50_000_000:
            p.append("TimeDifferences would allocate more than 50 million bins")
        return p


@register
class TimeDifferencesMeasurement(BaseMeasurement):
    type_name = "time_differences"
    display_name = "Time Differences"
    category = "advanced"
    ConfigClass = TimeDifferencesConfig
    HELP = """What it measures
  Array of time-difference histograms (official TimeDifferences) with optional next/sync
  channels stepping through histogram indices; suited for pulse-resolved or gated sequences.
Output
  arrays: index_ps[n_bins], histograms[n_histograms, n_bins]
  scalars: histogram_index (current; -1/-2 = waiting states), rollovers, ready"""

    def _build(self, tagger: Any) -> None:
        c: TimeDifferencesConfig = self.config
        self.td = M.make_time_differences(tagger, int(c.click_channel), c.start_channel, c.next_channel, c.sync_channel, int(c.binwidth_ps), int(c.n_bins), int(c.n_histograms))
        if int(c.max_rollovers) > 0:
            self.td.setMaxRollovers(int(c.max_rollovers))
        self.objects = [self.td]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        data = np.asarray(self.td.getData(), dtype=np.int64)
        snap.arrays = {"index_ps": np.asarray(self.td.getIndex(), dtype=np.int64), "histograms": data.reshape(int(self.config.n_histograms), -1)}
        snap.scalars = {"histogram_index": int(self.td.getHistogramIndex()), "rollovers": int(self.td.getCounts()), "ready": bool(self.td.ready()), "total_counts": int(data.sum())}
        snap.finished = bool(self.td.ready()) if int(self.config.max_rollovers) > 0 else False

    def plot_specs(self) -> list[PlotSpec]:
        if int(self.config.n_histograms) > 1:
            return [PlotSpec("heatmap", "Time differences", x_key="index_ps", y_keys=("histograms",), x_label="Delay", x_unit="ps", y_label="Histogram index", extra={"transpose": True})]
        return [PlotSpec("histogram", "Time differences", x_key="index_ps", y_keys=("histograms",), x_label="Delay", x_unit="ps", y_label="Counts", y_unit="counts")]


# =========================================================================== time differences ND
@dataclass
class TimeDifferencesNDConfig(MeasurementConfig):
    click_channel: Optional[int] = None
    start_channel: Optional[int] = None
    next_channels: list[int] = field(default_factory=list)
    sync_channels: list[int] = field(default_factory=list)
    n_histograms: list[int] = field(default_factory=list)
    binwidth_ps: int = 100
    n_bins: int = 1000

    FIELDS = [
        FieldSpec("click_channel", "Click channel", "channel"),
        FieldSpec("start_channel", "Start channel", "channel"),
        FieldSpec("next_channels", "Next channels (one per dimension)", "channels"),
        FieldSpec("sync_channels", "Sync channels (one per dimension)", "channels"),
        FieldSpec("n_histograms", "Histograms per dimension (comma list)", "str"),
        FieldSpec("binwidth_ps", "Bin width", "int", unit="ps", minimum=1),
        FieldSpec("n_bins", "Bins per histogram", "int", minimum=1),
    ]

    def channels_used(self) -> list[int]:
        return [c for c in (self.click_channel, self.start_channel) if c is not None] + list(self.next_channels) + list(self.sync_channels)

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if None in (self.click_channel, self.start_channel):
            p.append("Click and start channels are required")
        if not (len(self.next_channels) == len(self.sync_channels) == len(self.n_histograms)):
            p.append("next_channels, sync_channels and n_histograms must have equal length")
        return p


@register
class TimeDifferencesNDMeasurement(BaseMeasurement):
    type_name = "time_differences_nd"
    display_name = "Time Differences ND"
    category = "advanced"
    ConfigClass = TimeDifferencesNDConfig
    HELP = "Multi-dimensional TimeDifferences (official TimeDifferencesND). Output histograms[M, n_bins] with M = product of n_histograms."

    def _build(self, tagger: Any) -> None:
        c: TimeDifferencesNDConfig = self.config
        n_h = [int(x) for x in (c.n_histograms if isinstance(c.n_histograms, list) else str(c.n_histograms).split(","))]
        self.td = M.make_time_differences_nd(tagger, int(c.click_channel), int(c.start_channel), c.next_channels, c.sync_channels, n_h, int(c.binwidth_ps), int(c.n_bins))
        self.objects = [self.td]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        data = np.asarray(self.td.getData(), dtype=np.int64)
        snap.arrays = {"index_ps": np.asarray(self.td.getIndex(), dtype=np.int64), "histograms": data.reshape(-1, int(self.config.n_bins))}
        snap.scalars = {"histogram_index": [int(i) for i in self.td.getHistogramIndex()], "rollovers": [int(r) for r in self.td.getRollovers()], "total_counts": int(data.sum())}

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("heatmap", "Time differences ND", x_key="index_ps", y_keys=("histograms",), x_label="Delay", x_unit="ps", y_label="Histogram index", extra={"transpose": True})]
