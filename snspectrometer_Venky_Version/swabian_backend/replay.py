"""TTbin replay (``TimeTaggerVirtual``), direct reading (``FileReader``) and merging.

Replay speed semantics follow the manual: 1.0 is real time, negative values
replay as fast as possible, and speeds between 0 and 0.1 are not supported by
the library. Pausing is not an API primitive; :meth:`ReplaySource.pause` stops the
replay, remembers the stream position and :meth:`ReplaySource.resume` re-queues
the file from that position with ``appendFile(begin=...)``. Data already
buffered by the library beyond the pause point may be delivered twice.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

import numpy as np

from .api import get_api
from .devices import TaggerHandle, open_virtual
from .measurements import buffer_to_arrays, make_time_tag_stream, tag_type_names
from .recorder import ttbin_sequence_files

log = logging.getLogger("snspec.backend.replay")

MIN_SUPPORTED_SPEED = 0.1
SPEED_PRESETS: list[tuple[str, float]] = [
    ("0.1x", 0.1), ("0.25x", 0.25), ("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("5x", 5.0),
    ("10x", 10.0), ("20x", 20.0), ("50x", 50.0), ("100x", 100.0), ("Max", -1.0),
]


def normalise_speed(speed: float) -> tuple[float, str]:
    """Map a requested speed onto what the library accepts.

    Returns (library_speed, note). Speeds below 0.1 are clamped to 0.1 and the
    note explains it; ``speed <= 0`` means as fast as possible (-1).
    """
    s = float(speed)
    if s <= 0:
        return -1.0, "as fast as possible"
    if s < MIN_SUPPORTED_SPEED:
        return MIN_SUPPORTED_SPEED, f"requested {s}x is below the library minimum of {MIN_SUPPORTED_SPEED}x; clamped"
    return s, ""


class ReplaySource:
    """A ``TimeTaggerVirtual`` bound to a file, with position tracking and pause/resume."""

    def __init__(self, filename: str, begin_ps: int = 0, duration_ps: int = -1):
        self.filename = str(filename)
        self.begin_ps = int(begin_ps)
        self.duration_ps = int(duration_ps)
        self.handle: TaggerHandle = open_virtual(self.filename, self.begin_ps, self.duration_ps)
        self.tagger = self.handle.tagger
        self.channels = list(self.handle.channels_all)
        self._probe = None
        self._t_start = 0
        self._paused_at_ps: Optional[int] = None
        self._speed = 1.0
        self._running = False
        self._resume_offset_ps = 0
        if self.channels:
            try:
                self._probe = make_time_tag_stream(self.tagger, 1, [self.channels[0]])
            except Exception as exc:  # pragma: no cover
                log.warning("position probe unavailable: %s", exc)

    # ------------------------------------------------------------------ control
    def run(self, speed: float = 1.0) -> str:
        lib_speed, note = normalise_speed(speed)
        self._speed = lib_speed
        self.tagger.run(lib_speed)
        self._running = True
        self._paused_at_ps = None
        return note

    def stop(self) -> None:
        try:
            self.tagger.stop()
        finally:
            self._running = False

    def position_ps(self) -> int:
        """Current stream position relative to the first replayed tag (0 if unknown)."""
        if self._probe is None:
            return 0
        try:
            buf = self._probe.getData()
            t_get = int(buf.tGetData)
            t_start = int(buf.tStart)
            if t_start and not self._t_start:
                self._t_start = t_start
            return max(0, t_get - (self._t_start or t_start))
        except Exception:
            return 0

    def pause(self) -> int:
        """Stop the replay and remember the position; returns the paused position (ps)."""
        self.tagger.stop()
        time.sleep(0.05)
        pos = self.position_ps()
        self._paused_at_ps = pos
        self._running = False
        return pos

    def resume(self, speed: Optional[float] = None) -> str:
        if self._paused_at_ps is None:
            return self.run(self._speed if speed is None else speed)
        begin = self.begin_ps + int(self._paused_at_ps)
        self.tagger.appendFile(self.filename, int(begin), self.duration_ps, True)
        return self.run(self._speed if speed is None else speed)

    def restart(self, speed: Optional[float] = None) -> str:
        self.tagger.stop()
        self._t_start = 0
        self._paused_at_ps = None
        self.tagger.appendFile(self.filename, self.begin_ps, self.duration_ps, True)
        return self.run(self._speed if speed is None else speed)

    def append_file(self, filename: str, begin_ps: int = 0, duration_ps: int = -1, clear: bool = False) -> int:
        return int(self.tagger.appendFile(str(filename), int(begin_ps), int(duration_ps), bool(clear)))

    def is_finished(self) -> bool:
        try:
            return bool(self.tagger.waitUntilFinished(0, 0))
        except Exception:
            return False

    def wait(self, timeout_ms: int = -1) -> bool:
        return bool(self.tagger.waitUntilFinished(0, int(timeout_ms)))

    @property
    def is_paused(self) -> bool:
        return self._paused_at_ps is not None

    @property
    def speed(self) -> float:
        return self._speed

    def close(self) -> None:
        self.handle.close()


# --------------------------------------------------------------------------- file scanning
@dataclass
class FileScanResult:
    files: list[str]
    channels: list[int] = field(default_factory=list)
    counts_per_channel: dict[int, int] = field(default_factory=dict)
    n_events: int = 0
    t_first_ps: Optional[int] = None
    t_last_ps: Optional[int] = None
    n_overflow_begin: int = 0
    n_missed_event_records: int = 0
    missed_events_total: int = 0
    configuration: dict[str, Any] = field(default_factory=dict)
    last_marker: str = ""
    complete: bool = True

    @property
    def duration_ps(self) -> int:
        if self.t_first_ps is None or self.t_last_ps is None:
            return 0
        return int(self.t_last_ps - self.t_first_ps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": list(self.files),
            "channels": list(self.channels),
            "counts_per_channel": {str(k): v for k, v in self.counts_per_channel.items()},
            "n_events": self.n_events,
            "t_first_ps": self.t_first_ps,
            "t_last_ps": self.t_last_ps,
            "duration_ps": self.duration_ps,
            "n_overflow_begin": self.n_overflow_begin,
            "n_missed_event_records": self.n_missed_event_records,
            "missed_events_total": self.missed_events_total,
            "last_marker": self.last_marker,
            "complete": self.complete,
        }


def _config_dict(fr: Any) -> dict[str, Any]:
    try:
        cfg = fr.getConfiguration()
    except Exception as exc:
        return {"error": str(exc)}
    if isinstance(cfg, dict):
        return cfg
    try:
        return json.loads(cfg)
    except Exception:
        return {"raw": str(cfg)}


def open_reader(filenames: Sequence[str] | str) -> Any:
    TT = get_api().require()
    if isinstance(filenames, (str, Path)):
        return TT.FileReader(str(filenames))
    return TT.FileReader([str(f) for f in filenames])


def iter_chunks(filenames: Sequence[str] | str, n_events: int = 1_000_000, stop_flag: Optional[Callable[[], bool]] = None) -> Iterator[dict[str, Any]]:
    """Yield raw chunks (numpy arrays) from a FileReader until the files are exhausted."""
    fr = open_reader(filenames)
    while fr.hasData():
        if stop_flag is not None and stop_flag():
            break
        buf = fr.getData(int(n_events))
        arrays = buffer_to_arrays(buf)
        if arrays["size"] == 0:
            break
        yield arrays


def scan_file(filenames: Sequence[str] | str, chunk: int = 1_000_000, progress_cb: Optional[Callable[[int], None]] = None, stop_flag: Optional[Callable[[], bool]] = None) -> FileScanResult:
    """Read the whole file(s) once and gather per-channel statistics."""
    files = [str(filenames)] if isinstance(filenames, (str, Path)) else [str(f) for f in filenames]
    fr = open_reader(files)
    res = FileScanResult(files=files)
    res.configuration = _config_dict(fr)
    try:
        res.channels = [int(c) for c in fr.getChannelList()]
    except Exception:
        res.channels = []
    types = tag_type_names()
    code_of = {v: k for k, v in types.items()}
    overflow_begin = code_of.get("OverflowBegin", 2)
    missed = code_of.get("MissedEvents", 4)
    timetag = code_of.get("TimeTag", 0)
    counts: dict[int, int] = {}
    while fr.hasData():
        if stop_flag is not None and stop_flag():
            res.complete = False
            break
        buf = fr.getData(int(chunk))
        n = int(buf.size)
        if n == 0:
            break
        ts = np.asarray(buf.getTimestamps())
        ch = np.asarray(buf.getChannels())
        et = np.asarray(buf.getEventTypes())
        me = np.asarray(buf.getMissedEvents())
        is_tag = et == timetag
        res.n_events += int(is_tag.sum())
        if res.t_first_ps is None and n:
            res.t_first_ps = int(ts[0])
        res.t_last_ps = int(ts[-1])
        res.n_overflow_begin += int((et == overflow_begin).sum())
        mm = et == missed
        res.n_missed_event_records += int(mm.sum())
        res.missed_events_total += int(me[mm].sum()) if mm.any() else 0
        u, c = np.unique(ch[is_tag], return_counts=True)
        for a, b in zip(u.tolist(), c.tolist()):
            counts[int(a)] = counts.get(int(a), 0) + int(b)
        if progress_cb is not None:
            progress_cb(res.n_events)
    res.counts_per_channel = dict(sorted(counts.items()))
    if not res.channels:
        res.channels = sorted(counts)
    try:
        res.last_marker = str(fr.getLastMarker())
    except Exception:
        pass
    return res


# --------------------------------------------------------------------------- merging
def merge_files(output: str, inputs: Sequence[str], channel_offsets: Sequence[int], time_offsets_ps: Sequence[int], overlap_only: bool = False) -> str:
    """``mergeStreamFiles(output, inputs, channel_offsets, time_offsets, overlap_only)``.

    The manual notes that no rescaling into a common time base is performed.
    """
    TT = get_api().require()
    ins = [str(f) for f in inputs]
    if not ins:
        raise ValueError("No input files")
    if not (len(ins) == len(channel_offsets) == len(time_offsets_ps)):
        raise ValueError("inputs, channel_offsets and time_offsets must have equal length")
    out = str(output)
    if not out.endswith(".ttbin"):
        out += ".ttbin"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    TT.mergeStreamFiles(out, ins, [int(c) for c in channel_offsets], [int(t) for t in time_offsets_ps], bool(overlap_only))
    return out


def sequence_files(path: str) -> list[str]:
    return ttbin_sequence_files(path)
