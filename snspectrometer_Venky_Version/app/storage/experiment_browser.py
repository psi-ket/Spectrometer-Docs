"""Scan the data directory into an experiment / run tree."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.results import load_json
from .run_store import RunDirectory


@dataclass
class RunInfo:
    path: Path
    run_id: str
    experiment_id: str
    status: str = ""
    status_reason: str = ""
    timestamp_start: str = ""
    duration_s: Any = None
    group_name: str = ""
    measurement_types: list[str] = field(default_factory=list)
    raw_files: list[str] = field(default_factory=list)
    result_files: list[str] = field(default_factory=list)
    analysis_files: list[str] = field(default_factory=list)
    has_log: bool = False
    missing_raw: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.run_id}  [{self.status or '?'}]"


@dataclass
class ExperimentInfo:
    path: Path
    experiment_id: str
    runs: list[RunInfo] = field(default_factory=list)
    name: str = ""


def scan_data_root(data_root: str | Path) -> list[ExperimentInfo]:
    root = Path(data_root)
    out: list[ExperimentInfo] = []
    if not root.exists():
        return out
    for exp_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        exp = ExperimentInfo(exp_dir, exp_dir.name)
        for run_dir in sorted((p for p in exp_dir.iterdir() if p.is_dir()), reverse=True):
            rd = RunDirectory(run_dir, create=False)
            meta = rd.read_metadata()
            status = rd.read_status()
            info = RunInfo(run_dir, run_dir.name, exp_dir.name)
            info.metadata = meta
            info.status = meta.get("status") or status.get("status", "")
            info.status_reason = meta.get("status_reason") or status.get("reason", "")
            info.timestamp_start = meta.get("timestamp_start", "")
            info.duration_s = meta.get("duration_actual_s")
            info.group_name = meta.get("group_name", "")
            info.measurement_types = [m.get("measurement_type", "") for m in meta.get("measurements", [])]
            info.raw_files = [str(p) for p in rd.raw_files()]
            info.result_files = [str(p) for p in rd.result_files()]
            info.analysis_files = [str(p) for p in rd.analysis_files()]
            info.has_log = rd.log_path.exists()
            info.missing_raw = [f for f in meta.get("raw_files", []) if not Path(f).exists()]
            if not exp.name:
                exp.name = meta.get("experiment", {}).get("experiment_name", "") or exp_dir.name
            exp.runs.append(info)
        out.append(exp)
    return out


def run_summary(info: RunInfo) -> dict[str, Any]:
    meta = info.metadata
    return {
        "run_id": info.run_id, "status": info.status, "reason": info.status_reason, "group": info.group_name,
        "start": info.timestamp_start, "stop": meta.get("timestamp_stop", ""), "duration_s": info.duration_s,
        "device": meta.get("device", {}).get("id", ""), "model": meta.get("device", {}).get("model", ""), "serial": meta.get("device", {}).get("serial", ""),
        "measurements": info.measurement_types, "raw_files": info.raw_files, "missing_raw": info.missing_raw,
        "results": info.result_files, "analysis": info.analysis_files, "overflows": meta.get("overflows_during_run"),
        "swabian_library_version": meta.get("swabian_library_version"), "application_version": meta.get("application_version"),
    }


def load_run_result(json_path: str | Path):
    from ..models.results import MeasurementResult
    return MeasurementResult.load(json_path)
