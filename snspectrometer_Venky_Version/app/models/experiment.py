"""Global experiment configuration and its frozen snapshot."""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .channels import DetectorMap


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str = "") -> str:
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}{stamp}_{uuid.uuid4().hex[:6]}"


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


@dataclass
class ExperimentConfig:
    experiment_id: str = field(default_factory=lambda: new_id("EXP_"))
    experiment_name: str = "Untitled experiment"
    operator: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    description: str = ""
    sample: str = ""
    pump_wavelength_nm: Optional[float] = None
    laser_repetition_rate_hz: Optional[float] = None
    detector_map: DetectorMap = field(default_factory=DetectorMap)
    time_tagger_configuration: dict[str, Any] = field(default_factory=dict)
    reference_clock_state: dict[str, Any] = field(default_factory=dict)
    synchronization_configuration: dict[str, Any] = field(default_factory=dict)
    measurement_settings: dict[str, Any] = field(default_factory=dict)
    acquisition_duration_s: float = 10.0
    file_saving: dict[str, Any] = field(default_factory=lambda: {"record_raw_ttbin": True, "max_file_size_bytes": None})
    analysis_settings: dict[str, Any] = field(default_factory=dict)
    software_version: str = ""
    swabian_library_version: str = ""
    devices: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["detector_map"] = self.detector_map.to_dict()
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ExperimentConfig":
        d = dict(d)
        dm = d.pop("detector_map", {"channels": []})
        allowed = set(ExperimentConfig.__dataclass_fields__)
        cfg = ExperimentConfig(**{k: v for k, v in d.items() if k in allowed})
        cfg.detector_map = DetectorMap.from_dict(dm) if isinstance(dm, dict) else DetectorMap()
        return cfg

    def freeze(self) -> "FrozenExperimentConfig":
        snapshot = copy.deepcopy(self.to_dict())
        return FrozenExperimentConfig(snapshot, stable_hash(snapshot), utc_now_iso())

    def validate(self) -> list[str]:
        problems = []
        if not self.experiment_name.strip():
            problems.append("Experiment name is empty")
        if self.acquisition_duration_s is not None and self.acquisition_duration_s < 0:
            problems.append("Acquisition duration must not be negative")
        if self.pump_wavelength_nm is not None and self.pump_wavelength_nm <= 0:
            problems.append("Pump wavelength must be positive")
        if self.laser_repetition_rate_hz is not None and self.laser_repetition_rate_hz <= 0:
            problems.append("Laser repetition rate must be positive")
        problems.extend(self.detector_map.validate())
        return problems


class FrozenExperimentConfig:
    """Immutable copy of the configuration captured at acquisition start."""

    __slots__ = ("_data", "config_hash", "frozen_at")

    def __init__(self, data: dict[str, Any], config_hash: str, frozen_at: str):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "frozen_at", frozen_at)

    def __setattr__(self, name, value):
        raise AttributeError("FrozenExperimentConfig is immutable")

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def __getitem__(self, key: str) -> Any:
        return copy.deepcopy(self._data[key])

    def get(self, key: str, default: Any = None) -> Any:
        return copy.deepcopy(self._data.get(key, default))
