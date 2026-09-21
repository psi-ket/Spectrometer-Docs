"""Count rate, count trace and gated counter measurements."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from swabian_backend import measurements as M

from .base import BaseMeasurement, FieldSpec, MeasurementConfig, PlotSpec, Snapshot, ValidationContext, nan_stats
from .registry import register


# =========================================================================== count rate
@dataclass
class CountRateConfig(MeasurementConfig):
    channels: list[int] = field(default_factory=list)
    stats_binwidth_ms: float = 100.0
    stats_bins: int = 600

    FIELDS = [
        FieldSpec("channels", "Channels", "channels", help="Channels whose average count rate is measured"),
        FieldSpec("stats_binwidth_ms", "Statistics bin width", "float", unit="ms", minimum=1.0, maximum=60000.0, help="Bin width of the companion Counter used for rolling statistics"),
        FieldSpec("stats_bins", "Statistics bins", "int", minimum=2, maximum=100000, help="Number of bins kept for min/max/mean/std"),
    ]

    def channels_used(self) -> list[int]:
        return list(self.channels)


@register
class CountRateMeasurement(BaseMeasurement):
    type_name = "countrate"
    display_name = "Count Rate"
    category = "basic"
    ConfigClass = CountRateConfig
    HELP = """What it measures
  Average counts per second on each selected channel since the start (or last clear),
  using the official Countrate class. Total counts come from Countrate.getCountsTotal().
Rolling statistics
  A companion Counter (same synchronized tags) with the configured bin width feeds
  min / max / mean / std of the binned rate. NaN bins (not yet integrated, or in overflow)
  are excluded and the number of used bins is reported.
Units
  Rates in cps (counts per second), bin width in ms, time axis in s.
Raw data
  Enable "Record raw TTbin" in the group to store the time-tag stream with FileWriter.
Output
  arrays: rates_cps[n_ch], counts_total[n_ch], trace_time_s[n_bins], trace_cps[n_ch, n_bins]
  scalars: per channel min/max/mean/std, capture_duration_s
Limitations
  Countrate is robust against overflows (manual 5.5.2); the Counter trace marks overflow bins as NaN."""

    def _build(self, tagger: Any) -> None:
        cfg: CountRateConfig = self.config
        self.countrate = M.make_countrate(tagger, cfg.channels)
        bw_ps = int(round(cfg.stats_binwidth_ms * 1e9))
        self.counter = M.make_counter(tagger, cfg.channels, bw_ps, int(cfg.stats_bins))
        self.objects = [self.countrate, self.counter]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        cfg: CountRateConfig = self.config
        rates = np.asarray(self.countrate.getData(), dtype=float)
        totals = np.asarray(self.countrate.getCountsTotal(), dtype=np.int64)
        trace = np.asarray(self.counter.getDataNormalized(True), dtype=float)
        index = np.asarray(self.counter.getIndex(), dtype=np.int64)
        snap.arrays = {"rates_cps": rates, "counts_total": totals, "trace_time_s": index / 1e12, "trace_cps": trace}
        snap.labels = {"channels": [int(c) for c in cfg.channels], "channel_labels": [self.label(c) for c in cfg.channels]}
        stats = {}
        for i, ch in enumerate(cfg.channels):
            stats[str(ch)] = nan_stats(trace[i]) if trace.ndim == 2 and i < trace.shape[0] else nan_stats(np.array([]))
        snap.scalars = {
            "rates_cps": {str(c): float(r) for c, r in zip(cfg.channels, rates)},
            "counts_total": {str(c): int(t) for c, t in zip(cfg.channels, totals)},
            "rolling_statistics_cps": stats,
            "statistics_binwidth_ms": cfg.stats_binwidth_ms,
        }

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("lines", "Count rate trace", x_key="trace_time_s", y_keys=("trace_cps",), x_label="Time", x_unit="s", y_label="Count rate", y_unit="cps", series_label_key="channel_labels")]

    def units(self) -> dict[str, str]:
        return {"rates_cps": "cps", "counts_total": "counts", "trace_time_s": "s", "trace_cps": "cps"}

    def array_descriptions(self) -> dict[str, str]:
        return {"rates_cps": "Countrate.getData(): average cps per channel", "counts_total": "Countrate.getCountsTotal()", "trace_time_s": "Counter.getIndex()/1e12 (rolling)", "trace_cps": "Counter.getDataNormalized(rolling=True); NaN = not integrated / overflow"}


# =========================================================================== counter trace
@dataclass
class CounterConfig(MeasurementConfig):
    channels: list[int] = field(default_factory=list)
    binwidth_ms: float = 100.0
    n_values: int = 1000
    rolling: bool = True

    FIELDS = [
        FieldSpec("channels", "Channels", "channels"),
        FieldSpec("binwidth_ms", "Bin width", "float", unit="ms", minimum=0.001, maximum=3.6e6),
        FieldSpec("n_values", "Number of bins", "int", minimum=1, maximum=10_000_000),
        FieldSpec("rolling", "Rolling display", "bool", help="Rolling: newest bin on the right; otherwise sweep mode (manual 5.5.2)"),
    ]

    def channels_used(self) -> list[int]:
        return list(self.channels)


@register
class CounterMeasurement(BaseMeasurement):
    type_name = "counter"
    display_name = "Count Trace"
    category = "basic"
    ConfigClass = CounterConfig
    HELP = """What it measures
  Time trace of counts per bin on one or more channels (official Counter class).
