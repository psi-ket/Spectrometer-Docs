"""Measurement manager: owns groups, builds them from the application state, polls them."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from swabian_backend.devices import TaggerHandle
from swabian_backend import get_api

from ..models.channels import DetectorMap
from ..models.experiment import ExperimentConfig
from ..models.sync_state import RunStatus, SyncState
from ..storage.run_store import RunDirectory
from .base import BaseMeasurement, ValidationContext
from .group import MeasurementGroup, RawRecordingConfig
from .registry import create_measurement

log = logging.getLogger("snspec.measurements.manager")


class MeasurementManager:
    def __init__(self, app_version: str = ""):
        self.app_version = app_version
        self.groups: dict[str, MeasurementGroup] = {}
        self._lock = threading.RLock()
        self.listeners: list[Callable[[str, MeasurementGroup], None]] = []

    # ------------------------------------------------------------------ events
    def _emit(self, event: str, group: MeasurementGroup) -> None:
        for cb in list(self.listeners):
            try:
                cb(event, group)
            except Exception:
                log.exception("listener failed")

    # ------------------------------------------------------------------ building
    @staticmethod
    def validation_context(handle: TaggerHandle, sync_state: SyncState) -> ValidationContext:
        api = get_api()
        return ValidationContext(
            available_channels=list(handle.channels_rising) + list(handle.channels_falling),
            capabilities=dict(handle.capabilities),
            sync_allows_cross_device=sync_state.allows_cross_device,
            multi_device=handle.is_multi_device,
            source_index_of=handle.source_index,
            is_virtual=handle.is_virtual,
            library_capabilities=api.capabilities() if api.available else {},
        )

    @staticmethod
    def labels_for(handle: TaggerHandle, detector_map: DetectorMap) -> tuple[dict[int, str], dict[int, float]]:
        labels: dict[int, str] = {}
        wls: dict[int, float] = {}
        for dc in detector_map.by_device(handle.id):
            labels[dc.physical_channel] = dc.label
            if dc.wavelength_nm is not None:
                wls[dc.physical_channel] = float(dc.wavelength_nm)
        return labels, wls

    def build_measurement(self, handle: TaggerHandle, detector_map: DetectorMap, type_name: str, params: dict[str, Any], name: str = "") -> BaseMeasurement:
        labels, wls = self.labels_for(handle, detector_map)
        return create_measurement(type_name, params, labels=labels, wavelengths=wls, name=name)

    def create_group(
        self,
        name: str,
        handle: TaggerHandle,
        measurements: list[BaseMeasurement],
        experiment: ExperimentConfig,
        sync_state: SyncState,
        duration_s: Optional[float],
        raw: RawRecordingConfig,
        data_root: Optional[str],
        hardware_states: Optional[dict[str, Any]] = None,
        sync_info: Optional[dict[str, Any]] = None,
        save: bool = True,
    ) -> MeasurementGroup:
        run_dir = RunDirectory.create(data_root, experiment.experiment_id, name) if (save and data_root) else None
        frozen = experiment.freeze()
        ctx = self.validation_context(handle, sync_state)
        phys = experiment.detector_map.physical_channels(handle.id, enabled_only=True) or list(handle.channels_rising)
        group = MeasurementGroup(name, handle, measurements, duration_s, raw, run_dir, frozen, ctx, phys, hardware_states, sync_info, self.app_version)
        group.on_finished = lambda g: self._emit("finished", g)
        with self._lock:
            self.groups[group.group_id] = group
        self._emit("created", group)
        return group

    # ------------------------------------------------------------------ control
    def get(self, group_id: str) -> Optional[MeasurementGroup]:
        return self.groups.get(group_id)

    def active_groups(self, handle_id: Optional[str] = None) -> list[MeasurementGroup]:
        with self._lock:
            return [g for g in self.groups.values() if g.status.is_active and (handle_id is None or g.handle.id == handle_id)]

    def is_acquiring(self, handle_id: Optional[str] = None) -> bool:
        return bool(self.active_groups(handle_id))

    def poll_all(self) -> dict[str, dict[str, Any]]:
        out = {}
        with self._lock:
            groups = list(self.groups.values())
        for g in groups:
            if g.status in (RunStatus.RUNNING, RunStatus.PAUSED):
                try:
                    out[g.group_id] = g.poll()
                except Exception:
                    log.exception("poll failed for %s", g.name)
        return out

    def stop_all(self, reason: str = "stopped") -> None:
        for g in self.active_groups():
            try:
                g.stop(reason)
            except Exception:
                log.exception("stop failed for %s", g.name)

    def abort_for_handle(self, handle_id: str, reason: str) -> None:
        for g in self.active_groups(handle_id):
            g.abort(reason)

    def remove_finished(self, keep_last: int = 20) -> None:
        with self._lock:
            finished = [g for g in self.groups.values() if not g.status.is_active and g.status != RunStatus.PENDING]
            for g in finished[:-keep_last] if len(finished) > keep_last else []:
                self.groups.pop(g.group_id, None)
