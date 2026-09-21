"""Configuration presets: portable per-device channel settings with explicit mapping."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .channels import DetectorChannel, DetectorMap

PRESET_VERSION = 1


@dataclass
class ChannelPreset:
    detector_name: str = ""
    wavelength_nm: Optional[float] = None
    wavelength_bandwidth_nm: Optional[float] = None
    detector_enabled: bool = True
    trigger_level_v: Optional[float] = None
    impedance: Optional[str] = None
    input_delay_ps: int = 0
    deadtime_ps: Optional[int] = None
    event_divider: int = 1
    role: str = "unassigned"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ChannelPreset":
        allowed = set(ChannelPreset.__dataclass_fields__)
        return ChannelPreset(**{k: v for k, v in d.items() if k in allowed})

    @staticmethod
    def from_detector(dc: DetectorChannel) -> "ChannelPreset":
        return ChannelPreset(
            detector_name=dc.detector_name, wavelength_nm=dc.wavelength_nm, wavelength_bandwidth_nm=dc.wavelength_bandwidth_nm,
            detector_enabled=dc.detector_enabled, trigger_level_v=dc.trigger_level_v, impedance=dc.impedance,
            input_delay_ps=dc.input_delay_ps, deadtime_ps=dc.deadtime_ps, event_divider=dc.event_divider, role=dc.role, notes=dc.notes,
        )


@dataclass
class DevicePreset:
    serial: str
    model: str = ""
    channels: dict[str, ChannelPreset] = field(default_factory=dict)  # key: physical channel as string

    def to_dict(self) -> dict[str, Any]:
        return {"serial": self.serial, "model": self.model, "channels": {k: v.to_dict() for k, v in self.channels.items()}}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "DevicePreset":
        return DevicePreset(serial=str(d.get("serial", "")), model=str(d.get("model", "")), channels={str(k): ChannelPreset.from_dict(v) for k, v in d.get("channels", {}).items()})


@dataclass
class Preset:
    preset_name: str
    version: int = PRESET_VERSION
    description: str = ""
    devices: list[DevicePreset] = field(default_factory=list)
    experiment_defaults: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"preset_name": self.preset_name, "version": self.version, "description": self.description, "devices": [d.to_dict() for d in self.devices], "experiment_defaults": dict(self.experiment_defaults)}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Preset":
        if "preset_name" not in d:
            raise ValueError("Not a preset file: missing 'preset_name'")
        return Preset(preset_name=str(d["preset_name"]), version=int(d.get("version", PRESET_VERSION)), description=str(d.get("description", "")), devices=[DevicePreset.from_dict(x) for x in d.get("devices", [])], experiment_defaults=dict(d.get("experiment_defaults", {})))

    @staticmethod
    def from_detector_map(name: str, detector_map: DetectorMap, device_serials: dict[str, str], device_models: Optional[dict[str, str]] = None) -> "Preset":
        """Build a preset from the current mapping. ``device_serials`` maps device id -> serial."""
        devices: dict[str, DevicePreset] = {}
        for dc in detector_map.all():
            serial = device_serials.get(dc.tagger_id, dc.tagger_id)
            dp = devices.setdefault(serial, DevicePreset(serial=serial, model=(device_models or {}).get(dc.tagger_id, "")))
            dp.channels[str(dc.physical_channel)] = ChannelPreset.from_detector(dc)
        return Preset(preset_name=name, devices=list(devices.values()))

    def device(self, serial: str) -> Optional[DevicePreset]:
        for d in self.devices:
            if d.serial == serial:
                return d
        return None


@dataclass
class PresetMapping:
    """How preset devices/channels map onto the currently connected hardware.

    ``device_map``: preset serial -> target device id.
    ``channel_map``: preset serial -> {preset channel -> target physical channel}.
    Channels without an entry map 1:1 by number, but only when the user chose to.
    """

    device_map: dict[str, str] = field(default_factory=dict)
    channel_map: dict[str, dict[int, int]] = field(default_factory=dict)
    identity_channels: bool = True

    def target_channel(self, serial: str, channel: int) -> Optional[int]:
        m = self.channel_map.get(serial, {})
        if channel in m:
            return m[channel]
        return channel if self.identity_channels else None


@dataclass
class PresetDifference:
    device_id: str
    channel: int
    field: str
    preset_value: Any
    current_value: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apply_preset_to_map(preset: Preset, detector_map: DetectorMap, mapping: PresetMapping, valid_channels: dict[str, list[int]]) -> tuple[list[DetectorChannel], list[str]]:
    """Update ``detector_map`` from ``preset``; returns (changed channels, warnings)."""
    changed: list[DetectorChannel] = []
    warnings: list[str] = []
    for dp in preset.devices:
        target_dev = mapping.device_map.get(dp.serial)
        if target_dev is None:
            warnings.append(f"Preset device {dp.serial} is not mapped to a connected device; skipped")
            continue
        allowed = set(valid_channels.get(target_dev, []))
        for ch_str, cp in dp.channels.items():
            src_ch = int(ch_str)
            tgt = mapping.target_channel(dp.serial, src_ch)
            if tgt is None:
                warnings.append(f"{dp.serial} channel {src_ch}: no target channel mapping; skipped")
                continue
            if allowed and tgt not in allowed:
                warnings.append(f"{dp.serial} channel {src_ch} -> {target_dev}:{tgt}: channel does not exist on target device; skipped")
                continue
            dc = detector_map.get_physical(target_dev, tgt)
            if dc is None:
                dc = DetectorChannel(tagger_id=target_dev, physical_channel=tgt)
                detector_map.add(dc)
            dc.detector_name = cp.detector_name or dc.detector_name
            dc.wavelength_nm = cp.wavelength_nm
            dc.wavelength_bandwidth_nm = cp.wavelength_bandwidth_nm
            dc.detector_enabled = cp.detector_enabled
            dc.trigger_level_v = cp.trigger_level_v
            dc.impedance = cp.impedance
            dc.input_delay_ps = cp.input_delay_ps
            dc.deadtime_ps = cp.deadtime_ps
            dc.event_divider = cp.event_divider
            dc.role = cp.role
            dc.notes = cp.notes
            changed.append(dc)
    return changed, warnings


def compare_preset(preset: Preset, mapping: PresetMapping, current_states: dict[str, dict[int, dict[str, Any]]], tolerance_v: float = 2e-3) -> list[PresetDifference]:
    """Compare preset hardware values against read-back hardware states.

    ``current_states``: device id -> channel -> {trigger_level_v, impedance, input_delay_ps, deadtime_ps, event_divider}.
    """
    diffs: list[PresetDifference] = []
    for dp in preset.devices:
        dev = mapping.device_map.get(dp.serial)
        if dev is None or dev not in current_states:
            continue
        for ch_str, cp in dp.channels.items():
            tgt = mapping.target_channel(dp.serial, int(ch_str))
            if tgt is None or tgt not in current_states[dev]:
                continue
            cur = current_states[dev][tgt]
            if cp.trigger_level_v is not None and cur.get("trigger_level_v") is not None:
                if abs(float(cp.trigger_level_v) - float(cur["trigger_level_v"])) > tolerance_v:
                    diffs.append(PresetDifference(dev, tgt, "trigger_level_v", cp.trigger_level_v, cur["trigger_level_v"]))
            for f in ("impedance", "input_delay_ps", "deadtime_ps", "event_divider"):
                pv = getattr(cp, f)
                cv = cur.get(f)
                if pv is not None and cv is not None and pv != cv:
                    diffs.append(PresetDifference(dev, tgt, f, pv, cv))
    return diffs
