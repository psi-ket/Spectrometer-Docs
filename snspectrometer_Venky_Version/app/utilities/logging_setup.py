"""Structured logging with an in-memory ring buffer and Swabian logger integration."""
from __future__ import annotations

import collections
import logging
import logging.handlers
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from swabian_backend import get_api

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


class RingBufferHandler(logging.Handler):
    """Keeps the last N records for the Logs tab and run exports."""

    def __init__(self, capacity: int = 5000):
        super().__init__()
        self.records: collections.deque = collections.deque(maxlen=capacity)
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self._lock2 = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {"time": record.created, "level": record.levelname, "logger": record.name, "message": record.getMessage(), "text": self.format(record)}
            with self._lock2:
                self.records.append(entry)
            for cb in list(self.listeners):
                try:
                    cb(entry)
                except Exception:
                    pass
        except Exception:  # pragma: no cover
            self.handleError(record)

    def dump(self, since: Optional[float] = None) -> str:
        with self._lock2:
            recs = list(self.records)
        if since is not None:
            recs = [r for r in recs if r["time"] >= since]
        return "\n".join(r["text"] for r in recs)


_ring: Optional[RingBufferHandler] = None


def setup_logging(log_dir: Optional[str | Path] = None, level: str = "INFO", capacity: int = 5000) -> RingBufferHandler:
    global _ring
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(LOG_FORMAT)
    if _ring is None:
        _ring = RingBufferHandler(capacity)
        _ring.setFormatter(fmt)
        root.addHandler(_ring)
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        console.setLevel(getattr(logging, level.upper(), logging.INFO))
        root.addHandler(console)
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(Path(log_dir) / "snspectrometer.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
            fh.setFormatter(fmt)
            fh.setLevel(logging.DEBUG)
            root.addHandler(fh)
    set_level(level)
    install_swabian_logger()
    return _ring


def get_ring() -> Optional[RingBufferHandler]:
    return _ring


def set_level(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, (RingBufferHandler, logging.FileHandler)):
            h.setLevel(lvl)
    logging.getLogger("snspec").setLevel(logging.DEBUG)


def install_swabian_logger() -> bool:
    """Route ``setLogger`` callbacks (level, message) into Python logging."""
    api = get_api()
    if not api.available:
        return False
    TT = api.module
    lib_log = logging.getLogger("swabian.TimeTagger")
    level_map = {}
    for name, py in (("LOGGER_INFO", logging.INFO), ("LOGGER_WARNING", logging.WARNING), ("LOGGER_ERROR", logging.ERROR)):
        v = getattr(TT, name, None)
        if v is not None:
            level_map[int(v)] = py

    def callback(level, message):
        lib_log.log(level_map.get(int(level), logging.INFO), "%s", str(message).strip())

    return api.install_logger(callback)
