"""Factories for Swabian virtual channels (manual chapter 5.4).

Virtual channels are software-defined: the manual states explicitly that
coincidence processing happens in the library, not on the FPGA.
"""
from __future__ import annotations

import itertools
import logging
from typing import Any, Optional, Sequence

from .api import get_api

log = logging.getLogger("snspec.backend.vchannels")

COINCIDENCE_TIMESTAMP_MODES = ("Last", "Average", "First", "ListedFirst")


def _timestamp_mode(TT: Any, mode: str) -> Any:
    if mode not in COINCIDENCE_TIMESTAMP_MODES:
        raise ValueError(f"Unknown coincidence timestamp mode {mode!r}")
    enum_cls = getattr(TT, "CoincidenceTimestamp", None)
    if enum_cls is not None and hasattr(enum_cls, mode):
        return getattr(enum_cls, mode)
    return getattr(TT, f"CoincidenceTimestamp_{mode}")


def _gate_initial(TT: Any, initial: str) -> Any:
    enum_cls = getattr(TT, "GatedChannelInitial", None)
    if enum_cls is not None and hasattr(enum_cls, initial):
        return getattr(enum_cls, initial)
    return getattr(TT, f"GatedChannelInitial_{initial}")


def make_coincidences(tagger: Any, groups: Sequence[Sequence[int]], window_ps: int, timestamp: str = "Last") -> tuple[Any, list[int]]:
    """``Coincidences(tagger, coincidenceGroups, coincidenceWindow, timestamp)`` -> (object, virtual channels)."""
    TT = get_api().require()
    if int(window_ps) <= 0:
        raise ValueError("Coincidence window must be positive (ps)")
    grp = [[int(c) for c in g] for g in groups]
    if not grp:
        raise ValueError("No coincidence groups given")
    for g in grp:
        if len(g) < 2:
            raise ValueError("Each coincidence group needs at least two channels")
        if len(set(g)) != len(g):
            raise ValueError(f"Coincidence group {g} contains a repeated channel")
    unique = {c for g in grp for c in g}
    if len(unique) > 64:
        raise ValueError("Coincidences supports at most 64 unique channels (manual 5.4.4)")
    obj = TT.Coincidences(tagger, grp, int(window_ps), _timestamp_mode(TT, timestamp))
    return obj, [int(c) for c in obj.getChannels()]


def make_coincidence(tagger: Any, channels: Sequence[int], window_ps: int, timestamp: str = "Last") -> tuple[Any, int]:
    TT = get_api().require()
    chans = [int(c) for c in channels]
    if len(chans) < 2:
        raise ValueError("A coincidence needs at least two channels")
    obj = TT.Coincidence(tagger, chans, int(window_ps), _timestamp_mode(TT, timestamp))
    return obj, int(obj.getChannel())


def make_combinations(tagger: Any, channels: Sequence[int], window_ps: int) -> Any:
    """``Combinations(tagger, channels, window_size)``. Virtual channels are only enabled on request."""
    TT = get_api().require()
    if not hasattr(TT, "Combinations"):
        raise RuntimeError("Combinations is not available in the installed library")
    return TT.Combinations(tagger, [int(c) for c in channels], int(window_ps))


def make_delayed_channels(tagger: Any, channels: Sequence[int], delay_ps: int) -> tuple[Any, list[int]]:
    TT = get_api().require()
    obj = TT.DelayedChannels(tagger, [int(c) for c in channels], int(delay_ps))
    return obj, [int(c) for c in obj.getChannels()]


def make_gated_channels(tagger: Any, input_channels: Sequence[int], gate_start: int, gate_stop: int, initial: str = "Closed") -> tuple[Any, list[int]]:
    TT = get_api().require()
    if int(gate_start) == int(gate_stop):
        raise ValueError("gate_start_channel and gate_stop_channel must differ")
    chans = [int(c) for c in input_channels]
    if hasattr(TT, "GatedChannels"):
        obj = TT.GatedChannels(tagger, chans, int(gate_start), int(gate_stop), _gate_initial(TT, initial))
        return obj, [int(c) for c in obj.getChannels()]
    # older libraries (e.g. 2.21) only provide the single-channel GatedChannel
    objs = [TT.GatedChannel(tagger, c, int(gate_start), int(gate_stop), _gate_initial(TT, initial)) for c in chans]
    return objs, [int(o.getChannel()) for o in objs]


def make_combiner(tagger: Any, channels: Sequence[int]) -> tuple[Any, int]:
    TT = get_api().require()
    obj = TT.Combiner(tagger, [int(c) for c in channels])
    return obj, int(obj.getChannel())


# --------------------------------------------------------------------------- planning helpers
def coincidence_groups(channels: Sequence[int], order: int, include: Optional[Sequence[int]] = None, exclude: Optional[Sequence[int]] = None, max_groups: Optional[int] = None) -> list[tuple[int, ...]]:
    """All ``order``-fold combinations of ``channels`` with optional inclusion/exclusion filters."""
    chans = [int(c) for c in channels]
    if order < 2:
        raise ValueError("Coincidence order must be at least 2")
    if order > len(chans):
        raise ValueError(f"Coincidence order {order} exceeds the number of channels ({len(chans)})")
    inc = set(int(c) for c in include) if include else set()
    exc = set(int(c) for c in exclude) if exclude else set()
    out = []
    for combo in itertools.combinations(chans, order):
        s = set(combo)
        if inc and not inc.issubset(s):
            continue
        if exc and (s & exc):
            continue
        out.append(tuple(combo))
        if max_groups is not None and len(out) >= max_groups:
            break
    return out


def count_coincidence_groups(n_channels: int, order: int) -> int:
    from math import comb
    return comb(n_channels, order) if 0 <= order <= n_channels else 0


def count_combinations(n_channels: int) -> int:
    """Number of virtual channels ``Combinations`` can expose: 2^N - 1 (manual 5.4.5)."""
    return (1 << int(n_channels)) - 1
