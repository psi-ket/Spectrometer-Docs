"""Measurement registry and factory."""
from __future__ import annotations

from typing import Any, Optional

from .base import BaseMeasurement, MeasurementConfig

_REGISTRY: dict[str, type[BaseMeasurement]] = {}


def register(cls: type[BaseMeasurement]) -> type[BaseMeasurement]:
    _REGISTRY[cls.type_name] = cls
    return cls


def get_measurement_class(type_name: str) -> type[BaseMeasurement]:
    try:
        return _REGISTRY[type_name]
    except KeyError:
        raise KeyError(f"Unknown measurement type {type_name!r}; known: {sorted(_REGISTRY)}")


def list_measurement_types(category: Optional[str] = None) -> list[type[BaseMeasurement]]:
    out = list(_REGISTRY.values())
    if category:
        out = [c for c in out if c.category == category]
    return out


def create_measurement(type_name: str, params: dict[str, Any], labels: Optional[dict[int, str]] = None, wavelengths: Optional[dict[int, float]] = None, name: str = "") -> BaseMeasurement:
    cls = get_measurement_class(type_name)
    cfg = cls.ConfigClass.from_dict(params)
    return cls(cfg, labels=labels, wavelengths=wavelengths, name=name)


def default_config(type_name: str) -> MeasurementConfig:
    return get_measurement_class(type_name).ConfigClass()
