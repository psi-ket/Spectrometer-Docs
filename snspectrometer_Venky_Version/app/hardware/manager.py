"""Hardware manager: discovery, connections, channel configuration and health.

Pure Python (no Qt). The controller wraps it and marshals events to the GUI.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from swabian_backend import get_api
from swabian_backend import devices as D
from swabian_backend import configuration as C

from ..models.channels import DetectorChannel
from ..models.sync_state import DeviceState

log = logging.getLogger("snspec.hardware")


@dataclass
class DeviceRecord:
    handle: D.TaggerHandle
    state: DeviceState = DeviceState.CONNECTED
    last_error: str = ""
    channel_states: dict[int, C.ChannelHardwareState] = field(default_factory=dict)
    connected_at: float = field(default_factory=time.time)
    last_overflows: Optional[int] = None
    overflow_events: int = 0

    @property
    def id(self) -> str:
        return self.handle.id


class HardwareManager:
    def __init__(self):
        self.devices: dict[str, DeviceRecord] = {}
        self.discovered: list[D.DiscoveredDevice] = []
        self.servers: list[str] = []
        self.discovery_errors: list[str] = []
        self.listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._lock = threading.RLock()
        self.change_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ events
    def _emit(self, event: str, **payload: Any) -> None:
        for cb in list(self.listeners):
            try:
                cb(event, payload)
            except Exception:
                log.exception("hardware listener failed")

    # ------------------------------------------------------------------ discovery
    def discover(self, scan_network: bool = True) -> dict[str, Any]:
        api = get_api()
        self.discovery_errors = []
        self.discovered = []
        self.servers = []
        if not api.available:
            self.discovery_errors.append("Swabian library unavailable: " + api.error)
        else:
            try:
                self.discovered = D.scan_local_devices()
            except Exception as exc:
                log.exception("scanTimeTagger failed")
                self.discovery_errors.append(f"scanTimeTagger failed: {exc}")
            if scan_network:
                try:
                    self.servers = D.scan_network_servers()
                except Exception as exc:
                    log.exception("scanTimeTaggerServers failed")
                    self.discovery_errors.append(f"scanTimeTaggerServers failed: {exc}")
        result = {"local": [d.to_dict() for d in self.discovered], "servers": list(self.servers), "errors": list(self.discovery_errors), "library": api.diagnostics()}
        log.info("Discovery: %d local devices, %d network servers", len(self.discovered), len(self.servers))
        self._emit("discovered", **result)
        return result

    # ------------------------------------------------------------------ connections
    def _register(self, handle: D.TaggerHandle) -> DeviceRecord:
        rec = DeviceRecord(handle=handle)
        with self._lock:
            self.devices[handle.id] = rec
        try:
            rec.channel_states = C.read_all_channel_states(handle)
            rec.state = DeviceState.CONFIGURED if rec.channel_states else DeviceState.CONNECTED
        except Exception as exc:
            log.warning("reading channel states failed: %s", exc)
        rec.last_overflows = handle.overflows()
        log.info("Device connected: %s (%s %s) channels=%s", handle.id, handle.model, handle.serial, handle.channels_rising)
        self._emit("connected", device_id=handle.id, description=handle.describe())
        return rec

    def connect_local(self, serial: str = "", resolution: Optional[str] = None) -> DeviceRecord:
        self._emit("connecting", device_id=f"TT:{serial or 'first'}")
        try:
            handle = D.open_local(serial, resolution)
        except Exception as exc:
            log.exception("createTimeTagger failed")
            self._emit("error", device_id=f"TT:{serial}", error=str(exc))
            raise
        return self._register(handle)

    def connect_network(self, addresses: list[str]) -> DeviceRecord:
        self._emit("connecting", device_id="NET:" + "+".join(addresses))
        try:
            handle = D.open_network(addresses)
        except Exception as exc:
            log.exception("createTimeTaggerNetwork failed")
            self._emit("error", device_id="NET", error=str(exc))
            raise
        return self._register(handle)

    def register_handle(self, handle: D.TaggerHandle) -> DeviceRecord:
        """Register an externally created handle (replay source, simulator)."""
        return self._register(handle)

    def disconnect(self, device_id: str) -> None:
        with self._lock:
            rec = self.devices.pop(device_id, None)
        if rec is None:
            return
        rec.state = DeviceState.DISCONNECTING
        self._emit("disconnecting", device_id=device_id)
        try:
            rec.handle.close()
        finally:
            rec.state = DeviceState.DISCONNECTED
            log.info("Device disconnected: %s", device_id)
            self._emit("disconnected", device_id=device_id)

    def disconnect_all(self) -> None:
        for dev_id in list(self.devices):
            try:
                self.disconnect(dev_id)
            except Exception:
                log.exception("disconnect failed for %s", dev_id)

    def get(self, device_id: str) -> Optional[DeviceRecord]:
        return self.devices.get(device_id)

    def handle(self, device_id: str) -> D.TaggerHandle:
        rec = self.devices.get(device_id)
        if rec is None:
            raise KeyError(f"Device {device_id} is not connected")
        return rec.handle

    def set_state(self, device_id: str, state: DeviceState, error: str = "") -> None:
        rec = self.devices.get(device_id)
        if rec is None:
            return
        rec.state = state
        if error:
            rec.last_error = error
        self._emit("state", device_id=device_id, state=state.value, error=error)

    # ------------------------------------------------------------------ configuration
    def read_channel_states(self, device_id: str) -> dict[int, C.ChannelHardwareState]:
        rec = self.devices[device_id]
        rec.channel_states = C.read_all_channel_states(rec.handle)
        return rec.channel_states

    def apply_detector_channel(self, dc: DetectorChannel, fields: Optional[set[str]] = None) -> list[C.AppliedChange]:
        """Push the requested hardware fields of a DetectorChannel to the device and log the changes."""
        rec = self.devices.get(dc.tagger_id)
        if rec is None:
            raise KeyError(f"Device {dc.tagger_id} is not connected")
        want = fields or {"trigger_level_v", "impedance", "input_delay_ps", "deadtime_ps", "event_divider"}
        kwargs: dict[str, Any] = {}
        if "trigger_level_v" in want and dc.trigger_level_v is not None:
            kwargs["trigger_level_v"] = dc.trigger_level_v
        if "impedance" in want and dc.impedance is not None:
            kwargs["impedance"] = dc.impedance
        if "input_delay_ps" in want:
            kwargs["input_delay_ps"] = int(dc.input_delay_ps)
        if "deadtime_ps" in want and dc.deadtime_ps is not None:
            kwargs["deadtime_ps"] = int(dc.deadtime_ps)
        if "event_divider" in want and dc.event_divider:
            kwargs["event_divider"] = int(dc.event_divider)
        state, changes = C.apply_channel_settings(rec.handle, dc.physical_channel, **kwargs)
        rec.channel_states[dc.physical_channel] = state
        for ch in changes:
            entry = {"time": time.time(), "device": dc.tagger_id, **ch.to_dict()}
            self.change_log.append(entry)
            if ch.ok:
                log.info("Hardware change %s ch %s %s: requested %s applied %s", dc.tagger_id, ch.channel, ch.field, ch.requested, ch.applied)
            else:
                log.error("Hardware change failed %s ch %s %s: %s", dc.tagger_id, ch.channel, ch.field, ch.error)
        self._emit("channel_configured", device_id=dc.tagger_id, channel=dc.physical_channel, state=state.to_dict(), changes=[c.to_dict() for c in changes])
        return changes

    def set_test_signal(self, device_id: str, channel: int, enabled: bool) -> list[C.AppliedChange]:
        rec = self.devices[device_id]
        state, changes = C.apply_channel_settings(rec.handle, channel, test_signal=enabled)
        rec.channel_states[channel] = state
        return changes

    def set_hardware_delay_compensation(self, device_id: str, active: bool) -> None:
        rec = self.devices[device_id]
        C.set_hardware_delay_compensation(rec.handle, active)
        log.info("Hardware delay compensation on %s set to %s", device_id, active)
        self.read_channel_states(device_id)

    # ------------------------------------------------------------------ health
    def poll_health(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for dev_id, rec in list(self.devices.items()):
            h = rec.handle
            info: dict[str, Any] = {"state": rec.state.value, "error": rec.last_error}
            if h.closed or h.tagger is None:
                rec.state = DeviceState.DISCONNECTED
                info["state"] = rec.state.value
                out[dev_id] = info
                continue
            try:
                ov = h.overflows()
                info["overflows"] = ov
                if ov is not None and rec.last_overflows is not None and ov > rec.last_overflows:
                    rec.overflow_events += ov - rec.last_overflows
                    if h.connection_type.value == "SIMULATOR":
                        log.info("Overflow marker on %s: counter %s -> %s (expected at dataset loop boundaries of the simulated device)", dev_id, rec.last_overflows, ov)
                    else:
                        log.warning("Overflow on %s: counter %s -> %s", dev_id, rec.last_overflows, ov)
                    self._emit("overflow", device_id=dev_id, overflows=ov)
                rec.last_overflows = ov
                cov = h.client_overflows()
                if cov is not None:
                    info["client_overflows"] = cov
                if hasattr(h.tagger, "isConnected"):
                    connected = bool(h.tagger.isConnected())
                    info["connected"] = connected
                    if not connected and rec.state != DeviceState.ERROR:
                        rec.state = DeviceState.ERROR
                        rec.last_error = "network connection lost"
                        self._emit("error", device_id=dev_id, error=rec.last_error)
            except Exception as exc:
                rec.state = DeviceState.ERROR
                rec.last_error = str(exc)
                log.error("Health poll failed for %s: %s", dev_id, exc)
                self._emit("error", device_id=dev_id, error=str(exc))
            info["state"] = rec.state.value
            info["overflow_events"] = rec.overflow_events
            out[dev_id] = info
        return out

    def clear_overflows(self, device_id: str) -> None:
        rec = self.devices[device_id]
        rec.handle.clear_overflows()
        rec.last_overflows = rec.handle.overflows()
