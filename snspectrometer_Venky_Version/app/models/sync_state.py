"""State enumerations shared across layers."""
from __future__ import annotations

from enum import Enum


class SyncState(str, Enum):
    UNSYNCED = "UNSYNCED"
    SYNC_REQUIRED = "SYNC_REQUIRED"
    SYNCING = "SYNCING"
    SYNCED = "SYNCED"
    SYNC_ERROR = "SYNC_ERROR"
    REFERENCE_CLOCK_LOCKED = "REFERENCE_CLOCK_LOCKED"

    @property
    def allows_cross_device(self) -> bool:
        return self in (SyncState.SYNCED, SyncState.REFERENCE_CLOCK_LOCKED)


class DeviceState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    DETECTED = "DETECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    CONFIGURED = "CONFIGURED"
    ACQUIRING = "ACQUIRING"
    ERROR = "ERROR"
    DISCONNECTING = "DISCONNECTING"


class RunStatus(str, Enum):
    PENDING = "PENDING"
    PREPARED = "PREPARED"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"

    @property
    def is_active(self) -> bool:
        return self in (RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.ARMED)


class RawRecordingPolicy(str, Enum):
    ALWAYS = "ALWAYS"
    ASK = "ASK"
    NEVER = "NEVER"


class AppMode(str, Enum):
    BASIC = "BASIC"
    ADVANCED = "ADVANCED"
    DEVELOPER = "DEVELOPER"
