"""Device discovery and Time Tagger object lifecycle.

Wraps ``scanTimeTagger``, ``scanTimeTaggerServers``, ``createTimeTagger``,
``createTimeTaggerNetwork``, ``createTimeTaggerVirtual`` and ``freeTimeTagger``.

A :class:`TaggerHandle` owns exactly one Swabian ``TimeTaggerBase`` object. For a
Synchronizer group or a multi-server network client that single object exposes
several physical devices; those are listed as :class:`SubDevice` entries with the
channel mapping the library reports.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .api import get_api

log = logging.getLogger("snspec.backend.devices")


class ConnectionType(str, Enum):
    LOCAL_USB = "LOCAL_USB"
    NETWORK = "NETWORK"
    VIRTUAL_REPLAY = "VIRTUAL_REPLAY"
    SYNCHRONIZED_MULTI_TAGGER = "SYNCHRONIZED_MULTI_TAGGER"
    SIMULATOR = "SIMULATOR"


@dataclass
class DiscoveredDevice:
    serial: str
    model: str
    connection_type: ConnectionType
    address: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "serial": self.serial,
            "model": self.model,
            "connection_type": self.connection_type.value,
            "address": self.address,
            "note": self.note,
        }


@dataclass
class SubDevice:
    """One physical device inside a handle (network server or Synchronizer member)."""

    index: int
    serial: str = ""
    model: str = ""
    address: str = ""
    channels: list[int] = field(default_factory=list)
    #: mapping local (server-side) channel -> channel number seen on the handle
    channel_map: dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "serial": self.serial,
            "model": self.model,
            "address": self.address,
            "channels": list(self.channels),
            "channel_map": {str(k): v for k, v in self.channel_map.items()},
        }


# --------------------------------------------------------------------------- discovery
def scan_local_devices() -> list[DiscoveredDevice]:
    """``scanTimeTagger(include_model_name=True)`` -> parsed device list.

    The manual notes the list may include devices blocked by other processes.
    """
    api = get_api()
    TT = api.require()
    out: list[DiscoveredDevice] = []
    raw = TT.scanTimeTagger(True)
    for entry in raw:
        text = str(entry)
        if "," in text:
            serial, model = text.split(",", 1)
        else:
            serial, model = text, ""
        out.append(DiscoveredDevice(serial=serial.strip(), model=model.strip(), connection_type=ConnectionType.LOCAL_USB))
    return out


def scan_network_servers() -> list[str]:
    """``scanTimeTaggerServers()`` (multicast UDP discovery; may be blocked by firewalls)."""
    TT = get_api().require()
    return [str(a) for a in TT.scanTimeTaggerServers()]


def server_info(address: str) -> dict[str, Any]:
    """``getTimeTaggerServerInfo(address)`` parsed into a dict."""
    TT = get_api().require()
    raw = TT.getTimeTaggerServerInfo(address)
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": str(raw)}


# --------------------------------------------------------------------------- handle
def _edge_constant(TT: Any, name: str) -> Any:
    """Return the ChannelEdge constant for ``name`` in whichever spelling the module offers."""
    enum_cls = getattr(TT, "ChannelEdge", None)
    if enum_cls is not None and hasattr(enum_cls, name):
        return getattr(enum_cls, name)
    flat = getattr(TT, f"ChannelEdge_{name}", None)
    if flat is not None:
        return flat
    legacy = {"Rising": "TT_CHANNEL_RISING_EDGES", "Falling": "TT_CHANNEL_FALLING_EDGES", "All": "TT_CHANNEL_RISING_AND_FALLING_EDGES"}
    return getattr(TT, legacy[name])


class TaggerHandle:
    """Owns one Swabian ``TimeTaggerBase`` object and describes it."""

    def __init__(self, tagger: Any, connection_type: ConnectionType, ident: str, serial: str = "", model: str = "", source: str = ""):
        self.tagger = tagger
        self.connection_type = connection_type
        self.id = ident
        self.serial = serial
        self.model = model
        self.source = source
        self.created_at = time.time()
        self.closed = False
        self.channels_all: list[int] = []
        self.channels_rising: list[int] = []
        self.channels_falling: list[int] = []
        self.sub_devices: list[SubDevice] = []
        self.capabilities: dict[str, bool] = {}
        self.notes: list[str] = []
        self.refresh_channels()
        self._detect_sub_devices()
        self._detect_capabilities()

    # ------------------------------------------------------------ properties
    @property
    def is_virtual(self) -> bool:
        return self.connection_type in (ConnectionType.VIRTUAL_REPLAY, ConnectionType.SIMULATOR)

    @property
    def is_multi_device(self) -> bool:
        return len(self.sub_devices) > 1

    # ------------------------------------------------------------ channels
    def refresh_channels(self) -> None:
        TT = get_api().require()
        t = self.tagger
        try:
            if self.is_virtual:
                chans = [int(c) for c in t.getChannelList()]
                self.channels_all = chans
                self.channels_rising = [c for c in chans if c > 0]
                self.channels_falling = [c for c in chans if c < 0]
            else:
                self.channels_rising = [int(c) for c in t.getChannelList(_edge_constant(TT, "Rising"))]
                self.channels_falling = [int(c) for c in t.getChannelList(_edge_constant(TT, "Falling"))]
                self.channels_all = [int(c) for c in t.getChannelList(_edge_constant(TT, "All"))]
        except Exception as exc:
            log.warning("getChannelList failed on %s: %s", self.id, exc)
            self.channels_all, self.channels_rising, self.channels_falling = [], [], []

    def inverted_channel(self, channel: int) -> Optional[int]:
        try:
            inv = int(self.tagger.getInvertedChannel(channel))
        except Exception:
            return None
        try:
            if self.tagger.isUnusedChannel(inv):
                return None
        except Exception:
            pass
        return inv

    # ------------------------------------------------------------ sub devices
    def _detect_sub_devices(self) -> None:
        self.sub_devices = []
        t = self.tagger
        if self.connection_type == ConnectionType.NETWORK and hasattr(t, "getServers"):
            try:
                servers = list(t.getServers())
            except Exception as exc:
                log.warning("getServers failed: %s", exc)
                servers = []
            for i, srv in enumerate(servers):
                sd = SubDevice(index=i)
                for attr, name in (("address", "getAddress"), ("serial", "getSerial"), ("model", "getModel")):
                    try:
                        setattr(sd, attr, str(getattr(srv, name)()))
                    except Exception:
                        pass
                try:
                    TT = get_api().require()
                    local = [int(c) for c in srv.getChannelList(_edge_constant(TT, "Rising"))]
                    for lc in local:
                        try:
                            sd.channel_map[lc] = int(srv.getClientChannel(lc))
                        except Exception:
                            sd.channel_map[lc] = lc
                    sd.channels = sorted(sd.channel_map.values())
                except Exception as exc:
                    log.debug("server channel map unavailable: %s", exc)
                self.sub_devices.append(sd)
            if len(self.sub_devices) > 1:
                self.notes.append("Multi-server TimeTaggerNetwork: channels are offset per server (see manual: n*1000).")
        elif self.connection_type in (ConnectionType.LOCAL_USB, ConnectionType.SYNCHRONIZED_MULTI_TAGGER):
            groups: dict[int, list[int]] = {}
            for c in self.channels_rising:
                groups.setdefault(c // 100 if c >= 100 else 0, []).append(c)
            if len(groups) > 1 or (groups and 0 not in groups):
                # Synchronizer channel scheme: CHANNEL = TT_NUMBER*100 + INPUT (manual 7.5)
                self.connection_type = ConnectionType.SYNCHRONIZED_MULTI_TAGGER
                for i, key in enumerate(sorted(groups)):
                    sd = SubDevice(index=i, serial=(self.serial if i == 0 else f"member-{key}"), model=self.model)
                    sd.channels = sorted(groups[key])
                    sd.channel_map = {c - key * 100: c for c in sd.channels}
                    self.sub_devices.append(sd)
                self.notes.append("Synchronizer group detected from channel numbering (TT_NUMBER*100 + INPUT).")
            else:
                sd = SubDevice(index=0, serial=self.serial, model=self.model)
                sd.channels = list(self.channels_rising)
                sd.channel_map = {c: c for c in sd.channels}
                self.sub_devices.append(sd)
        else:
            sd = SubDevice(index=0, serial=self.serial, model=self.model, address=self.source)
            sd.channels = list(self.channels_rising)
            sd.channel_map = {c: c for c in sd.channels}
            self.sub_devices.append(sd)

    def source_index(self, channel: int) -> int:
        """Index of the sub device that produces ``channel``."""
        for sd in self.sub_devices:
            if channel in sd.channels or channel in sd.channel_map.values() or (-channel) in sd.channels:
                return sd.index
        return 0

    def hardware_target(self, channel: int) -> tuple[Any, int]:
        """Object on which hardware setters for ``channel`` must be called, plus the local channel number.

        A ``TimeTaggerNetwork`` forwards hardware calls to the first server only; for
        other servers the ``TimeTaggerServer`` object must be used (manual 5.3.4).
        """
        t = self.tagger
        if self.connection_type == ConnectionType.NETWORK and len(self.sub_devices) > 1 and hasattr(t, "getServer"):
            idx = self.source_index(channel)
            sd = self.sub_devices[idx]
            try:
                srv = t.getServer(sd.address)
            except Exception:
                return t, channel
            for local, client in sd.channel_map.items():
                if client == channel:
                    return srv, local
            return srv, channel
        return t, channel

    # ------------------------------------------------------------ capabilities
    def _detect_capabilities(self) -> None:
        t = self.tagger
        virt = self.is_virtual
        caps = {
            "trigger_level": hasattr(t, "setTriggerLevel") and not virt,
            "input_delay": hasattr(t, "setInputDelay"),
            "deadtime": hasattr(t, "setDeadtime"),
            "event_divider": hasattr(t, "setEventDivider") and not virt,  # manual: not supported on TimeTaggerVirtual
            "hardware_delay_compensation": hasattr(t, "getHardwareDelayCompensation") and not virt,
            "test_signal": hasattr(t, "setTestSignal") and not virt,
            "reference_clock": hasattr(t, "setReferenceClock") and not virt,
            "conditional_filter": hasattr(t, "setConditionalFilter"),
            "impedance": False,
            "hysteresis": False,
            "replay": virt,
            "server": hasattr(t, "startServer") and not virt,
            "overflows": hasattr(t, "getOverflows"),
        }
        if not virt and self.channels_rising:
            ch0 = self.channels_rising[0]
            target, local = self.hardware_target(ch0)
            for cap, method in (("impedance", "getInputImpedanceHigh"), ("hysteresis", "getInputHysteresis")):
                try:
                    getattr(target, method)(local)
                    caps[cap] = True
                except Exception:
                    caps[cap] = False  # manual: Time Tagger X only
        self.capabilities = caps

    # ------------------------------------------------------------ state
    def get_configuration(self) -> dict[str, Any]:
        """``getConfiguration()`` as a dict (JSON string on some bindings)."""
        try:
            cfg = self.tagger.getConfiguration()
        except Exception as exc:
            return {"error": str(exc)}
        if isinstance(cfg, dict):
            return cfg
        try:
            return json.loads(cfg)
        except Exception:
            return {"raw": str(cfg)}

    def overflows(self) -> Optional[int]:
        try:
            return int(self.tagger.getOverflows())
        except Exception:
            return None

    def client_overflows(self) -> Optional[int]:
        if hasattr(self.tagger, "getOverflowsClient"):
            try:
                return int(self.tagger.getOverflowsClient())
            except Exception:
                return None
        return None

    def clear_overflows(self) -> None:
        try:
            self.tagger.clearOverflows()
        except Exception as exc:
            log.warning("clearOverflows failed: %s", exc)

    def firmware_info(self) -> dict[str, str]:
        info: dict[str, str] = {}
        for key, name in (("pcb_version", "getPcbVersion"), ("firmware", "getFirmwareVersion"), ("license", "getDeviceLicense")):
            fn = getattr(self.tagger, name, None)
            if fn is None:
                continue
            try:
                info[key] = str(fn())
            except Exception:
                pass
        return info

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "connection_type": self.connection_type.value,
            "serial": self.serial,
            "model": self.model,
            "source": self.source,
            "channels_rising": list(self.channels_rising),
            "channels_falling": list(self.channels_falling),
            "sub_devices": [sd.to_dict() for sd in self.sub_devices],
            "capabilities": dict(self.capabilities),
            "notes": list(self.notes),
        }

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            TT = get_api().require()
            if self.is_virtual and hasattr(self.tagger, "stop"):
                try:
                    self.tagger.stop()
                except Exception:
                    pass
            TT.freeTimeTagger(self.tagger)
        except Exception as exc:
            log.warning("freeTimeTagger failed for %s: %s", self.id, exc)
        self.tagger = None


# --------------------------------------------------------------------------- factories
def open_local(serial: str = "", resolution: Optional[str] = None) -> TaggerHandle:
    """``createTimeTagger(serial, resolution)``."""
    TT = get_api().require()
    if resolution:
        res = getattr(getattr(TT, "Resolution"), resolution, None) or getattr(TT, f"Resolution_{resolution}")
        tagger = TT.createTimeTagger(serial, res)
    else:
        tagger = TT.createTimeTagger(serial) if serial else TT.createTimeTagger()
    real_serial = ""
    model = ""
    try:
        real_serial = str(tagger.getSerial())
    except Exception:
        real_serial = serial
    try:
        model = str(tagger.getModel())
    except Exception:
        pass
    handle = TaggerHandle(tagger, ConnectionType.LOCAL_USB, ident=f"TT:{real_serial or serial or 'first'}", serial=real_serial, model=model, source="usb")
    log.info("Opened local Time Tagger %s (%s) with %d rising-edge channels", real_serial, model, len(handle.channels_rising))
    return handle


def open_network(addresses: list[str]) -> TaggerHandle:
    """``createTimeTaggerNetwork(addresses)``.

    The library raises if several non-synchronized servers are combined; that
    exception is passed through unchanged.
    """
    TT = get_api().require()
    addrs = [a.strip() for a in addresses if a.strip()]
    if not addrs:
        raise ValueError("No server address given")
    if len(addrs) == 1:
        tagger = TT.createTimeTaggerNetwork(addrs[0])
    else:
        tagger = TT.createTimeTaggerNetwork(addrs)
    serial = model = ""
    try:
        serial = str(tagger.getSerial())
        model = str(tagger.getModel())
    except Exception:
        pass
    handle = TaggerHandle(tagger, ConnectionType.NETWORK, ident="NET:" + "+".join(addrs), serial=serial, model=model, source=",".join(addrs))
    log.info("Opened TimeTaggerNetwork %s with %d servers", handle.id, len(handle.sub_devices))
    return handle


def open_virtual(filename: str, begin_ps: int = 0, duration_ps: int = -1) -> TaggerHandle:
    """``createTimeTaggerVirtual(filename, begin, duration)`` for TTbin replay."""
    TT = get_api().require()
    tagger = TT.createTimeTaggerVirtual(filename, int(begin_ps), int(duration_ps))
    handle = TaggerHandle(tagger, ConnectionType.VIRTUAL_REPLAY, ident=f"VIRT:{filename}", serial="virtual", model="TimeTaggerVirtual", source=filename)
    return handle


def open_simulated(ident: str = "SIM:0") -> TaggerHandle:
    """``createTimeTaggerVirtual()`` without a file: the library's simulated tagger."""
    TT = get_api().require()
    tagger = TT.createTimeTaggerVirtual()
    return TaggerHandle(tagger, ConnectionType.SIMULATOR, ident=ident, serial="simulator", model="TimeTaggerVirtual (simulated)", source="")


def close_handle(handle: TaggerHandle) -> None:
    handle.close()
