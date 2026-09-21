"""Offline analysis: replay a TTbin through TimeTaggerVirtual into the *same* measurement classes.

    Live:    TimeTagger        -> SynchronizedMeasurements -> BaseMeasurement
    Offline: TTbin -> TimeTaggerVirtual -> SynchronizedMeasurements -> BaseMeasurement
"""
from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

from swabian_backend import get_api
from swabian_backend.replay import ReplaySource, FileScanResult, scan_file, SPEED_PRESETS, normalise_speed
from swabian_backend.synchronization import SyncGroup

from ..measurements.base import BaseMeasurement, Snapshot
from ..models.experiment import new_id, utc_now_iso
from ..models.results import MeasurementResult, Provenance

log = logging.getLogger("snspec.analysis.replay")


class ReplaySession:
    """One TTbin (sequence) replayed into a set of measurements with transport controls."""

    def __init__(self, filename: str, begin_ps: int = 0, duration_ps: int = -1, total_duration_ps: Optional[int] = None, history_size: int = 50):
        self.filename = str(filename)
        self.source = ReplaySource(self.filename, begin_ps, duration_ps)
        self.handle = self.source.handle
        self.measurements: list[BaseMeasurement] = []
        self.sync: Optional[SyncGroup] = None
        self.total_duration_ps: Optional[int] = total_duration_ps
        self.scan: Optional[FileScanResult] = None
        self.history: dict[str, deque] = {}
        self.history_size = history_size
        self.session_id = new_id("REPLAY_")
        self.started_at = ""
        self.state = "idle"  # idle | armed | playing | paused | finished | stopped
        self._speed = 1.0
        self.last_snapshots: dict[str, Snapshot] = {}

    # ------------------------------------------------------------------ setup
    def scan_in_background(self) -> FileScanResult:
        self.scan = scan_file(self.filename)
        if self.total_duration_ps is None:
            self.total_duration_ps = self.scan.duration_ps
        return self.scan

    def add_measurement(self, m: BaseMeasurement) -> None:
        if not m.measurement_id:
            m.measurement_id = new_id("MEAS_")
        self.measurements.append(m)
        self.history[m.measurement_id] = deque(maxlen=self.history_size)

    def clear_measurements(self) -> None:
        for m in self.measurements:
            m.release()
        self.measurements = []
        self.history = {}
        self.sync = None
        self.state = "idle"

    def arm(self) -> None:
        """Create measurement objects on a synchronized proxy and start them (data flows on play)."""
        self.sync = SyncGroup(self.source.tagger)
        for m in self.measurements:
            m.prepare(self.sync.proxy)
        self.sync.clear()
        self.sync.start()
        self.state = "armed"
        self.started_at = utc_now_iso()

    # ------------------------------------------------------------------ transport
    def play(self, speed: float = 1.0) -> str:
        if self.sync is None:
            self.arm()
        self._speed = speed
        note = self.source.run(speed)
        self.state = "playing"
        log.info("Replay %s playing at %s (%s)", Path(self.filename).name, speed, note or "ok")
        return note

    def pause(self) -> int:
        pos = self.source.pause()
        self.state = "paused"
        return pos

    def resume(self) -> str:
        note = self.source.resume(self._speed)
        self.state = "playing"
        return note

    def stop(self) -> None:
        self.source.stop()
        self.state = "stopped"

    def restart(self, speed: Optional[float] = None) -> str:
        if self.sync is not None:
            self.sync.stop()
            self.sync.clear()
            self.sync.start()
        for q in self.history.values():
            q.clear()
        note = self.source.restart(speed if speed is not None else self._speed)
        self.state = "playing"
        return note

    def set_speed(self, speed: float) -> str:
        """Change speed: pause and resume at the new speed (the library has no live speed change on run())."""
        self._speed = speed
        if self.state == "playing":
            self.source.pause()
            return self.source.resume(speed)
        return normalise_speed(speed)[1]

    # ------------------------------------------------------------------ status
    def position_ps(self) -> int:
        if self.state == "finished" and self.total_duration_ps:
            return int(self.total_duration_ps)
        return self.source.position_ps()

    def progress(self) -> Optional[float]:
        if not self.total_duration_ps:
            return None
        return min(1.0, self.position_ps() / float(self.total_duration_ps))

    def remaining_ps(self) -> Optional[int]:
        if not self.total_duration_ps:
            return None
        return max(0, int(self.total_duration_ps) - self.position_ps())

    def is_finished(self) -> bool:
        fin = self.state in ("playing",) and self.source.is_finished()
        if fin:
            self.state = "finished"
        return self.state == "finished"

    def poll(self) -> dict[str, Snapshot]:
        snaps: dict[str, Snapshot] = {}
        for m in self.measurements:
            try:
                s = m.snapshot()
                s.scalars["replay_position_s"] = self.position_ps() / 1e12
                snaps[m.measurement_id] = s
                self.history[m.measurement_id].append(s)
            except Exception:
                log.exception("replay snapshot failed for %s", m.name)
        self.last_snapshots = snaps
        self.is_finished()
        return snaps

    # ------------------------------------------------------------------ results
    def provenance(self, m: BaseMeasurement, source_measurement_id: str = "") -> Provenance:
        api = get_api()
        return Provenance(
            source_raw_files=[self.filename] + ([str(f) for f in self.scan.files if str(f) != self.filename] if self.scan else []),
            source_measurement_id=source_measurement_id,
            analysis_type=f"replay:{m.type_name}",
            analysis_version="1",
            parameters={"measurement": m.configuration_record(), "begin_ps": self.source.begin_ps, "duration_ps": self.source.duration_ps, "speed": self._speed},
            swabian_library_version=api.version,
            notes="Re-analysis by TimeTaggerVirtual replay of the raw TTbin; raw file untouched.",
        )

    def build_results(self, source_measurement_id: str = "", app_version: str = "") -> list[MeasurementResult]:
        out = []
        for m in self.measurements:
            # take a fresh snapshot when the stream is idle so counts and singles totals are consistent
            snap = m.snapshot() if self.state in ("finished", "stopped", "paused") else (m.last_snapshot or m.snapshot())
            prov = self.provenance(m, source_measurement_id)
            prov.software_version = app_version
            res = m.build_result(snap, self.started_at, utc_now_iso(), "COMPLETED" if self.state == "finished" else "PARTIAL", "" if self.state == "finished" else f"replay state {self.state}", prov.source_raw_files, {"replay_session": self.session_id, "replay_position_ps": self.position_ps(), "total_duration_ps": self.total_duration_ps})
            res.provenance = prov.to_dict()
            out.append(res)
        return out

    def save_results(self, directory: str | Path, source_measurement_id: str = "", app_version: str = "") -> list[Path]:
        paths = []
        for res in self.build_results(source_measurement_id, app_version):
            stamp = time.strftime("%Y%m%d_%H%M%S")
            j, _ = res.save(directory, f"analysis_{res.measurement_type}_{stamp}_{res.measurement_id[-6:]}")
            paths.append(j)
        return paths

    def close(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
        for m in self.measurements:
            try:
                m.release()
            except Exception:
                pass
        self.sync = None
        self.source.close()


def speed_presets() -> list[tuple[str, float]]:
    return list(SPEED_PRESETS)
