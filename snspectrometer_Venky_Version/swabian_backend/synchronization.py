"""``SynchronizedMeasurements`` and software-defined reference clock wrappers."""
from __future__ import annotations

import logging
from typing import Any, Optional

from .api import get_api

log = logging.getLogger("snspec.backend.sync")


class SyncGroup:
    """Wraps one ``SynchronizedMeasurements`` instance.

    Measurements created with :attr:`proxy` as their tagger argument are
    registered automatically and do not auto-start (manual 5.5.7).
    """

    def __init__(self, tagger: Any):
        TT = get_api().require()
        self.sm = TT.SynchronizedMeasurements(tagger)
        self.proxy = self.sm.getTagger()
        self.tagger = tagger

    def register(self, measurement: Any) -> None:
        objs = getattr(measurement, "objects", None)
        for o in (objs if objs is not None else [measurement]):
            self.sm.registerMeasurement(o)

    def unregister(self, measurement: Any) -> None:
        objs = getattr(measurement, "objects", None)
        for o in (objs if objs is not None else [measurement]):
            try:
                self.sm.unregisterMeasurement(o)
            except Exception:
                pass

    def start(self) -> None:
        self.sm.start()

    def start_for(self, duration_ps: int, clear: bool = True) -> None:
        if int(duration_ps) <= 0:
            raise ValueError("Duration must be positive")
        self.sm.startFor(int(duration_ps), bool(clear))

    def stop(self) -> None:
        self.sm.stop()

    def clear(self) -> None:
        fn = getattr(self.sm, "clear", None)
        if fn is None:
            raise RuntimeError("SynchronizedMeasurements.clear() is not available in this library version")
        fn()

    def is_running(self) -> bool:
        return bool(self.sm.isRunning())

    def wait_until_finished(self, timeout_ms: int = -1) -> bool:
        return bool(self.sm.waitUntilFinished(int(timeout_ms)))


# --------------------------------------------------------------------------- reference clock
REFERENCE_CLOCK_FIELDS = (
    "clock_period", "clock_channel", "synchronization_channel", "ideal_clock_channel", "averaging_periods",
    "synchronization_offset", "enabled", "event_divider", "is_locked", "is_synchronized", "error_counter",
    "last_ideal_clock_event", "period_error", "phase_error_estimation",
)


def set_reference_clock(tagger: Any, clock_channel: int, clock_frequency_hz: float = 10e6, time_constant_s: float = 1e-3, synchronization_channel: Optional[int] = None, synchronization_offset_ps: int = 0, wait_until_locked: bool = True) -> None:
    """``setReferenceClock(clock_channel, clock_frequency, time_constant, synchronization_channel, synchronization_offset, wait_until_locked)``.

    The manual warns that the time base is invalid until the PLL locks and that
    overflows are expected right after this call.
    """
    api = get_api()
    sync_ch = api.CHANNEL_UNUSED if synchronization_channel is None else int(synchronization_channel)
    if float(clock_frequency_hz) <= 0:
        raise ValueError("Clock frequency must be positive")
    if float(time_constant_s) <= 0:
        raise ValueError("PLL time constant must be positive")
    tagger.setReferenceClock(int(clock_channel), float(clock_frequency_hz), float(time_constant_s), sync_ch, int(synchronization_offset_ps), bool(wait_until_locked))


def disable_reference_clock(tagger: Any) -> None:
    tagger.disableReferenceClock()


def reference_clock_state(tagger: Any) -> dict[str, Any]:
    """``getReferenceClockState()`` flattened to a dict; missing fields are omitted."""
    out: dict[str, Any] = {}
    try:
        st = tagger.getReferenceClockState()
    except Exception as exc:
        return {"error": str(exc)}
    for name in REFERENCE_CLOCK_FIELDS:
        if hasattr(st, name):
            try:
                v = getattr(st, name)
                out[name] = v if isinstance(v, (int, float, bool, str)) else str(v)
            except Exception:
                pass
    return out
