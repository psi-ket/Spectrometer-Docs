"""Synchronization state derivation and reference-clock configuration."""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any, Optional

from swabian_backend.devices import ConnectionType, TaggerHandle
from swabian_backend import synchronization as S

from ..models.sync_state import SyncState

log = logging.getLogger("snspec.sync")


@dataclass
class ReferenceClockConfig:
    clock_channel: Optional[int] = None
    clock_frequency_hz: float = 10e6
    time_constant_s: float = 1e-3
    synchronization_channel: Optional[int] = None
    synchronization_offset_ps: int = 0
    wait_until_locked: bool = True
    enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ReferenceClockConfig":
        allowed = set(ReferenceClockConfig.__dataclass_fields__)
        return ReferenceClockConfig(**{k: v for k, v in d.items() if k in allowed})


class SyncManager:
    def __init__(self):
        self.configs: dict[str, ReferenceClockConfig] = {}
        self.overrides: dict[str, SyncState] = {}

    def reference_clock_state(self, handle: TaggerHandle) -> dict[str, Any]:
        if not handle.capabilities.get("reference_clock"):
            return {}
        if handle.connection_type == ConnectionType.NETWORK and len(handle.sub_devices) > 1 and hasattr(handle.tagger, "getServers"):
            states = {}
            try:
                for srv in handle.tagger.getServers():
                    states[str(srv.getAddress())] = S.reference_clock_state(srv)
            except Exception as exc:
                states["error"] = str(exc)
            return states
        return S.reference_clock_state(handle.tagger)

    def compute_state(self, handle: TaggerHandle) -> tuple[SyncState, str]:
        """Derive the synchronization state for cross-channel correlations on this handle."""
        if handle.id in self.overrides:
            return self.overrides[handle.id], "manual override"
        ct = handle.connection_type
        if ct in (ConnectionType.VIRTUAL_REPLAY, ConnectionType.SIMULATOR):
            return SyncState.SYNCED, "single recorded/simulated time base"
        if ct == ConnectionType.SYNCHRONIZED_MULTI_TAGGER:
            return SyncState.SYNCED, "Swabian Synchronizer group (common hardware time base)"
        rc = self.reference_clock_state(handle)
        if ct == ConnectionType.NETWORK and len(handle.sub_devices) > 1:
            locked = []
            for addr, st in rc.items():
                if isinstance(st, dict) and st.get("enabled"):
                    locked.append(bool(st.get("is_locked")))
            if locked and all(locked) and len(locked) == len(handle.sub_devices):
                return SyncState.REFERENCE_CLOCK_LOCKED, "all servers report a locked reference clock"
            if locked and not all(locked):
                return SyncState.SYNC_ERROR, "at least one server's reference clock is not locked"
            return SyncState.SYNC_REQUIRED, "multiple servers: the library requires synchronized servers; reference clock state not confirmed"
        if isinstance(rc, dict) and rc.get("enabled"):
            if rc.get("is_locked"):
                return SyncState.REFERENCE_CLOCK_LOCKED, "reference clock locked"
            return SyncState.SYNCING, "reference clock enabled but not locked"
        return SyncState.SYNCED, "single device time base"

    def apply_reference_clock(self, handle: TaggerHandle, cfg: ReferenceClockConfig) -> dict[str, Any]:
        if not handle.capabilities.get("reference_clock"):
            raise RuntimeError("Reference clock is not supported on this device type")
        if cfg.enabled:
            if cfg.clock_channel is None:
                raise ValueError("A clock channel is required")
            log.warning("Applying reference clock on %s: channel %s, %.6g Hz. The time base changes; data before/after are not comparable.", handle.id, cfg.clock_channel, cfg.clock_frequency_hz)
            S.set_reference_clock(handle.tagger, int(cfg.clock_channel), float(cfg.clock_frequency_hz), float(cfg.time_constant_s), cfg.synchronization_channel, int(cfg.synchronization_offset_ps), bool(cfg.wait_until_locked))
        else:
            log.warning("Disabling reference clock on %s", handle.id)
            S.disable_reference_clock(handle.tagger)
        self.configs[handle.id] = cfg
        state = S.reference_clock_state(handle.tagger)
        log.info("Reference clock state on %s: %s", handle.id, state)
        return state

    def info(self, handle: TaggerHandle) -> dict[str, Any]:
        state, reason = self.compute_state(handle)
        return {"sync_state": state.value, "reason": reason, "connection_type": handle.connection_type.value, "sub_devices": len(handle.sub_devices), "reference_clock": self.reference_clock_state(handle), "config": self.configs.get(handle.id, ReferenceClockConfig()).to_dict()}
