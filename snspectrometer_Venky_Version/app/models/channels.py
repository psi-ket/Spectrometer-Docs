"""Logical channel model: device -> physical channel -> detector -> wavelength -> role."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional

IMPEDANCE_CHOICES = ("50_OHM", "HIGH_Z")
ROLE_CHOICES = ("unassigned", "signal", "idler", "reference", "marker", "sync", "clock")


@dataclass(frozen=True)
class ChannelRef:
    device_id: str
    physical_channel: int

    @property
    def logical_id(self) -> str:
        return f"{self.device_id}:{self.physical_channel}"

    @staticmethod
    def parse(logical_id: str) -> "ChannelRef":
        dev, _, ch = logical_id.rpartition(":")
        return ChannelRef(dev, int(ch))


@dataclass
class DetectorChannel:
    """One physical input with its scientific identity.

    ``wavelength_nm`` is metadata (detector calibration), not a hardware setting.
    Hardware fields are the *requested* configuration; the applied values are read
    back from the device and stored separately in the run metadata.
    """

    tagger_id: str
    physical_channel: int
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
    source_index: int = 0  # sub-device index (network server / synchronizer member)

    @property
    def ref(self) -> ChannelRef:
        return ChannelRef(self.tagger_id, self.physical_channel)

    @property
    def logical_id(self) -> str:
        return self.ref.logical_id

    @property
    def label(self) -> str:
        name = self.detector_name or f"Ch {self.physical_channel}"
        if self.wavelength_nm is not None:
            return f"{name} ({self.wavelength_nm:g} nm)"
        return name

    def validate(self) -> list[str]:
        problems = []
        if self.wavelength_nm is not None and self.wavelength_nm <= 0:
            problems.append(f"{self.logical_id}: wavelength must be positive")
        if self.wavelength_bandwidth_nm is not None and self.wavelength_bandwidth_nm < 0:
            problems.append(f"{self.logical_id}: bandwidth must not be negative")
        if self.impedance is not None and self.impedance not in IMPEDANCE_CHOICES:
            problems.append(f"{self.logical_id}: invalid impedance {self.impedance!r}")
        if self.event_divider < 1 or self.event_divider > 65535:
            problems.append(f"{self.logical_id}: event divider must be 1..65535")
        if self.deadtime_ps is not None and self.deadtime_ps < 0:
            problems.append(f"{self.logical_id}: dead time must not be negative")
        if self.role not in ROLE_CHOICES:
            problems.append(f"{self.logical_id}: unknown role {self.role!r}")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "DetectorChannel":
        allowed = {f for f in DetectorChannel.__dataclass_fields__}
        return DetectorChannel(**{k: v for k, v in d.items() if k in allowed})


class DetectorMap:
    """Collection of :class:`DetectorChannel` entries keyed by logical id."""

    def __init__(self, channels: Iterable[DetectorChannel] = ()):
        self._items: dict[str, DetectorChannel] = {}
        for c in channels:
            self.add(c)

    # ---------------------------------------------------------------- mutation
    def add(self, channel: DetectorChannel) -> None:
        self._items[channel.logical_id] = channel

    def remove(self, logical_id: str) -> None:
        self._items.pop(logical_id, None)

    def remove_device(self, device_id: str) -> None:
        for k in [k for k, v in self._items.items() if v.tagger_id == device_id]:
            del self._items[k]

    def ensure_device_channels(self, device_id: str, physical_channels: Iterable[int], source_index_of=None) -> list[DetectorChannel]:
        """Create default entries for channels not yet mapped; returns the new ones."""
        created = []
        for ch in physical_channels:
            key = ChannelRef(device_id, int(ch)).logical_id
            if key not in self._items:
                dc = DetectorChannel(tagger_id=device_id, physical_channel=int(ch), detector_name=f"SNSPD-{int(ch):02d}")
                if source_index_of is not None:
                    try:
                        dc.source_index = int(source_index_of(int(ch)))
                    except Exception:
                        pass
                self._items[key] = dc
                created.append(dc)
        return created

    # ---------------------------------------------------------------- access
    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items.values())

    def __contains__(self, logical_id: str) -> bool:
        return logical_id in self._items

    def get(self, logical_id: str) -> Optional[DetectorChannel]:
        return self._items.get(logical_id)

    def get_ref(self, ref: ChannelRef) -> Optional[DetectorChannel]:
        return self._items.get(ref.logical_id)

    def get_physical(self, device_id: str, physical_channel: int) -> Optional[DetectorChannel]:
        return self._items.get(ChannelRef(device_id, physical_channel).logical_id)

    def all(self) -> list[DetectorChannel]:
        return list(self._items.values())

    def enabled(self) -> list[DetectorChannel]:
        return [c for c in self._items.values() if c.detector_enabled]

    def by_device(self, device_id: str, enabled_only: bool = False) -> list[DetectorChannel]:
        out = [c for c in self._items.values() if c.tagger_id == device_id]
        if enabled_only:
            out = [c for c in out if c.detector_enabled]
        return sorted(out, key=lambda c: c.physical_channel)

    def physical_channels(self, device_id: str, enabled_only: bool = True) -> list[int]:
        return [c.physical_channel for c in self.by_device(device_id, enabled_only)]

    def by_role(self, role: str, device_id: Optional[str] = None) -> list[DetectorChannel]:
        return [c for c in self._items.values() if c.role == role and (device_id is None or c.tagger_id == device_id)]

    def sorted_by_wavelength(self, device_id: Optional[str] = None, enabled_only: bool = True) -> list[DetectorChannel]:
        items = [c for c in self._items.values() if (device_id is None or c.tagger_id == device_id) and (not enabled_only or c.detector_enabled)]
        with_wl = [c for c in items if c.wavelength_nm is not None]
        without = [c for c in items if c.wavelength_nm is None]
        return sorted(with_wl, key=lambda c: c.wavelength_nm) + sorted(without, key=lambda c: (c.tagger_id, c.physical_channel))

    def devices(self) -> list[str]:
        return sorted({c.tagger_id for c in self._items.values()})

    def label_of(self, device_id: str, physical_channel: int) -> str:
        c = self.get_physical(device_id, physical_channel)
        return c.label if c is not None else f"Ch {physical_channel}"

    def validate(self) -> list[str]:
        problems: list[str] = []
        names: dict[str, str] = {}
        for c in self._items.values():
            problems.extend(c.validate())
            if c.detector_name:
                if c.detector_name in names and names[c.detector_name] != c.logical_id:
                    problems.append(f"detector name {c.detector_name!r} used twice ({names[c.detector_name]}, {c.logical_id})")
                names[c.detector_name] = c.logical_id
        return problems

    def wavelength_axis(self, device_id: Optional[str] = None) -> tuple[list[DetectorChannel], list[float]]:
        chans = [c for c in self.sorted_by_wavelength(device_id) if c.wavelength_nm is not None]
        return chans, [float(c.wavelength_nm) for c in chans]

    # ---------------------------------------------------------------- serialisation
    def to_dict(self) -> dict[str, Any]:
        return {"channels": [c.to_dict() for c in self._items.values()]}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "DetectorMap":
        return DetectorMap(DetectorChannel.from_dict(x) for x in d.get("channels", []))

    def copy(self) -> "DetectorMap":
        return DetectorMap.from_dict(self.to_dict())
