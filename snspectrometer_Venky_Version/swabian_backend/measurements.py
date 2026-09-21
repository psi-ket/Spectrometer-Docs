"""Factories for the official Swabian measurement classes.

Each factory validates its arguments, creates the library object and returns it.
Signatures follow the manual (release 2.22.6) and were checked against the
installed 2.21.2 package. ``GatedCounter`` is only present from 2.22; on older
libraries :func:`make_gated_counter` falls back to one ``CountBetweenMarkers``
per click channel and says so through ``impl_name``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

import numpy as np

from .api import get_api

log = logging.getLogger("snspec.backend.measurements")


def unused_channel() -> int:
    return get_api().CHANNEL_UNUSED


def _chan(ch: Optional[int]) -> int:
    return unused_channel() if ch is None else int(ch)


def _check_channels(channels: Sequence[int]) -> list[int]:
    chans = [int(c) for c in channels]
    if not chans:
        raise ValueError("At least one channel is required")
    return chans


def _check_bins(binwidth_ps: int, n_bins: int) -> tuple[int, int]:
    if int(binwidth_ps) <= 0:
        raise ValueError("Bin width must be a positive number of picoseconds")
    if int(n_bins) <= 0:
        raise ValueError("Number of bins must be positive")
    if int(binwidth_ps) * int(n_bins) > 2**62:
        raise ValueError("Histogram span exceeds the representable time range")
    return int(binwidth_ps), int(n_bins)


# --------------------------------------------------------------------------- counting
def make_countrate(tagger: Any, channels: Sequence[int]) -> Any:
    TT = get_api().require()
    return TT.Countrate(tagger, _check_channels(channels))


def make_counter(tagger: Any, channels: Sequence[int], binwidth_ps: int, n_values: int) -> Any:
    TT = get_api().require()
    bw, n = _check_bins(binwidth_ps, n_values)
    return TT.Counter(tagger, _check_channels(channels), bw, n)


class GatedCounterAdapter:
    """Uniform interface over ``GatedCounter`` (2.22+) or several ``CountBetweenMarkers``.

    ``getData()`` always returns an array of shape (n_channels, n_values).
    """

    def __init__(self, tagger: Any, click_channels: Sequence[int], begin_channel: int, end_channel: Optional[int], n_values: int):
        TT = get_api().require()
        self.click_channels = _check_channels(click_channels)
        self.begin_channel = int(begin_channel)
        self.end_channel = _chan(end_channel)
        self.n_values = int(n_values)
        if self.n_values <= 0:
            raise ValueError("n_values must be positive")
        if hasattr(TT, "GatedCounter"):
            self.impl_name = "GatedCounter"
            self.objects = [TT.GatedCounter(tagger, self.click_channels, self.begin_channel, self.end_channel, self.n_values)]
        elif hasattr(TT, "CountBetweenMarkers"):
            self.impl_name = "CountBetweenMarkers"
            self.objects = [TT.CountBetweenMarkers(tagger, ch, self.begin_channel, self.end_channel, self.n_values) for ch in self.click_channels]
        else:
            raise RuntimeError("Neither GatedCounter nor CountBetweenMarkers is available in the installed library")

    # IteratorBase-like delegation ------------------------------------------------
    def start(self):
        for o in self.objects:
            o.start()

    def startFor(self, duration_ps: int, clear: bool = True):
        for o in self.objects:
            o.startFor(int(duration_ps), clear)

    def stop(self):
        for o in self.objects:
            o.stop()

    def clear(self):
        for o in self.objects:
            o.clear()

    def isRunning(self) -> bool:
        return any(o.isRunning() for o in self.objects)

    def getCaptureDuration(self) -> int:
        return int(max(o.getCaptureDuration() for o in self.objects))

    def ready(self) -> bool:
        return all(bool(o.ready()) for o in self.objects)

    def getData(self) -> np.ndarray:
        if self.impl_name == "GatedCounter":
            return np.asarray(self.objects[0].getData()).reshape(len(self.click_channels), self.n_values)
        return np.vstack([np.asarray(o.getData()) for o in self.objects])

    def getIndex(self) -> np.ndarray:
        return np.asarray(self.objects[0].getIndex())

    def getBinWidths(self) -> np.ndarray:
        return np.asarray(self.objects[0].getBinWidths())

    def getConfiguration(self):
        return [o.getConfiguration() for o in self.objects]


def make_gated_counter(tagger: Any, click_channels: Sequence[int], begin_channel: int, end_channel: Optional[int] = None, n_values: int = 1000) -> GatedCounterAdapter:
    return GatedCounterAdapter(tagger, click_channels, begin_channel, end_channel, n_values)


# --------------------------------------------------------------------------- histograms
def make_histogram(tagger: Any, click_channel: int, start_channel: Optional[int], binwidth_ps: int, n_bins: int) -> Any:
    TT = get_api().require()
    bw, n = _check_bins(binwidth_ps, n_bins)
    return TT.Histogram(tagger, int(click_channel), _chan(start_channel), bw, n)


def make_correlation(tagger: Any, channel_1: int, channel_2: Optional[int], binwidth_ps: int, n_bins: int) -> Any:
    """``Correlation(tagger, channel_1, channel_2, binwidth, n_bins)``; same channel or unused -> auto-correlation."""
    TT = get_api().require()
    bw, n = _check_bins(binwidth_ps, n_bins)
    return TT.Correlation(tagger, int(channel_1), _chan(channel_2), bw, n)


def make_correlation_pairs(tagger: Any, channels: Sequence[int], binwidth_ps: int, n_bins: int) -> Any:
    TT = get_api().require()
    if not hasattr(TT, "CorrelationPairs"):
        raise RuntimeError("CorrelationPairs is not available in the installed library")
    bw, n = _check_bins(binwidth_ps, n_bins)
    chans = _check_channels(channels)
    if len(chans) < 2:
        raise ValueError("CorrelationPairs needs at least two channels")
    return TT.CorrelationPairs(tagger, chans, bw, n)


def make_histogram_2d(tagger: Any, start_channel: int, stop_channel_1: int, stop_channel_2: int, binwidth_1: int, binwidth_2: int, n_bins_1: int, n_bins_2: int) -> Any:
    TT = get_api().require()
    _check_bins(binwidth_1, n_bins_1)
    _check_bins(binwidth_2, n_bins_2)
    if int(n_bins_1) * int(n_bins_2) > 50_000_000:
        raise ValueError("Histogram2D would allocate more than 50 million bins")
    return TT.Histogram2D(tagger, int(start_channel), int(stop_channel_1), int(stop_channel_2), int(binwidth_1), int(binwidth_2), int(n_bins_1), int(n_bins_2))


def make_histogram_nd(tagger: Any, start_channel: int, stop_channels: Sequence[int], binwidths: Sequence[int], n_bins: Sequence[int]) -> Any:
    TT = get_api().require()
    stops = _check_channels(stop_channels)
    if not (len(stops) == len(binwidths) == len(n_bins)):
        raise ValueError("stop_channels, binwidths and n_bins must have the same length")
    total = 1
    for bw, n in zip(binwidths, n_bins):
        _check_bins(bw, n)
        total *= int(n)
    if total > 50_000_000:
        raise ValueError("HistogramND would allocate more than 50 million bins")
    return TT.HistogramND(tagger, int(start_channel), stops, [int(b) for b in binwidths], [int(n) for n in n_bins])


def make_time_differences(tagger: Any, click_channel: int, start_channel: Optional[int], next_channel: Optional[int], sync_channel: Optional[int], binwidth_ps: int, n_bins: int, n_histograms: int) -> Any:
    TT = get_api().require()
    bw, n = _check_bins(binwidth_ps, n_bins)
    if int(n_histograms) <= 0:
        raise ValueError("n_histograms must be positive")
    return TT.TimeDifferences(tagger, int(click_channel), _chan(start_channel), _chan(next_channel), _chan(sync_channel), bw, n, int(n_histograms))


def make_time_differences_nd(tagger: Any, click_channel: int, start_channel: int, next_channels: Sequence[int], sync_channels: Sequence[int], n_histograms: Sequence[int], binwidth_ps: int, n_bins: int) -> Any:
    TT = get_api().require()
    bw, n = _check_bins(binwidth_ps, n_bins)
    return TT.TimeDifferencesND(tagger, int(click_channel), int(start_channel), [int(c) for c in next_channels], [int(c) for c in sync_channels], [int(h) for h in n_histograms], bw, n)


# --------------------------------------------------------------------------- streaming
def make_time_tag_stream(tagger: Any, n_max_events: int, channels: Sequence[int]) -> Any:
    TT = get_api().require()
    if int(n_max_events) <= 0:
        raise ValueError("n_max_events must be positive")
    return TT.TimeTagStream(tagger, int(n_max_events), _check_channels(channels))


def buffer_to_arrays(buf: Any) -> dict[str, Any]:
    """Convert a ``TimeTagStreamBuffer`` into plain numpy arrays."""
    return {
        "timestamps": np.asarray(buf.getTimestamps(), dtype=np.int64),
        "channels": np.asarray(buf.getChannels(), dtype=np.int32),
        "event_types": np.asarray(buf.getEventTypes(), dtype=np.int32),
        "missed_events": np.asarray(buf.getMissedEvents(), dtype=np.int32),
        "size": int(buf.size),
        "has_overflows": bool(buf.hasOverflows),
        "t_start": int(getattr(buf, "tStart", 0)),
        "t_get_data": int(getattr(buf, "tGetData", 0)),
    }


def tag_type_names() -> dict[int, str]:
    """Event type codes from ``Tag::Type`` (manual 5.2.5)."""
    TT = get_api().module
    names = {0: "TimeTag", 1: "Error", 2: "OverflowBegin", 3: "OverflowEnd", 4: "MissedEvents"}
    tt = getattr(TT, "TagType", None) if TT is not None else None
    if tt is not None:
        for n in list(names.values()):
            v = getattr(tt, n, None)
            if isinstance(v, int):
                names[v] = n
    return names


# --------------------------------------------------------------------------- helpers
def capture_duration_ps(obj: Any) -> int:
    try:
        return int(obj.getCaptureDuration())
    except Exception:
        return 0


def describe_configuration(obj: Any) -> Any:
    try:
        return obj.getConfiguration()
    except Exception as exc:
        return {"error": str(exc)}
