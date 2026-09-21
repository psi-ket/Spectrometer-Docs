"""Raw stream analysis on TTbin files via FileReader (custom, user-defined processing)."""
from __future__ import annotations

from typing import Any, Callable, Iterator, Optional

import numpy as np

from swabian_backend.replay import iter_chunks
from swabian_backend.measurements import tag_type_names


def read_events(filenames, n_events: int = 1_000_000, max_events: Optional[int] = None, channels: Optional[list[int]] = None, stop_flag: Optional[Callable[[], bool]] = None) -> dict[str, np.ndarray]:
    """Concatenate chunks from FileReader into arrays (bounded by ``max_events``)."""
    parts: list[dict[str, np.ndarray]] = []
    total = 0
    for chunk in iter_chunks(filenames, n_events, stop_flag):
        if channels:
            m = np.isin(chunk["channels"], np.asarray(channels))
            chunk = {k: (v[m] if isinstance(v, np.ndarray) else v) for k, v in chunk.items()}
        parts.append(chunk)
        total += int(chunk["timestamps"].size)
        if max_events is not None and total >= max_events:
            break
    if not parts:
        return {"timestamps": np.array([], np.int64), "channels": np.array([], np.int32), "event_types": np.array([], np.int32), "missed_events": np.array([], np.int32)}
    out = {k: np.concatenate([p[k] for p in parts]) for k in ("timestamps", "channels", "event_types", "missed_events")}
    if max_events is not None:
        out = {k: v[:max_events] for k, v in out.items()}
    return out


def event_type_summary(event_types: np.ndarray) -> dict[str, int]:
    names = tag_type_names()
    u, c = np.unique(np.asarray(event_types), return_counts=True)
    return {names.get(int(a), str(int(a))): int(b) for a, b in zip(u, c)}


def interarrival_histogram(timestamps: np.ndarray, channels: np.ndarray, channel: int, binwidth_ps: int, n_bins: int) -> tuple[np.ndarray, np.ndarray]:
    ts = np.asarray(timestamps)[np.asarray(channels) == channel]
    if ts.size < 2:
        return np.arange(n_bins) * binwidth_ps, np.zeros(n_bins, np.int64)
    d = np.diff(ts)
    edges = np.arange(n_bins + 1) * binwidth_ps
    h, _ = np.histogram(d, bins=edges)
    return edges[:-1], h.astype(np.int64)
