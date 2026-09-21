"""Thread helpers: generic worker pool jobs and the acquisition poll thread."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Any, Callable, Optional

from qtpy.QtCore import QObject, QRunnable, QThread, Signal, Slot

log = logging.getLogger("snspec.controller.workers")


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str, str)      # message, traceback
    progress = Signal(float, str)


class Worker(QRunnable):
    """Run ``fn(*args, **kwargs)`` in the global thread pool.

    If the callable accepts ``progress_cb`` / ``stop_flag`` keyword arguments they
    are injected automatically.
    """

    def __init__(self, fn: Callable, *args: Any, name: str = "", **kwargs: Any):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.name = name or getattr(fn, "__name__", "worker")
        self.signals = WorkerSignals()
        self._stop = threading.Event()
        self.setAutoDelete(True)
        import inspect
        try:
            params = inspect.signature(fn).parameters
            if "progress_cb" in params:
                self.kwargs.setdefault("progress_cb", lambda frac, msg="": self.signals.progress.emit(float(frac), str(msg)))
            if "stop_flag" in params:
                self.kwargs.setdefault("stop_flag", self._stop.is_set)
            if "progress" in params and "progress" not in self.kwargs:
                self.kwargs["progress"] = lambda frac: self.signals.progress.emit(float(frac), "")
        except (TypeError, ValueError):
            pass

    def stop(self) -> None:
        self._stop.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            log.exception("Worker %s failed", self.name)
            self.signals.error.emit(str(exc), traceback.format_exc())
            return
        self.signals.finished.emit(result)


class PollThread(QThread):
    """Periodically samples measurement groups, replay sessions, device health and system load."""

    snapshots = Signal(object)          # {group_id: {measurement_id: Snapshot}}
    replay_snapshots = Signal(object)   # {session_id: {measurement_id: Snapshot}}
    health = Signal(object)
    system = Signal(object)
    groups_changed = Signal()

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._stop = threading.Event()
        self.refresh_hz = 5.0
        self.health_interval_s = 1.0
        self.system_interval_s = 2.0

    def stop(self) -> None:
        self._stop.set()
        self.wait(3000)

    def run(self) -> None:
        last_health = 0.0
        last_system = 0.0
        c = self.controller
        while not self._stop.is_set():
            t0 = time.time()
            try:
                snaps = c.measurements.poll_all()
                if snaps:
                    self.snapshots.emit(snaps)
            except Exception:
                log.exception("poll_all failed")
            try:
                rs = {}
                for sid, session in list(c.replay_sessions.items()):
                    if session.state in ("playing", "paused", "armed"):
                        rs[sid] = session.poll()
                if rs:
                    self.replay_snapshots.emit(rs)
            except Exception:
                log.exception("replay poll failed")
            now = time.time()
            if now - last_health >= self.health_interval_s:
                last_health = now
                try:
                    self.health.emit(c.hardware.poll_health())
                except Exception:
                    log.exception("health poll failed")
            if now - last_system >= self.system_interval_s:
                last_system = now
                try:
                    self.system.emit(c.monitor.sample())
                except Exception:
                    pass
            dt = 1.0 / max(0.5, float(self.refresh_hz))
            remaining = dt - (time.time() - t0)
            if remaining > 0:
                self._stop.wait(remaining)
