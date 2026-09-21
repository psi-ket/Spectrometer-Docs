"""Synchronized measurement groups.

A group owns one ``SynchronizedMeasurements`` (via :class:`SyncGroup`) on one
tagger handle. All measurements and the optional raw ``FileWriter`` are created
against the proxy tagger, so they process exactly the same time tags and start
together. Single measurements are run as one-member groups for a uniform
lifecycle:

    prepare -> arm -> start -> (pause/resume) -> stop/finish -> finalize
"""
from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from swabian_backend.devices import TaggerHandle
from swabian_backend.recorder import Recorder
from swabian_backend.synchronization import SyncGroup, reference_clock_state
from swabian_backend import get_api

from ..models.experiment import FrozenExperimentConfig, utc_now_iso, new_id
from ..models.results import MeasurementResult
from ..models.sync_state import RunStatus
from ..storage.run_store import RunDirectory, safe_name
from .base import BaseMeasurement, Snapshot, ValidationContext

log = logging.getLogger("snspec.measurements.group")


@dataclass
class PreflightItem:
    name: str
    ok: bool
    detail: str = ""
    blocking: bool = True

    @property
    def label(self) -> str:
        return ("PASS" if self.ok else ("BLOCK" if self.blocking else "WARN")) + "  " + self.name + (f" - {self.detail}" if self.detail else "")


@dataclass
class RawRecordingConfig:
    enabled: bool = False
    channels: Optional[list[int]] = None        # None -> all enabled physical channels of the group
    include_virtual: bool = False
    max_file_size_bytes: Optional[int] = None
    filename: str = "raw"
    marker: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "channels": self.channels, "include_virtual": self.include_virtual, "max_file_size_bytes": self.max_file_size_bytes, "filename": self.filename, "marker": self.marker}


