"""Live raw stream measurement (TimeTagStream) for developer diagnostics."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from swabian_backend import measurements as M

from .base import BaseMeasurement, FieldSpec, MeasurementConfig, PlotSpec, Snapshot
from .registry import register


@dataclass
class RawStreamConfig(MeasurementConfig):
    channels: list[int] = field(default_factory=list)
    n_max_events: int = 1_000_000
    keep_events: int = 100_000

    FIELDS = [
        FieldSpec("channels", "Channels", "channels"),
        FieldSpec("n_max_events", "Buffer size (events)", "int", minimum=1000, maximum=100_000_000),
        FieldSpec("keep_events", "Events kept for display", "int", minimum=100, maximum=10_000_000),
    ]

    def channels_used(self) -> list[int]:
        return list(self.channels)


@register
class RawStreamMeasurement(BaseMeasurement):
    type_name = "raw_stream"
    display_name = "Raw Time-Tag Stream"
    category = "developer"
    ConfigClass = RawStreamConfig
    HELP = """Exposes the live time-tag stream through the official TimeTagStream class. Each snapshot
drains the library buffer (every tag is returned once) and keeps a bounded window of the most
recent events (timestamps, channels, event types, missed events). If the buffer size is reached
between snapshots, events are discarded by the library; the snapshot reports it."""

    def _build(self, tagger: Any) -> None:
        cfg: RawStreamConfig = self.config
        self.stream = M.make_time_tag_stream(tagger, int(cfg.n_max_events), cfg.channels)
        self.objects = [self.stream]
        self._ts = deque(maxlen=int(cfg.keep_events))
        self._ch = deque(maxlen=int(cfg.keep_events))
        self._et = deque(maxlen=int(cfg.keep_events))
        self._me = deque(maxlen=int(cfg.keep_events))
        self.total_events = 0
        self.overflow_chunks = 0

    def _fill_snapshot(self, snap: Snapshot) -> None:
        buf = self.stream.getData()
        arr = M.buffer_to_arrays(buf)
        n = arr["size"]
        self.total_events += n
        if arr["has_overflows"]:
            self.overflow_chunks += 1
        full = n >= int(self.config.n_max_events)
        if n:
            self._ts.extend(arr["timestamps"].tolist())
            self._ch.extend(arr["channels"].tolist())
            self._et.extend(arr["event_types"].tolist())
            self._me.extend(arr["missed_events"].tolist())
        ts = np.asarray(self._ts, dtype=np.int64)
        ch = np.asarray(self._ch, dtype=np.int32)
        snap.arrays = {"timestamps_ps": ts, "channels": ch, "event_types": np.asarray(self._et, dtype=np.int32), "missed_events": np.asarray(self._me, dtype=np.int32)}
        u, c = np.unique(ch, return_counts=True) if ch.size else (np.array([]), np.array([]))
        snap.scalars = {"events_in_last_chunk": n, "total_events": self.total_events, "buffer_full_warning": full, "overflow_chunks": self.overflow_chunks, "events_per_channel_window": {str(int(a)): int(b) for a, b in zip(u, c)}, "t_get_data_ps": arr["t_get_data"], "t_start_ps": arr["t_start"]}

    def plot_specs(self) -> list[PlotSpec]:
        return [PlotSpec("scatter", "Recent time tags", x_key="timestamps_ps", y_keys=("channels",), x_label="Time", x_unit="ps", y_label="Channel")]
