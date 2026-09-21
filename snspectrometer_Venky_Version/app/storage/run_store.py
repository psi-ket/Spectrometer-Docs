"""Run directory layout and crash-recovery checkpoints.

    <data_root>/<experiment_id>/<run_id>/
        measurement.json      group metadata (progressively flushed)
        status.json           heartbeat / status checkpoint
        results/<name>.json   per-measurement metadata
        results/<name>.npz    per-measurement arrays
        raw/<name>.ttbin      FileWriter output (+ .N.ttbin parts)
        analysis/             re-analysis outputs (never overwrite raw)
        run.log               log excerpt for this run
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Any, Optional

import psutil

from ..models.experiment import utc_now_iso
from ..models.results import dump_json, load_json


def safe_name(text: str, default: str = "run") -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("._")
    return s or default


class RunDirectory:
    def __init__(self, path: str | os.PathLike, create: bool = True):
        self.path = Path(path)
        if create:
            self.path.mkdir(parents=True, exist_ok=True)
            (self.path / "results").mkdir(exist_ok=True)
            (self.path / "raw").mkdir(exist_ok=True)
            (self.path / "analysis").mkdir(exist_ok=True)

    @staticmethod
    def create(data_root: str | os.PathLike, experiment_id: str, run_name: str) -> "RunDirectory":
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(data_root) / safe_name(experiment_id, "experiment")
        run_id = f"{stamp}_{safe_name(run_name)}"
        p = base / run_id
        i = 1
        while p.exists():
            i += 1
            p = base / f"{run_id}_{i}"
        return RunDirectory(p)

    # ------------------------------------------------------------------ paths
    @property
    def run_id(self) -> str:
        return self.path.name

    @property
    def experiment_id(self) -> str:
        return self.path.parent.name

    @property
    def raw_dir(self) -> Path:
        return self.path / "raw"

    @property
    def results_dir(self) -> Path:
        return self.path / "results"

    @property
    def analysis_dir(self) -> Path:
        return self.path / "analysis"

    @property
    def metadata_path(self) -> Path:
        return self.path / "measurement.json"

    @property
    def status_path(self) -> Path:
        return self.path / "status.json"

    @property
    def log_path(self) -> Path:
        return self.path / "run.log"

    # ------------------------------------------------------------------ checkpoints
    def write_status(self, status: str, reason: str = "", progress: Optional[float] = None, extra: Optional[dict[str, Any]] = None) -> None:
        payload = {"status": status, "reason": reason, "progress": progress, "pid": os.getpid(), "heartbeat": utc_now_iso()}
        if extra:
            payload.update(extra)
        dump_json(payload, self.status_path)

    def read_status(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return {}
        try:
            return load_json(self.status_path)
        except Exception:
            return {}

    def write_metadata(self, meta: dict[str, Any]) -> None:
        dump_json(meta, self.metadata_path)

    def read_metadata(self) -> dict[str, Any]:
        if not self.metadata_path.exists():
            return {}
        try:
            return load_json(self.metadata_path)
        except Exception:
            return {}

    def append_log(self, text: str) -> None:
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")

    def result_files(self) -> list[Path]:
        return sorted(self.results_dir.glob("*.json")) if self.results_dir.exists() else []

    def raw_files(self) -> list[Path]:
        return sorted(self.raw_dir.glob("*.ttbin")) if self.raw_dir.exists() else []

    def analysis_files(self) -> list[Path]:
        return sorted(self.analysis_dir.glob("*.json")) if self.analysis_dir.exists() else []


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        return psutil.pid_exists(int(pid))
    except Exception:
        return False


def find_interrupted_runs(data_root: str | os.PathLike, mark: bool = True) -> list[RunDirectory]:
    """Runs whose status checkpoint says active but whose process is gone.

    With ``mark=True`` they are relabelled INTERRUPTED (never COMPLETED) in both
    status.json and measurement.json.
    """
    root = Path(data_root)
    out: list[RunDirectory] = []
    if not root.exists():
        return out
    for status_file in root.glob("*/*/status.json"):
        rd = RunDirectory(status_file.parent, create=False)
        st = rd.read_status()
        if st.get("status") in ("RUNNING", "ARMED", "PAUSED", "PREPARED") and not _pid_alive(st.get("pid")):
            out.append(rd)
            if mark:
                reason = "application terminated while acquisition was active"
                rd.write_status("INTERRUPTED", reason, st.get("progress"), {"previous_status": st.get("status"), "detected_at": utc_now_iso()})
                meta = rd.read_metadata()
                if meta:
                    meta["status"] = "INTERRUPTED"
                    meta["status_reason"] = reason
                    rd.write_metadata(meta)
    return out
