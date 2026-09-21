"""Generic measurement abstraction.

A :class:`BaseMeasurement` wraps one or more official Swabian measurement objects.
It is *prepared* against a tagger object, which is normally the proxy returned
by ``SynchronizedMeasurements.getTagger()`` so that all measurements of a group
start on exactly the same time tags. Lifecycle control (start/stop/clear) is then
performed by the group; the measurement only converts library data into
numpy snapshots and result objects.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, fields, asdict, is_dataclass
from typing import Any, Callable, ClassVar, Optional, Sequence

import numpy as np

from ..models.results import MeasurementResult
from ..models.experiment import utc_now_iso


# --------------------------------------------------------------------------- config schema
@dataclass
class FieldSpec:
    name: str
    label: str
    kind: str  # int | float | bool | choice | str | channel | channel_optional | channels
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Optional[Sequence[Any]] = None
    help: str = ""
    advanced: bool = False
    step: Optional[float] = None


@dataclass
class ValidationContext:
    available_channels: list[int] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=dict)
    sync_allows_cross_device: bool = True
    multi_device: bool = False
    source_index_of: Optional[Callable[[int], int]] = None
    is_virtual: bool = False
    library_capabilities: dict[str, bool] = field(default_factory=dict)

    def cross_device(self, channels: Sequence[int]) -> bool:
        if not self.multi_device or self.source_index_of is None:
            return False
        idx = {self.source_index_of(int(c)) for c in channels}
        return len(idx) > 1


class MeasurementConfig:
    """Base class for dataclass configs. Subclasses define ``FIELDS`` for the UI."""

    FIELDS: ClassVar[list[FieldSpec]] = []

    def to_dict(self) -> dict[str, Any]:
        if is_dataclass(self):
            return asdict(self)
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict[str, Any]):
        allowed = {f.name for f in fields(cls)} if is_dataclass(cls) else set(d)
        return cls(**{k: v for k, v in d.items() if k in allowed})

    def channels_used(self) -> list[int]:
        return []

    def validate(self, ctx: ValidationContext) -> list[str]:
        problems: list[str] = []
        avail = set(ctx.available_channels)
        for ch in self.channels_used():
            if avail and int(ch) not in avail:
                problems.append(f"Channel {ch} is not available on the selected device")
        for spec in self.FIELDS:
            val = getattr(self, spec.name, None)
            if val is None:
                continue
            if spec.kind in ("int", "float"):
                if spec.minimum is not None and val < spec.minimum:
                    problems.append(f"{spec.label} must be >= {spec.minimum}")
                if spec.maximum is not None and val > spec.maximum:
                    problems.append(f"{spec.label} must be <= {spec.maximum}")
            if spec.kind == "choice" and spec.choices and val not in spec.choices:
                problems.append(f"{spec.label}: invalid choice {val!r}")
            if spec.kind == "channels" and not val:
                problems.append(f"{spec.label}: select at least one channel")
        if ctx.cross_device(self.channels_used()) and not ctx.sync_allows_cross_device:
            problems.append("Channels span several devices but the devices are not verified as synchronized")
        return problems


# --------------------------------------------------------------------------- snapshots
@dataclass
class Snapshot:
    wall_time: float
    capture_duration_ps: int
    arrays: dict[str, np.ndarray] = field(default_factory=dict)
    scalars: dict[str, Any] = field(default_factory=dict)
    finished: bool = False
    labels: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlotSpec:
    kind: str  # lines | histogram | heatmap | bars | matrix
    title: str
    x_key: str = ""
    y_keys: Sequence[str] = ()
    x_label: str = ""
    y_label: str = ""
    x_unit: str = ""
    y_unit: str = ""
    log_y: bool = False
    series_label_key: str = ""  # key in snapshot.labels giving per-row labels
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- base measurement
class BaseMeasurement:
    type_name: ClassVar[str] = "base"
    display_name: ClassVar[str] = "Base"
    category: ClassVar[str] = "basic"  # basic | advanced | developer
    ConfigClass: ClassVar[type] = MeasurementConfig
    HELP: ClassVar[str] = ""

    def __init__(self, config: MeasurementConfig, labels: Optional[dict[int, str]] = None, wavelengths: Optional[dict[int, float]] = None, name: str = ""):
        self.config = config
        self.labels: dict[int, str] = dict(labels or {})
        self.wavelengths: dict[int, float] = dict(wavelengths or {})
        self.name = name or self.display_name
        self.objects: list[Any] = []       # official measurement objects (IteratorBase-like)
        self.vchannels: list[Any] = []     # virtual channel objects that must stay alive
        self.impl_notes: list[str] = []
        self.prepared = False
        self.last_snapshot: Optional[Snapshot] = None
        self.measurement_id: str = ""

    # ------------------------------------------------------------ helpers
    def label(self, ch: int) -> str:
        return self.labels.get(int(ch), f"Ch {ch}")

    def iterator_objects(self) -> list[Any]:
        """Objects to register with SynchronizedMeasurements."""
        return list(self.objects)

    # ------------------------------------------------------------ lifecycle
    def prepare(self, tagger: Any) -> None:
        self.release()
        self._build(tagger)
        self.prepared = True

    def _build(self, tagger: Any) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def release(self) -> None:
        for o in self.objects:
            try:
                o.stop()
            except Exception:
                pass
        self.objects = []
        self.vchannels = []
        self.prepared = False

    def start(self) -> None:
        for o in self.objects:
            o.start()

    def start_for(self, duration_ps: int, clear: bool = True) -> None:
        for o in self.objects:
            o.startFor(int(duration_ps), clear)

    def stop(self) -> None:
        for o in self.objects:
            o.stop()

    def clear(self) -> None:
        for o in self.objects:
            o.clear()

    def is_running(self) -> bool:
        return any(bool(o.isRunning()) for o in self.objects)

    def capture_duration_ps(self) -> int:
        best = 0
        for o in self.objects:
            try:
                best = max(best, int(o.getCaptureDuration()))
            except Exception:
                pass
        return best

    # ------------------------------------------------------------ data
    def snapshot(self) -> Snapshot:
        snap = Snapshot(wall_time=time.time(), capture_duration_ps=self.capture_duration_ps())
        self._fill_snapshot(snap)
        snap.scalars.setdefault("capture_duration_s", snap.capture_duration_ps / 1e12)
        self.last_snapshot = snap
        return snap

    def _fill_snapshot(self, snap: Snapshot) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def plot_specs(self) -> list[PlotSpec]:
        return []

    def units(self) -> dict[str, str]:
        return {}

    def array_descriptions(self) -> dict[str, str]:
        return {}

    def configuration_record(self) -> dict[str, Any]:
        rec = {
            "measurement_type": self.type_name,
            "name": self.name,
            "parameters": self.config.to_dict(),
            "channel_labels": {str(k): v for k, v in self.labels.items()},
            "channel_wavelengths_nm": {str(k): v for k, v in self.wavelengths.items()},
            "implementation_notes": list(self.impl_notes),
            "swabian_objects": [type(o).__name__ for o in self.objects],
        }
        return rec

    def library_configuration(self) -> list[Any]:
        out = []
        for o in self.objects:
            try:
                out.append(o.getConfiguration())
            except Exception as exc:
                out.append({"error": str(exc)})
        return out

    def build_result(self, snap: Optional[Snapshot] = None, timestamp_start: str = "", timestamp_end: str = "", status: str = "COMPLETED", status_reason: str = "", raw_files: Optional[list[str]] = None, metadata: Optional[dict[str, Any]] = None) -> MeasurementResult:
        snap = snap or self.last_snapshot or self.snapshot()
        res = MeasurementResult(
            measurement_id=self.measurement_id or MeasurementResult().measurement_id,
            measurement_type=self.type_name,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end or utc_now_iso(),
            configuration=self.configuration_record(),
            arrays={k: np.asarray(v) for k, v in snap.arrays.items()},
            scalars=dict(snap.scalars),
            metadata=dict(metadata or {}),
            raw_files=list(raw_files or []),
            status=status,
            status_reason=status_reason,
            units=self.units(),
            array_descriptions=self.array_descriptions(),
        )
        res.metadata.setdefault("capture_duration_ps", snap.capture_duration_ps)
        res.metadata.setdefault("labels", snap.labels)
        return res


def nan_stats(values: np.ndarray) -> dict[str, float]:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {"min": float("nan"), "max": float("nan"), "mean": float("nan"), "std": float("nan"), "n": 0}
    return {"min": float(v.min()), "max": float(v.max()), "mean": float(v.mean()), "std": float(v.std(ddof=1)) if v.size > 1 else 0.0, "n": int(v.size)}
