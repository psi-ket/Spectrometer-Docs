"""Explicit units. Internal base units: time -> ps, voltage -> V, wavelength -> nm, frequency -> Hz."""
from __future__ import annotations

import math

PS_PER_NS = 1_000
PS_PER_US = 1_000_000
PS_PER_MS = 1_000_000_000
PS_PER_S = 1_000_000_000_000

TIME_UNITS: dict[str, float] = {"ps": 1.0, "ns": PS_PER_NS, "µs": PS_PER_US, "ms": PS_PER_MS, "s": PS_PER_S}
FREQ_UNITS: dict[str, float] = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}
VOLT_UNITS: dict[str, float] = {"V": 1.0, "mV": 1e-3}


def s_to_ps(seconds: float) -> int:
    return int(round(float(seconds) * PS_PER_S))


def ps_to_s(ps: float) -> float:
    return float(ps) / PS_PER_S


def to_ps(value: float, unit: str) -> int:
    return int(round(float(value) * TIME_UNITS[unit]))


def from_ps(ps: float, unit: str) -> float:
    return float(ps) / TIME_UNITS[unit]


def format_time_ps(ps: float, digits: int = 3) -> str:
    """Human readable time with an automatically chosen unit."""
    if ps is None or (isinstance(ps, float) and math.isnan(ps)):
        return "n/a"
    a = abs(float(ps))
    for unit in ("s", "ms", "µs", "ns"):
        if a >= TIME_UNITS[unit]:
            return f"{float(ps) / TIME_UNITS[unit]:.{digits}g} {unit}"
    return f"{float(ps):.0f} ps"


def format_rate(cps: float, digits: int = 3) -> str:
    if cps is None or (isinstance(cps, float) and math.isnan(cps)):
        return "n/a"
    a = abs(float(cps))
    if a >= 1e9:
        return f"{cps / 1e9:.{digits}g} Gcps"
    if a >= 1e6:
        return f"{cps / 1e6:.{digits}g} Mcps"
    if a >= 1e3:
        return f"{cps / 1e3:.{digits}g} kcps"
    return f"{cps:.{digits}g} cps"


def format_frequency(hz: float, digits: int = 4) -> str:
    a = abs(float(hz))
    for unit in ("GHz", "MHz", "kHz"):
        if a >= FREQ_UNITS[unit]:
            return f"{hz / FREQ_UNITS[unit]:.{digits}g} {unit}"
    return f"{hz:.{digits}g} Hz"


def format_voltage(v: float) -> str:
    if v is None:
        return "n/a"
    if abs(v) < 1.0:
        return f"{v * 1e3:.1f} mV"
    return f"{v:.3f} V"


def format_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
