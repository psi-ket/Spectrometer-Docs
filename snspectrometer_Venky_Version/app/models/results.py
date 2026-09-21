"""Standard measurement result object with JSON metadata + NPZ arrays."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .experiment import utc_now_iso, new_id, stable_hash

RESULT_FORMAT_VERSION = 1


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "value"):
        return o.value
    return str(o)


def dump_json(obj: Any, path: str | os.PathLike) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False, default=_json_default)
    os.replace(tmp, p)


def load_json(path: str | os.PathLike) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def sha256_file(path: str | os.PathLike, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


@dataclass
class Provenance:
    source_raw_files: list[str] = field(default_factory=list)
    source_measurement_id: str = ""
    analysis_type: str = ""
    analysis_version: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    software_version: str = ""
    swabian_library_version: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_raw_files": list(self.source_raw_files),
            "source_measurement_id": self.source_measurement_id,
            "analysis_type": self.analysis_type,
            "analysis_version": self.analysis_version,
            "parameters": dict(self.parameters),
            "created_at": self.created_at,
            "software_version": self.software_version,
            "swabian_library_version": self.swabian_library_version,
            "notes": self.notes,
        }


@dataclass
class MeasurementResult:
    measurement_id: str = field(default_factory=lambda: new_id("MEAS_"))
    measurement_type: str = ""
    timestamp_start: str = ""
    timestamp_end: str = ""
    configuration: dict[str, Any] = field(default_factory=dict)
    arrays: dict[str, np.ndarray] = field(default_factory=dict)
    scalars: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_files: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "COMPLETED"
    status_reason: str = ""
    units: dict[str, str] = field(default_factory=dict)
    array_descriptions: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ files
    def json_dict(self) -> dict[str, Any]:
        return {
            "format_version": RESULT_FORMAT_VERSION,
            "measurement_id": self.measurement_id,
            "measurement_type": self.measurement_type,
            "timestamp_start": self.timestamp_start,
            "timestamp_end": self.timestamp_end,
            "status": self.status,
            "status_reason": self.status_reason,
            "configuration": self.configuration,
            "configuration_hash": stable_hash(self.configuration),
            "scalars": self.scalars,
            "units": self.units,
            "arrays": {k: {"shape": list(v.shape), "dtype": str(v.dtype), "description": self.array_descriptions.get(k, "")} for k, v in self.arrays.items()},
            "metadata": self.metadata,
            "raw_files": list(self.raw_files),
            "provenance": self.provenance,
        }

    def save(self, directory: str | os.PathLike, basename: str = "result") -> tuple[Path, Path]:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        npz_path = d / f"{basename}.npz"
        json_path = d / f"{basename}.json"
        arrays = {k: np.asarray(v) for k, v in self.arrays.items()}
        if arrays:
            np.savez_compressed(npz_path, **arrays)
        payload = self.json_dict()
        payload["npz_file"] = npz_path.name if arrays else None
        payload["npz_sha256"] = sha256_file(npz_path) if arrays else None
        payload["saved_at"] = utc_now_iso()
        dump_json(payload, json_path)
        return json_path, npz_path

    @staticmethod
    def load(json_path: str | os.PathLike) -> "MeasurementResult":
        p = Path(json_path)
        meta = load_json(p)
        res = MeasurementResult(
            measurement_id=meta.get("measurement_id", ""), measurement_type=meta.get("measurement_type", ""),
            timestamp_start=meta.get("timestamp_start", ""), timestamp_end=meta.get("timestamp_end", ""),
            configuration=meta.get("configuration", {}), scalars=meta.get("scalars", {}), metadata=meta.get("metadata", {}),
            raw_files=list(meta.get("raw_files", [])), provenance=meta.get("provenance", {}), status=meta.get("status", ""),
            status_reason=meta.get("status_reason", ""), units=meta.get("units", {}),
            array_descriptions={k: v.get("description", "") for k, v in meta.get("arrays", {}).items()},
        )
        npz_name = meta.get("npz_file")
        if npz_name:
            npz_path = p.parent / npz_name
            if npz_path.exists():
                with np.load(npz_path) as data:
                    res.arrays = {k: data[k] for k in data.files}
                expected = meta.get("npz_sha256")
                if expected and sha256_file(npz_path) != expected:
                    res.metadata["integrity_warning"] = "NPZ checksum does not match the JSON record"
            else:
                res.metadata["integrity_warning"] = f"NPZ file missing: {npz_name}"
        missing = [f for f in res.raw_files if not Path(f).exists()]
        if missing:
            res.metadata["missing_raw_files"] = missing
        return res

    # ------------------------------------------------------------------ export
    def to_csv(self, path: str | os.PathLike, array_keys: Optional[list[str]] = None) -> Path:
        """Write 1-D arrays of equal length as columns (2-D arrays are flattened row-wise)."""
        keys = array_keys or [k for k, v in self.arrays.items() if np.asarray(v).ndim == 1]
        cols = [np.asarray(self.arrays[k]).ravel() for k in keys]
        n = max((len(c) for c in cols), default=0)
        p = Path(path)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# measurement_id=" + self.measurement_id + "\n")
            fh.write("# measurement_type=" + self.measurement_type + "\n")
            for k, u in self.units.items():
                fh.write(f"# unit {k}={u}\n")
            fh.write(",".join(keys) + "\n")
            for i in range(n):
                fh.write(",".join(str(c[i]) if i < len(c) else "" for c in cols) + "\n")
        return p