class MeasurementGroup:
    def __init__(
        self,
        name: str,
        handle: TaggerHandle,
        measurements: list[BaseMeasurement],
        duration_s: Optional[float] = None,
        raw: Optional[RawRecordingConfig] = None,
        run_dir: Optional[RunDirectory] = None,
        experiment: Optional[FrozenExperimentConfig] = None,
        validation_context: Optional[ValidationContext] = None,
        physical_channels: Optional[list[int]] = None,
        hardware_states: Optional[dict[str, Any]] = None,
        sync_info: Optional[dict[str, Any]] = None,
        app_version: str = "",
    ):
        self.name = name
        self.handle = handle
        self.measurements = list(measurements)
        self.duration_s = float(duration_s) if duration_s else None
        self.raw = raw or RawRecordingConfig()
        self.run_dir = run_dir
        self.experiment = experiment
        self.ctx = validation_context or ValidationContext(available_channels=list(handle.channels_rising))
        self.physical_channels = list(physical_channels or handle.channels_rising)
        self.hardware_states = dict(hardware_states or {})
        self.sync_info = dict(sync_info or {})
        self.app_version = app_version
        self.group_id = new_id("GRP_")
        self.status = RunStatus.PENDING
        self.status_reason = ""
        self.sync: Optional[SyncGroup] = None
        self.recorder: Optional[Recorder] = None
        self.timestamp_start = ""
        self.timestamp_end = ""
        self._t_start = 0.0
        self._paused_accum = 0.0
        self._pause_t0 = 0.0
        self.snapshots: dict[str, Snapshot] = {}
        self.results: dict[str, MeasurementResult] = {}
        self.preflight_items: list[PreflightItem] = []
        self.overflows_at_start: Optional[int] = None
        self.overflows_seen: int = 0
        self.error: str = ""
        self._last_checkpoint = 0.0
        self.on_finished: Optional[Callable[["MeasurementGroup"], None]] = None
        for m in self.measurements:
            if not m.measurement_id:
                m.measurement_id = new_id("MEAS_")

    # ------------------------------------------------------------------ preflight
    def preflight(self) -> list[PreflightItem]:
        items: list[PreflightItem] = []
        h = self.handle
        items.append(PreflightItem("Hardware connected", h.tagger is not None and not h.closed, h.id))
        items.append(PreflightItem("Channels available", bool(h.channels_rising) or h.is_virtual, f"{len(h.channels_rising)} rising-edge channels"))
        if self.duration_s is not None:
            items.append(PreflightItem("Acquisition duration", self.duration_s > 0, f"{self.duration_s} s"))
        if not self.measurements:
            items.append(PreflightItem("Measurements selected", False, "the group is empty"))
        for m in self.measurements:
            problems = m.config.validate(self.ctx)
            items.append(PreflightItem(f"{m.name} parameters", not problems, "; ".join(problems)))
            cross = self.ctx.cross_device(m.config.channels_used())
            if cross:
                items.append(PreflightItem(f"{m.name} cross-device synchronization", self.ctx.sync_allows_cross_device, "requires verified synchronization" if not self.ctx.sync_allows_cross_device else "synchronized"))
        if self.raw.enabled:
            chans = self._raw_channels()
            items.append(PreflightItem("TTbin recording channels", bool(chans), f"{len(chans)} channels"))
            if self.run_dir is not None:
                try:
                    test = self.run_dir.raw_dir / ".write_test"
                    test.write_text("ok")
                    test.unlink()
                    items.append(PreflightItem("Output directory writable", True, str(self.run_dir.path)))
                except Exception as exc:
                    items.append(PreflightItem("Output directory writable", False, str(exc)))
        elif self.run_dir is not None:
            items.append(PreflightItem("Output directory", self.run_dir.path.exists(), str(self.run_dir.path)))
        ov = h.overflows()
        if ov is not None:
            items.append(PreflightItem("Overflow counter", True, f"{ov} overflows since last clear", blocking=False))
        self.preflight_items = items
        return items

    @property
    def preflight_ok(self) -> bool:
        return all(i.ok or not i.blocking for i in self.preflight_items)

    # ------------------------------------------------------------------ lifecycle
    def prepare(self) -> None:
        if not self.preflight_items:
            self.preflight()
        if not self.preflight_ok:
            blocked = [i.label for i in self.preflight_items if not i.ok and i.blocking]
            raise RuntimeError("Preflight blocked: " + "; ".join(blocked))
        self.sync = SyncGroup(self.handle.tagger)
        self.status = RunStatus.PREPARED
        self._checkpoint(force=True)

    def _raw_channels(self) -> list[int]:
        if self.raw.channels:
            chans = list(self.raw.channels)
        else:
            chans = list(self.physical_channels)
        if self.raw.include_virtual:
            for m in self.measurements:
                vch = getattr(m, "vch", None)
                if vch:
                    chans.extend(int(c) for c in vch)
        return sorted(set(int(c) for c in chans))

    def arm(self) -> None:
        if self.sync is None:
            self.prepare()
        proxy = self.sync.proxy
        try:
            for m in self.measurements:
                m.prepare(proxy)
                log.info("Prepared measurement %s (%s) with %s", m.name, m.type_name, [type(o).__name__ for o in m.objects])
            if self.raw.enabled:
                chans = self._raw_channels()
                path = (self.run_dir.raw_dir if self.run_dir is not None else None)
                fname = str((path / safe_name(self.raw.filename, "raw")) if path is not None else safe_name(self.raw.filename, "raw"))
                self.recorder = Recorder(proxy, fname, chans, self.raw.max_file_size_bytes)
                log.info("Raw recording armed: %s", self.recorder.filename)
        except Exception:
            self.error = traceback.format_exc()
            self.status = RunStatus.FAILED
            self.status_reason = "failed to create measurement objects"
            self._release_all()
            raise
        self.status = RunStatus.ARMED
        self._checkpoint(force=True)

    def start(self) -> None:
        if self.status != RunStatus.ARMED:
            self.arm()
        self.timestamp_start = utc_now_iso()
        self._t_start = time.time()
        self._paused_accum = 0.0
        self.overflows_at_start = self.handle.overflows()
        if self.recorder is not None and self.raw.marker:
            try:
                self.recorder.set_marker(self.raw.marker)
            except Exception as exc:
                log.warning("setMarker failed: %s", exc)
        if self.duration_s:
            self.sync.start_for(int(round(self.duration_s * 1e12)), clear=True)
        else:
            self.sync.clear()
            self.sync.start()
        self.status = RunStatus.RUNNING
        log.info("Group %s started (%d measurements, duration=%s s, raw=%s)", self.name, len(self.measurements), self.duration_s, self.raw.enabled)
        self._write_metadata()
        self._checkpoint(force=True)

    def pause(self) -> None:
        if self.status != RunStatus.RUNNING:
            return
        self.sync.stop()
        self._pause_t0 = time.time()
        self.status = RunStatus.PAUSED
        log.info("Group %s paused", self.name)
        self._checkpoint(force=True)

    def resume(self) -> None:
        if self.status != RunStatus.PAUSED:
            return
        self._paused_accum += time.time() - self._pause_t0
        remaining = None
        if self.duration_s:
            remaining = self.duration_s - self.elapsed_s()
        if remaining is not None and remaining > 0:
            self.sync.start_for(int(round(remaining * 1e12)), clear=False)
        else:
            self.sync.start()
        self.status = RunStatus.RUNNING
        log.info("Group %s resumed", self.name)
        self._checkpoint(force=True)

    def stop(self, reason: str = "stopped by user") -> None:
        if self.status not in (RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.ARMED):
            return
        try:
            self.sync.stop()
        except Exception as exc:
            log.warning("sync stop failed: %s", exc)
        self.finalize(RunStatus.COMPLETED, reason)

    def abort(self, reason: str) -> None:
        try:
            if self.sync is not None:
                self.sync.stop()
        except Exception:
            pass
        self.finalize(RunStatus.INTERRUPTED, reason)

    def clear(self) -> None:
        if self.sync is not None:
            self.sync.clear()
        for m in self.measurements:
            m.last_snapshot = None
        self.snapshots = {}

    # ------------------------------------------------------------------ polling
    def elapsed_s(self) -> float:
        if not self._t_start:
            return 0.0
        end = self._pause_t0 if self.status == RunStatus.PAUSED else time.time()
        return max(0.0, end - self._t_start - self._paused_accum)

    def progress(self) -> Optional[float]:
        if not self.duration_s:
            return None
        return min(1.0, self.elapsed_s() / self.duration_s)

    def poll(self) -> dict[str, Snapshot]:
        """Take a snapshot of every measurement; detect completion of timed runs."""
        if self.status not in (RunStatus.RUNNING, RunStatus.PAUSED):
            return self.snapshots
        snaps: dict[str, Snapshot] = {}
        for m in self.measurements:
            try:
                snaps[m.measurement_id] = m.snapshot()
            except Exception as exc:
                log.exception("Snapshot failed for %s", m.name)
                self.abort(f"snapshot failed for {m.name}: {exc}")
                return self.snapshots
        self.snapshots = snaps
        ov = self.handle.overflows()
        if ov is not None and self.overflows_at_start is not None:
            self.overflows_seen = max(0, ov - self.overflows_at_start)
        if self.status == RunStatus.RUNNING and self.duration_s:
            if not self.sync.is_running() and self.elapsed_s() >= self.duration_s - 0.05:
                self.finalize(RunStatus.COMPLETED, "acquisition duration reached")
        elif self.status == RunStatus.RUNNING and all(s.finished for s in snaps.values()) and snaps:
            self.finalize(RunStatus.COMPLETED, "all measurements reported completion")
        self._checkpoint()
        return self.snapshots

    # ------------------------------------------------------------------ finalize
    def finalize(self, status: RunStatus, reason: str = "") -> None:
        if self.status in (RunStatus.COMPLETED, RunStatus.INTERRUPTED, RunStatus.FAILED):
            return
        self.timestamp_end = utc_now_iso()
        self.status = status
        self.status_reason = reason
        raw_files: list[str] = []
        if self.recorder is not None:
            try:
                self.recorder.stop()
            except Exception as exc:
                log.warning("FileWriter stop failed: %s", exc)
            raw_files = self.recorder.files()
        for m in self.measurements:
            try:
                snap = m.snapshot()
            except Exception as exc:
                log.exception("Final snapshot failed for %s", m.name)
                snap = m.last_snapshot
            try:
                res = m.build_result(snap, self.timestamp_start, self.timestamp_end, status.value, reason, raw_files, {"group_id": self.group_id, "group_name": self.name, "device_id": self.handle.id, "library_configuration": m.library_configuration()})
                self.results[m.measurement_id] = res
                if self.run_dir is not None:
                    res.save(self.run_dir.results_dir, f"{m.type_name}_{m.measurement_id}")
            except Exception:
                log.exception("Saving result failed for %s", m.name)
        self._write_metadata(final=True, raw_files=raw_files)
        self._checkpoint(force=True)
        self._release_all()
        log.info("Group %s finished: %s (%s)", self.name, status.value, reason)
        if self.on_finished is not None:
            try:
                self.on_finished(self)
            except Exception:
                log.exception("on_finished callback failed")

    def _release_all(self) -> None:
        for m in self.measurements:
            try:
                m.release()
            except Exception:
                pass
        self.recorder = None
        self.sync = None

    # ------------------------------------------------------------------ metadata
    def metadata(self, final: bool = False, raw_files: Optional[list[str]] = None) -> dict[str, Any]:
        api = get_api()
        meta: dict[str, Any] = {
            "application_version": self.app_version,
            "swabian_library_version": api.version,
            "group_id": self.group_id,
            "group_name": self.name,
            "run_id": self.run_dir.run_id if self.run_dir is not None else "",
            "timestamp_start": self.timestamp_start,
            "timestamp_stop": self.timestamp_end,
            "duration_requested_s": self.duration_s,
            "duration_actual_s": self.elapsed_s() if self._t_start else None,
            "status": self.status.value,
            "status_reason": self.status_reason,
            "device": self.handle.describe(),
            "hardware_channel_states": self.hardware_states,
            "synchronization_configuration": self.sync_info,
            "reference_clock_state": reference_clock_state(self.handle.tagger) if self.handle.capabilities.get("reference_clock") else {},
            "experiment": self.experiment.to_dict() if self.experiment is not None else {},
            "experiment_config_hash": self.experiment.config_hash if self.experiment is not None else "",
            "measurements": [
                {
                    "measurement_id": m.measurement_id, "measurement_type": m.type_name, "name": m.name,
                    "configuration": m.configuration_record(),
                    "result_json": f"results/{m.type_name}_{m.measurement_id}.json" if self.run_dir is not None else None,
                    "result_npz": f"results/{m.type_name}_{m.measurement_id}.npz" if self.run_dir is not None else None,
                }
                for m in self.measurements
            ],
            "raw_recording": self.raw.to_dict(),
            "raw_files": list(raw_files or (self.recorder.files() if self.recorder is not None else [])),
            "overflows_during_run": self.overflows_seen,
            "analysis_configuration": {},
        }
        if final or self.status == RunStatus.RUNNING:
            meta["tagger_configuration"] = self.handle.get_configuration()
        if self.recorder is not None:
            meta["recorder"] = self.recorder.describe()
        return meta

    def _write_metadata(self, final: bool = False, raw_files: Optional[list[str]] = None) -> None:
        if self.run_dir is None:
            return
        try:
            self.run_dir.write_metadata(self.metadata(final, raw_files))
        except Exception:
            log.exception("Writing measurement.json failed")

    def _checkpoint(self, force: bool = False) -> None:
        if self.run_dir is None:
            return
        now = time.time()
        if not force and now - self._last_checkpoint < 2.0:
            return
        self._last_checkpoint = now
        try:
            self.run_dir.write_status(self.status.value, self.status_reason, self.progress(), {"group_id": self.group_id, "elapsed_s": self.elapsed_s(), "overflows": self.overflows_seen})
        except Exception:
            log.exception("Writing status.json failed")

    # ------------------------------------------------------------------ run card
    def run_card(self) -> dict[str, Any]:
        return {
            "name": self.name, "status": self.status.value, "reason": self.status_reason, "elapsed_s": self.elapsed_s(), "duration_s": self.duration_s,
            "progress": self.progress(), "raw": (self.recorder.describe() if self.recorder is not None else {"enabled": self.raw.enabled}),
            "overflows": self.overflows_seen, "measurements": [{"id": m.measurement_id, "name": m.name, "type": m.type_name, "scalars": (m.last_snapshot.scalars if m.last_snapshot else {})} for m in self.measurements],
            "run_dir": str(self.run_dir.path) if self.run_dir is not None else "",
        }
