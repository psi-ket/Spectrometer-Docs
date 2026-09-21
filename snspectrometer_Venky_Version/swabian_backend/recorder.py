"""Raw TTbin recording through the official ``FileWriter``.

``FileWriter`` writes a losslessly compressed time-tag stream and stores the
Time Tagger configuration in every file. Split files are named
``name.ttbin`` (header), ``name.1.ttbin``, ``name.2.ttbin`` ... (manual 5.5.6).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional, Sequence

from .api import get_api

log = logging.getLogger("snspec.backend.recorder")


def ttbin_sequence_files(header_path: str | os.PathLike) -> list[str]:
    """All files belonging to a FileWriter sequence: header plus ``name.N.ttbin`` parts."""
    p = Path(header_path)
    if not p.suffix == ".ttbin":
        return [str(p)] if p.exists() else []
    stem = p.name[: -len(".ttbin")]
    pattern = re.compile(re.escape(stem) + r"\.(\d+)\.ttbin$")
    parts = []
    if p.parent.exists():
        for f in p.parent.iterdir():
            m = pattern.match(f.name)
            if m:
                parts.append((int(m.group(1)), str(f)))
    parts.sort()
    out = [str(p)] if p.exists() else []
    out.extend(f for _, f in parts)
    return out


class Recorder:
    """One ``FileWriter`` instance with bookkeeping."""

    def __init__(self, tagger: Any, filename: str, channels: Sequence[int], max_file_size_bytes: Optional[int] = None):
        TT = get_api().require()
        chans = [int(c) for c in channels]
        if not chans:
            raise ValueError("Recording needs at least one channel")
        path = Path(filename)
        if path.suffix != ".ttbin":
            path = path.with_suffix(".ttbin")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.filename = str(path)
        self.channels = chans
        self.writer = TT.FileWriter(tagger, self.filename, chans)
        self.max_file_size_bytes = None
        if max_file_size_bytes:
            self.writer.setMaxFileSize(int(max_file_size_bytes))
            self.max_file_size_bytes = int(max_file_size_bytes)
        self.markers: list[str] = []
        log.info("FileWriter created: %s channels=%s", self.filename, chans)

    # IteratorBase delegation --------------------------------------------------
    @property
    def objects(self) -> list[Any]:
        return [self.writer]

    def start(self) -> None:
        self.writer.start()

    def startFor(self, duration_ps: int, clear: bool = True) -> None:
        self.writer.startFor(int(duration_ps), clear)

    def stop(self) -> None:
        self.writer.stop()

    def is_running(self) -> bool:
        try:
            return bool(self.writer.isRunning())
        except Exception:
            return False

    def split(self, new_filename: str = "") -> None:
        if new_filename:
            self.writer.split(new_filename)
            self.filename = new_filename
        else:
            self.writer.split()

    def set_marker(self, marker: str) -> None:
        self.writer.setMarker(str(marker))
        self.markers.append(str(marker))

    def total_events(self) -> int:
        try:
            return int(self.writer.getTotalEvents())
        except Exception:
            return 0

    def total_bytes(self) -> int:
        try:
            return int(self.writer.getTotalSize())
        except Exception:
            return 0

    def files(self) -> list[str]:
        return ttbin_sequence_files(self.filename)

    def describe(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "channels": list(self.channels),
            "max_file_size_bytes": self.max_file_size_bytes,
            "total_events": self.total_events(),
            "total_bytes": self.total_bytes(),
            "files": self.files(),
            "markers": list(self.markers),
        }
