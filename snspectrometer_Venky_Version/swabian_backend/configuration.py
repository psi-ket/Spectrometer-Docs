"""Per-channel hardware configuration with read-back.

All setters are followed by a read-back of the actually applied value, as the
manual notes that e.g. the trigger level returned by ``getTriggerLevel`` may
differ from the requested value due to DAC discretisation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .devices import TaggerHandle

log = logging.getLogger("snspec.backend.config")

IMPEDANCE_50 = "50_OHM"
IMPEDANCE_HIGH = "HIGH_Z"


@dataclass
class ChannelHardwareState:
    channel: int
    trigger_level_v: Optional[float] = None
    trigger_level_range_v: Optional[tuple[float, float]] = None
    impedance: Optional[str] = None
    input_delay_ps: Optional[int] = None
    deadtime_ps: Optional[int] = None
    deadtime_range_ps: Optional[tuple[int, int]] = None
    event_divider: Optional[int] = None
    hardware_delay_compensation_ps: Optional[int] = None
    hysteresis_mv: Optional[int] = None
    test_signal: Optional[bool] = None
    unsupported: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class AppliedChange:
    channel: int
    field: str
    requested: Any
    applied: Any
    ok: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _try(state: ChannelHardwareState, name: str, fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        msg = str(exc)
        state.errors[name] = msg
        if "not supported" in msg.lower() or "not available" in msg.lower() or "only" in msg.lower():
            state.unsupported.append(name)
        return None


def read_channel_state(handle: TaggerHandle, channel: int) -> ChannelHardwareState:
    st = ChannelHardwareState(channel=channel)
    target, local = handle.hardware_target(channel)
    caps = handle.capabilities
    if caps.get("trigger_level"):
        v = _try(st, "trigger_level", target.getTriggerLevel, local)
        st.trigger_level_v = float(v) if v is not None else None
        rng = None
        if hasattr(target, "getTriggerLevelRange"):
            rng = _try(st, "trigger_level_range", target.getTriggerLevelRange, local)
        if rng is None and hasattr(target, "getDACRange"):
            rng = _try(st, "trigger_level_range", target.getDACRange)
        if rng is not None:
            try:
                st.trigger_level_range_v = (float(rng[0]), float(rng[1]))
            except Exception:
                pass
    else:
        st.unsupported.append("trigger_level")
    if caps.get("impedance"):
        hz = _try(st, "impedance", target.getInputImpedanceHigh, local)
        st.impedance = None if hz is None else (IMPEDANCE_HIGH if hz else IMPEDANCE_50)
    else:
        st.unsupported.append("impedance")
    if caps.get("input_delay"):
        d = _try(st, "input_delay", handle.tagger.getInputDelay, channel)
        st.input_delay_ps = int(d) if d is not None else None
    if caps.get("deadtime"):
        d = _try(st, "deadtime", handle.tagger.getDeadtime, channel)
        st.deadtime_ps = int(d) if d is not None else None
        rng = _try(st, "deadtime_range", handle.tagger.getDeadtimeRange, channel)
        if rng is not None:
            try:
                st.deadtime_range_ps = (int(rng[0]), int(rng[1]))
            except Exception:
                pass
    if caps.get("event_divider"):
        d = _try(st, "event_divider", handle.tagger.getEventDivider, channel)
        st.event_divider = int(d) if d is not None else None
    else:
        st.unsupported.append("event_divider")
    if caps.get("hardware_delay_compensation"):
        d = _try(st, "hardware_delay_compensation", target.getHardwareDelayCompensation, local)
        st.hardware_delay_compensation_ps = int(d) if d is not None else None
    if caps.get("hysteresis"):
        d = _try(st, "hysteresis", target.getInputHysteresis, local)
        st.hysteresis_mv = int(d) if d is not None else None
    if caps.get("test_signal"):
        d = _try(st, "test_signal", target.getTestSignal, local)
        st.test_signal = bool(d) if d is not None else None
    return st


def apply_channel_settings(
    handle: TaggerHandle,
    channel: int,
    trigger_level_v: Optional[float] = None,
    impedance: Optional[str] = None,
    input_delay_ps: Optional[int] = None,
    deadtime_ps: Optional[int] = None,
    event_divider: Optional[int] = None,
    test_signal: Optional[bool] = None,
) -> tuple[ChannelHardwareState, list[AppliedChange]]:
    """Apply the given (non-None) settings, then read back the real state.

    Validation against device ranges happens here so no invalid value reaches the
    hardware. Every change is returned as an :class:`AppliedChange` for logging.
    """
    changes: list[AppliedChange] = []
    target, local = handle.hardware_target(channel)
    caps = handle.capabilities
    t = handle.tagger

    def record(name, requested, fn, readback):
        try:
            fn()
            applied = readback()
            changes.append(AppliedChange(channel, name, requested, applied, True))
        except Exception as exc:
            changes.append(AppliedChange(channel, name, requested, None, False, str(exc)))
            log.error("Failed to set %s on channel %s: %s", name, channel, exc)

    if trigger_level_v is not None:
        if not caps.get("trigger_level"):
            changes.append(AppliedChange(channel, "trigger_level", trigger_level_v, None, False, "unsupported on this device"))
        else:
            rng = None
            try:
                rng = target.getTriggerLevelRange(local) if hasattr(target, "getTriggerLevelRange") else target.getDACRange()
            except Exception:
                rng = None
            if rng is not None and not (float(rng[0]) <= float(trigger_level_v) <= float(rng[1])):
                changes.append(AppliedChange(channel, "trigger_level", trigger_level_v, None, False, f"outside device range {float(rng[0]):.3f}..{float(rng[1]):.3f} V"))
            else:
                record("trigger_level", float(trigger_level_v), lambda: target.setTriggerLevel(local, float(trigger_level_v)), lambda: float(target.getTriggerLevel(local)))
    if impedance is not None:
        if not caps.get("impedance"):
            changes.append(AppliedChange(channel, "impedance", impedance, None, False, "unsupported on this device (Time Tagger X only)"))
        elif impedance not in (IMPEDANCE_50, IMPEDANCE_HIGH):
            changes.append(AppliedChange(channel, "impedance", impedance, None, False, "invalid impedance state"))
        else:
            hz = impedance == IMPEDANCE_HIGH
            record("impedance", impedance, lambda: target.setInputImpedanceHigh(local, hz), lambda: IMPEDANCE_HIGH if target.getInputImpedanceHigh(local) else IMPEDANCE_50)
    if input_delay_ps is not None:
        if not caps.get("input_delay"):
            changes.append(AppliedChange(channel, "input_delay", input_delay_ps, None, False, "unsupported"))
        else:
            record("input_delay", int(input_delay_ps), lambda: t.setInputDelay(channel, int(input_delay_ps)), lambda: int(t.getInputDelay(channel)))
    if deadtime_ps is not None:
        if not caps.get("deadtime"):
            changes.append(AppliedChange(channel, "deadtime", deadtime_ps, None, False, "unsupported"))
        else:
            rng = None
            try:
                rng = t.getDeadtimeRange(channel)
            except Exception:
                rng = None
            if rng is not None and not (int(rng[0]) <= int(deadtime_ps) <= int(rng[1])):
                changes.append(AppliedChange(channel, "deadtime", deadtime_ps, None, False, f"outside device range {int(rng[0])}..{int(rng[1])} ps"))
            elif int(deadtime_ps) < 0:
                changes.append(AppliedChange(channel, "deadtime", deadtime_ps, None, False, "negative dead time"))
            else:
                record("deadtime", int(deadtime_ps), lambda: t.setDeadtime(channel, int(deadtime_ps)), lambda: int(t.getDeadtime(channel)))
    if event_divider is not None:
        if not caps.get("event_divider"):
            changes.append(AppliedChange(channel, "event_divider", event_divider, None, False, "unsupported on this device (TimeTaggerVirtual has no event divider)"))
        elif not (1 <= int(event_divider) <= 65535):
            changes.append(AppliedChange(channel, "event_divider", event_divider, None, False, "divider must be 1..65535"))
        else:
            record("event_divider", int(event_divider), lambda: t.setEventDivider(channel, int(event_divider)), lambda: int(t.getEventDivider(channel)))
    if test_signal is not None:
        if not caps.get("test_signal"):
            changes.append(AppliedChange(channel, "test_signal", test_signal, None, False, "unsupported"))
        else:
            record("test_signal", bool(test_signal), lambda: target.setTestSignal(local, bool(test_signal)), lambda: bool(target.getTestSignal(local)))
    return read_channel_state(handle, channel), changes


def set_hardware_delay_compensation(handle: TaggerHandle, active: bool) -> bool:
    if not handle.capabilities.get("hardware_delay_compensation"):
        raise RuntimeError("Hardware delay compensation is not available on this device")
    handle.tagger.setHardwareDelayCompensationActive(bool(active))
    return True


def read_all_channel_states(handle: TaggerHandle) -> dict[int, ChannelHardwareState]:
    return {ch: read_channel_state(handle, ch) for ch in handle.channels_rising}