Parameters
  Bin width (ms) and number of bins; rolling vs sweep filling as in Counter.getData(rolling).
Units
  counts per bin (raw integers) and cps (Counter.getDataNormalized). Time axis in s.
Output
  arrays: time_s[n_values], counts[n_ch, n_values], cps[n_ch, n_values], counts_total[n_ch]
Limitations
  Bins in overflow are NaN in cps; raw counts of such bins are still reported by the library."""

    def _build(self, tagger: Any) -> None:
        cfg: CounterConfig = self.config
        self.counter = M.make_counter(tagger, cfg.channels, int(round(cfg.binwidth_ms * 1e9)), int(cfg.n_values))
        self.objects = [self.counter]

    def _fill_snapshot(self, snap: Snapshot) -> None:
        cfg: CounterConfig = self.config
        counts = np.asarray(self.counter.getData(bool(cfg.rolling)), dtype=np.int64)
        cps = np.asarray(self.counter.getDataNormalized(bool(cfg.rolling)), dtype=float)
        index = np.asarray(self.counter.getIndex(), dtype=np.int64)
        totals = np.asarray(self.counter.getDataTotalCounts(), dtype=np.int64)
        snap.arrays = {"time_s": index / 1e12, "counts": counts, "cps": cps, "counts_total": totals}
        snap.labels = {"channels": [int(c) for c in cfg.channels], "channel_labels": [self.label(c) for c in cfg.channels]}
        snap.scalars = {"binwidth_ms": cfg.binwidth_ms, "rolling": cfg.rolling, "counts_total": {str(c): int(t) for c, t in zip(cfg.channels, totals)}}

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("lines", "Count trace", x_key="time_s", y_keys=("cps",), x_label="Time", x_unit="s", y_label="Count rate", y_unit="cps", series_label_key="channel_labels")]

    def units(self) -> dict[str, str]:
        return {"time_s": "s", "counts": "counts/bin", "cps": "cps", "counts_total": "counts"}


# =========================================================================== gated counter
@dataclass
class GatedCounterConfig(MeasurementConfig):
    click_channels: list[int] = field(default_factory=list)
    begin_channel: Optional[int] = None
    end_channel: Optional[int] = None
    n_values: int = 1000

    FIELDS = [
        FieldSpec("click_channels", "Click channels", "channels"),
        FieldSpec("begin_channel", "Begin marker channel", "channel"),
        FieldSpec("end_channel", "End marker channel (optional)", "channel_optional"),
        FieldSpec("n_values", "Number of values", "int", minimum=1, maximum=10_000_000),
    ]

    def channels_used(self) -> list[int]:
        out = list(self.click_channels)
        if self.begin_channel is not None:
            out.append(self.begin_channel)
        if self.end_channel is not None:
            out.append(self.end_channel)
        return out

    def validate(self, ctx: ValidationContext) -> list[str]:
        p = super().validate(ctx)
        if self.begin_channel is None:
            p.append("A begin marker channel is required")
        if self.end_channel is not None and self.end_channel == self.begin_channel:
            p.append("Begin and end marker channels must differ")
        return p


@register
class GatedCounterMeasurement(BaseMeasurement):
    type_name = "gated_counter"
    display_name = "Count Between Markers"
    category = "basic"
    ConfigClass = GatedCounterConfig
    HELP = """What it measures
  Counts on the click channels between marker events: a tag on the begin channel starts a
  value and steps to the next; an optional end channel closes the value.
Implementation
  The experiment-facing name is "Count Between Markers". With library 2.22+ the multi-channel
  GatedCounter class is used; older libraries (e.g. 2.21) fall back to one CountBetweenMarkers
  object per click channel. The implementation used is stored in the result.
Output
  arrays: values[n_ch, n_values] (raw counts), index_ps[n_values] (time of each begin click
  relative to the first), bin_widths_ps[n_values] (begin -> next begin/end)
  scalars: ready (True when all values are filled), implementation"""

    def _build(self, tagger: Any) -> None:
        cfg: GatedCounterConfig = self.config
        self.gated = M.make_gated_counter(tagger, cfg.click_channels, int(cfg.begin_channel), cfg.end_channel, int(cfg.n_values))
        self.impl_notes = [f"implementation: {self.gated.impl_name}"]
        self.objects = [self.gated]

    def iterator_objects(self) -> list[Any]:
        return list(self.gated.objects)

    def _fill_snapshot(self, snap: Snapshot) -> None:
        cfg: GatedCounterConfig = self.config
        values = np.asarray(self.gated.getData(), dtype=np.int64)
        index = np.asarray(self.gated.getIndex(), dtype=np.int64)
        widths = np.asarray(self.gated.getBinWidths(), dtype=np.int64)
        snap.arrays = {"values": values, "index_ps": index, "bin_widths_ps": widths}
        snap.labels = {"channels": list(cfg.click_channels), "channel_labels": [self.label(c) for c in cfg.click_channels]}
        filled = int(np.count_nonzero(widths > 0))
        snap.scalars = {"ready": bool(self.gated.ready()), "implementation": self.gated.impl_name, "values_filled": filled, "n_values": cfg.n_values}
        snap.finished = bool(self.gated.ready())

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("lines", "Counts between markers", x_key="index_ps", y_keys=("values",), x_label="Marker time", x_unit="ps", y_label="Counts", y_unit="counts", series_label_key="channel_labels", extra={"step": True})]

    def units(self) -> dict[str, str]:
        return {"values": "counts", "index_ps": "ps", "bin_widths_ps": "ps"}
