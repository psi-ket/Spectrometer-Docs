"""Application controller: owns the engine objects and exposes Qt signals to the GUI.

No widget touches hardware directly; everything goes through this object.
Blocking library calls (discovery, connection, reference clock locking, dataset
generation, delay calibration, merges, file scans) run in worker threads.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from qtpy.QtCore import QObject, QThreadPool, Signal

from swabian_backend import get_api
from swabian_backend.devices import TaggerHandle

from .. import __version__, APP_NAME
from ..analysis.plugins import AnalysisOutput, get_plugin
from ..analysis.replay_session import ReplaySession
from ..hardware.manager import HardwareManager, DeviceRecord
from ..measurements.base import BaseMeasurement
from ..measurements.group import MeasurementGroup, RawRecordingConfig, PreflightItem
from ..measurements.manager import MeasurementManager
from ..models.channels import DetectorChannel, DetectorMap
from ..models.experiment import ExperimentConfig
from ..models.presets import Preset, PresetMapping, apply_preset_to_map, compare_preset
from ..models.results import MeasurementResult, dump_json, load_json
from ..models.sync_state import RunStatus, SyncState
from ..storage.preset_store import PresetStore
from ..storage.run_store import find_interrupted_runs
from ..storage.settings_store import AppSettings, SettingsStore, UIPreferences, app_home
from ..synchronization.sync_manager import ReferenceClockConfig, SyncManager
from ..utilities.delay_calibration import DelayCalibration
from ..utilities.logging_setup import setup_logging, get_ring, set_level
from ..utilities.sequence_runner import Sequence, SequenceRunner, SequenceStep
from ..utilities.system_monitor import SystemMonitor
from ..utilities.ttbin_combiner import CombinerJob
from .workers import PollThread, Worker

log = logging.getLogger("snspec.controller")


class AppController(QObject):
    # ------------------------------------------------------------------ signals
    log_message = Signal(object)
    hardware_event = Signal(str, object)
    measurement_event = Signal(str, object)
    snapshots = Signal(object)
    replay_snapshots = Signal(object)
    health = Signal(object)
    system_stats = Signal(object)
    error_reported = Signal(str, str, str, str)  # title, reason, details, suggestion
    status_message = Signal(str)
    detector_map_changed = Signal()
    experiment_changed = Signal()
    settings_changed = Signal()
    sync_changed = Signal(str, object)
    task_progress = Signal(str, float, str)
    task_finished = Signal(str, object)
    replay_event = Signal(str, object)
    sequence_event = Signal(str, object)

    def __init__(self, settings_store: Optional[SettingsStore] = None, parent=None):
        super().__init__(parent)
        self.store = settings_store or SettingsStore()
        self.settings: AppSettings = self.store.load_settings()
        self.ui_prefs: UIPreferences = self.store.load_ui()
        self.ring = setup_logging(app_home() / "logs", self.settings.log_level)
        self.ring.listeners.append(self._on_log_record)
        self.api = get_api()
        self.hardware = HardwareManager()
        self.hardware.listeners.append(lambda ev, payload: self.hardware_event.emit(ev, payload))
        self.measurements = MeasurementManager(__version__)
        self.measurements.listeners.append(lambda ev, group: self.measurement_event.emit(ev, group))
        self.sync = SyncManager()
        self.presets = PresetStore(self.settings.presets_dir())
        self.monitor = SystemMonitor()
        self.experiment = ExperimentConfig(software_version=__version__, swabian_library_version=self.api.version)
        self.replay_sessions: dict[str, ReplaySession] = {}
        self.simulated_devices: dict[str, Any] = {}
        self.delay_calibrations: dict[str, DelayCalibration] = {}
        self.virtual_channels: dict[str, list] = {}
        self.interrupted_runs = []
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(max(4, self.pool.maxThreadCount()))
        self.poller = PollThread(self)
        self.poller.refresh_hz = float(self.settings.plot_refresh_hz)
        self.poller.health_interval_s = float(self.settings.health_poll_s)
        self.poller.snapshots.connect(self.snapshots)
        self.poller.replay_snapshots.connect(self.replay_snapshots)
        self.poller.health.connect(self.health)
        self.poller.system.connect(self.system_stats)
        self.poller.start()
        self.sequence_runner = SequenceRunner(self._sequence_handlers(), self._sequence_condition, lambda e, p: self.sequence_event.emit(e, p))
        self._workers: list[Worker] = []
        log.info("%s %s started; Swabian library: %s", APP_NAME, __version__, self.api.diagnostics())
        if not self.api.available:
            self.report_error("Swabian TimeTagger library not available", self.api.error, "", "Install the Swabian Time Tagger software (Python package 'TimeTagger'). The application runs in offline mode without it.")
        elif not self.api.version_supported():
            self.report_error("Unsupported Swabian TimeTagger version", f"Expected API {self.api.diagnostics()['expected_api']}, detected {self.api.version}", "", "The adapter was verified against 2.21 / 2.22. Other versions may work but are untested.")
        try:
            self.interrupted_runs = find_interrupted_runs(self.settings.data_directory, mark=True)
            if self.interrupted_runs:
                log.warning("%d interrupted run(s) detected and marked INTERRUPTED", len(self.interrupted_runs))
        except Exception:
            log.exception("interrupted-run scan failed")

    # ================================================================== infrastructure
    def _on_log_record(self, entry: dict[str, Any]) -> None:
        try:
            self.log_message.emit(entry)
        except RuntimeError:
            pass

    def report_error(self, title: str, reason: str, details: str = "", suggestion: str = "") -> None:
        log.error("%s: %s %s", title, reason, ("| " + details.strip().splitlines()[-1]) if details else "")
        self.error_reported.emit(title, reason, details, suggestion)

    def run_async(self, name: str, fn: Callable, *args: Any, on_done: Optional[Callable[[Any], None]] = None, on_error: Optional[Callable[[str, str], None]] = None, **kwargs: Any) -> Worker:
        w = Worker(fn, *args, name=name, **kwargs)
        w.signals.progress.connect(lambda frac, msg: self.task_progress.emit(name, frac, msg))

        def done(result):
            self.task_finished.emit(name, result)
            if on_done:
                on_done(result)

        def err(msg, tb):
            if on_error:
                on_error(msg, tb)
            else:
                self.report_error(f"{name} failed", msg, tb)
            self.task_finished.emit(name, None)

        w.signals.finished.connect(done)
        w.signals.error.connect(err)
        self.pool.start(w)
        return w

    def diagnostics(self) -> dict[str, Any]:
        import platform
        import sys
        import numpy, scipy, pyqtgraph, matplotlib
        import qtpy
        from . import workers  # noqa: F401
        from .. import qtbootstrap
        return {
            "application": f"{APP_NAME} {__version__}",
            "python": sys.version.split()[0],
            "swabian": self.api.diagnostics(),
            "numpy": numpy.__version__, "scipy": scipy.__version__, "qt_binding": f"{qtpy.API_NAME} {qtpy.QT_VERSION}", "qt_probe_errors": qtbootstrap.PROBE_ERRORS, "pyqtgraph": pyqtgraph.__version__, "matplotlib": matplotlib.__version__,
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "settings_home": str(app_home()),
        }

    # ================================================================== hardware
    def discover_async(self) -> None:
        self.run_async("discover", self.hardware.discover, self.settings.scan_network_on_startup)

    def connect_local_async(self, serial: str = "", resolution: Optional[str] = None) -> None:
        def done(rec: DeviceRecord):
            self._after_connect(rec.handle)

        self.run_async("connect", self.hardware.connect_local, serial, resolution, on_done=done, on_error=lambda m, tb: self.report_error("Could not connect to Time Tagger", m, tb, "Check the USB connection, that no other process (e.g. Time Tagger Lab) holds the device, and the serial number."))

    def connect_network_async(self, addresses: list[str]) -> None:
        def done(rec: DeviceRecord):
            self._after_connect(rec.handle)

        self.run_async("connect_network", self.hardware.connect_network, addresses, on_done=done, on_error=lambda m, tb: self.report_error("Could not connect to Time Tagger server(s)", m, tb, "Verify host:port, that the server runs in Control/Listen mode, and that multiple servers are synchronized (the library refuses unsynchronized multi-server connections)."))

    def connect_simulator_async(self, dataset: Optional[str] = None, scenario: str = "spectrometer_jsi", duration_s: float = 10.0) -> None:
        from simulator.demo_dataset import SCENARIOS, generate_dataset, load_dataset_metadata
        from simulator.device import SimulatedDevice

        def job(progress_cb=None, stop_flag=None):
            path = dataset
            if not path:
                out = self.settings.demo_dir()
                path = str(out / f"{scenario}.ttbin")
                if not Path(path).exists():
                    scen = SCENARIOS[scenario](duration_s)
                    generate_dataset(scen, out, progress_cb, stop_flag)
            dev = SimulatedDevice(path)
            dev.start()
            meta = load_dataset_metadata(path)
            return dev, meta

        def done(res):
            dev, meta = res
            self.simulated_devices[dev.handle.id] = dev
            self.hardware.register_handle(dev.handle)
            dm = meta.get("detector_map")
            if dm:
                for ch in dm.get("channels", []):
                    ch["tagger_id"] = dev.handle.id
                    if not self.experiment.detector_map.get_physical(dev.handle.id, int(ch["physical_channel"])):
                        self.experiment.detector_map.add(DetectorChannel.from_dict(ch))
                if meta.get("scenario", {}).get("pump_wavelength_nm"):
                    self.experiment.pump_wavelength_nm = meta["scenario"]["pump_wavelength_nm"]
            self._after_connect(dev.handle)

        self.run_async("simulator", job, on_done=done)

    def _after_connect(self, handle: TaggerHandle) -> None:
        self.experiment.detector_map.ensure_device_channels(handle.id, handle.channels_rising, handle.source_index)
        self.experiment.devices = [self.hardware.devices[d].handle.describe() for d in self.hardware.devices]
        self.detector_map_changed.emit()
        state, reason = self.sync.compute_state(handle)
        self.sync_changed.emit(handle.id, self.sync.info(handle))
        self.status_message.emit(f"Connected {handle.id} ({handle.model}); sync state {state.value}: {reason}")

    def disconnect(self, device_id: str) -> None:
        if self.measurements.is_acquiring(device_id):
            self.measurements.abort_for_handle(device_id, "device disconnected by user")
        dev = self.simulated_devices.pop(device_id, None)
        if dev is not None:
            dev.stop()
        self.hardware.disconnect(device_id)
        self.experiment.devices = [self.hardware.devices[d].handle.describe() for d in self.hardware.devices]
        self.detector_map_changed.emit()

    def device_ids(self) -> list[str]:
        return list(self.hardware.devices)

    def handle(self, device_id: str) -> TaggerHandle:
        return self.hardware.handle(device_id)

    def device_record(self, device_id: str) -> Optional[DeviceRecord]:
        return self.hardware.get(device_id)

    def is_acquiring(self, device_id: Optional[str] = None) -> bool:
        return self.measurements.is_acquiring(device_id)

    # ================================================================== channels
    def detector_map(self) -> DetectorMap:
        return self.experiment.detector_map

    def apply_channel(self, dc: DetectorChannel, fields: Optional[set[str]] = None, force: bool = False) -> list:
        if self.measurements.is_acquiring(dc.tagger_id) and not force:
            raise RuntimeError("Acquisition is active on this device; stop it or confirm the change explicitly")
        changes = self.hardware.apply_detector_channel(dc, fields)
        self.detector_map_changed.emit()
        return changes

    def read_channel_states(self, device_id: str) -> dict[int, Any]:
        return self.hardware.read_channel_states(device_id)

    def channel_state_dicts(self, device_id: str) -> dict[int, dict[str, Any]]:
        rec = self.hardware.get(device_id)
        if rec is None:
            return {}
        return {ch: st.to_dict() for ch, st in rec.channel_states.items()}

    # ================================================================== presets
    def preset_from_current(self, name: str) -> Preset:
        serials = {d: self.hardware.devices[d].handle.serial or d for d in self.hardware.devices}
        models = {d: self.hardware.devices[d].handle.model for d in self.hardware.devices}
        return Preset.from_detector_map(name, self.experiment.detector_map, serials, models)

    def default_mapping(self, preset: Preset) -> PresetMapping:
        m = PresetMapping()
        connected = {self.hardware.devices[d].handle.serial: d for d in self.hardware.devices}
        ids = list(self.hardware.devices)
        for i, dp in enumerate(preset.devices):
            if dp.serial in connected:
                m.device_map[dp.serial] = connected[dp.serial]
            elif i < len(ids):
                m.device_map[dp.serial] = ids[i]
        return m

    def apply_preset(self, preset: Preset, mapping: PresetMapping, push_to_hardware: bool = True, force: bool = False) -> tuple[list[DetectorChannel], list[str]]:
        valid = {d: list(self.hardware.devices[d].handle.channels_rising) for d in self.hardware.devices}
        changed, warnings = apply_preset_to_map(preset, self.experiment.detector_map, mapping, valid)
        if push_to_hardware:
            for dc in changed:
                try:
                    self.apply_channel(dc, force=force)
                except Exception as exc:
                    warnings.append(f"{dc.logical_id}: {exc}")
        for k, v in preset.experiment_defaults.items():
            if hasattr(self.experiment, k):
                setattr(self.experiment, k, v)
        log.info("Preset %s applied: %d channels, %d warnings", preset.preset_name, len(changed), len(warnings))
        self.detector_map_changed.emit()
        return changed, warnings

    def compare_preset(self, preset: Preset, mapping: PresetMapping):
        states = {d: self.channel_state_dicts(d) for d in self.hardware.devices}
        return compare_preset(preset, mapping, states)

    # ================================================================== measurements
    def sync_state(self, device_id: str) -> SyncState:
        return self.sync.compute_state(self.handle(device_id))[0]

    def build_group(self, name: str, device_id: str, specs: list[dict[str, Any]], duration_s: Optional[float], raw: RawRecordingConfig, save: bool = True) -> MeasurementGroup:
        handle = self.handle(device_id)
        meas: list[BaseMeasurement] = []
        for spec in specs:
            meas.append(self.measurements.build_measurement(handle, self.experiment.detector_map, spec["type"], spec.get("params", {}), spec.get("name", "")))
        state = self.sync_state(device_id)
        hw_states = self.channel_state_dicts(device_id)
        group = self.measurements.create_group(name, handle, meas, self.experiment, state, duration_s, raw, self.settings.data_directory if save else None, {str(k): v for k, v in hw_states.items()}, self.sync.info(handle), save)
        return group

    def start_group(self, group: MeasurementGroup) -> list[PreflightItem]:
        items = group.preflight()
        if not group.preflight_ok:
            return items
        try:
            group.prepare()
            group.arm()
            group.start()
        except Exception as exc:
            self.report_error("Measurement could not start", str(exc), group.error, "Check the parameters and the device state; see the log for the library message.")
            raise
        self.measurement_event.emit("started", group)
        return items

    def run_group(self, name: str, device_id: str, specs: list[dict[str, Any]], duration_s: Optional[float], raw: RawRecordingConfig, save: bool = True) -> tuple[MeasurementGroup, list[PreflightItem]]:
        group = self.build_group(name, device_id, specs, duration_s, raw, save)
        items = self.start_group(group)
        return group, items

    def group_action(self, group: MeasurementGroup, action: str) -> None:
        if action == "pause":
            group.pause()
        elif action == "resume":
            group.resume()
        elif action == "stop":
            group.stop("stopped by user")
        elif action == "clear":
            group.clear()
        elif action == "abort":
            group.abort("aborted by user")
        self.measurement_event.emit(action, group)

    def active_groups(self, device_id: Optional[str] = None) -> list[MeasurementGroup]:
        return self.measurements.active_groups(device_id)

    # ================================================================== synchronization
    def sync_info(self, device_id: str) -> dict[str, Any]:
        return self.sync.info(self.handle(device_id))

    def apply_reference_clock_async(self, device_id: str, cfg: ReferenceClockConfig) -> None:
        if self.measurements.is_acquiring(device_id):
            raise RuntimeError("Stop the acquisition before changing the reference clock: the time base changes and data before/after are not comparable")
        handle = self.handle(device_id)

        def done(state):
            self.sync_changed.emit(device_id, self.sync.info(handle))
            self.status_message.emit(f"Reference clock on {device_id}: {'locked' if state.get('is_locked') else 'not locked'}")

        self.run_async("reference_clock", self.sync.apply_reference_clock, handle, cfg, on_done=done, on_error=lambda m, tb: self.report_error("Reference clock configuration failed", m, tb, "Check the clock signal, frequency and channel. The manual notes overflows are expected until the PLL locks."))

    # ================================================================== replay / analysis
    def open_replay(self, filename: str, begin_ps: int = 0, duration_ps: int = -1) -> ReplaySession:
        session = ReplaySession(filename, begin_ps, duration_ps)
        self.replay_sessions[session.session_id] = session
        self.ui_prefs.add_recent("file", filename)
        self.store.save_ui(self.ui_prefs)
        self.run_async("scan_file", session.scan_in_background, on_done=lambda scan: self.replay_event.emit("scanned", {"session_id": session.session_id, "scan": scan}))
        self.replay_event.emit("opened", {"session_id": session.session_id})
        return session

    def close_replay(self, session_id: str) -> None:
        s = self.replay_sessions.pop(session_id, None)
        if s is not None:
            s.close()
            self.replay_event.emit("closed", {"session_id": session_id})

    def replay_measurement(self, session: ReplaySession, type_name: str, params: dict[str, Any], name: str = "", labels: Optional[dict[int, str]] = None, wavelengths: Optional[dict[int, float]] = None) -> BaseMeasurement:
        from ..measurements.registry import create_measurement
        m = create_measurement(type_name, params, labels=labels, wavelengths=wavelengths, name=name)
        session.add_measurement(m)
        return m

    def run_plugin(self, result: MeasurementResult, plugin_name: str, params: dict[str, Any]) -> AnalysisOutput:
        plugin = get_plugin(plugin_name)
        problems = plugin.validate(result, params)
        if problems:
            raise ValueError("; ".join(problems))
        out = plugin.run(result, params)
        out.provenance["software_version"] = __version__
        log.info("Analysis %s on %s (%s): %s", plugin_name, result.measurement_id, result.measurement_type, {k: v for k, v in out.scalars.items() if isinstance(v, (int, float, str))})
        return out

    # ================================================================== utilities
    def delay_calibration_measure_async(self, device_id: str, reference: int, channels: list[int], binwidth_ps: int, n_bins: int, duration_s: float, window_ps: int, reset_first: bool) -> None:
        if self.measurements.is_acquiring(device_id):
            raise RuntimeError("Stop the acquisition on this device before calibrating delays")
        dc = DelayCalibration(self.handle(device_id), reference, channels, binwidth_ps, n_bins, duration_s, window_ps, reset_first)
        self.delay_calibrations[device_id] = dc
        self.run_async("delay_calibration", dc.measure, on_done=lambda res: self.task_finished.emit("delay_calibration_measured", device_id))

    def delay_calibration_apply(self, device_id: str, accepted: dict[int, bool]) -> dict[int, int]:
        dc = self.delay_calibrations[device_id]
        applied = dc.apply(accepted)
        for ch, d in applied.items():
            det = self.experiment.detector_map.get_physical(device_id, ch)
            if det is not None:
                det.input_delay_ps = int(d)
        self.hardware.read_channel_states(device_id)
        self.detector_map_changed.emit()
        return applied

    def delay_calibration_verify_async(self, device_id: str) -> None:
        dc = self.delay_calibrations[device_id]
        self.run_async("delay_verification", dc.verify, on_done=lambda res: self.task_finished.emit("delay_calibration_verified", device_id))

    def combiner_run_async(self, job: CombinerJob) -> None:
        self.run_async("merge", job.run, on_done=lambda out: self.status_message.emit(f"Merged into {out}"), on_error=lambda m, tb: self.report_error("Merge failed", m, tb, "Check input files, channel offsets and disk space."))

    def generate_dataset_async(self, scenario: str, duration_s: float, output_dir: Optional[str] = None) -> None:
        from simulator.demo_dataset import SCENARIOS, generate_dataset
        out = Path(output_dir) if output_dir else self.settings.demo_dir()
        scen = SCENARIOS[scenario](duration_s)
        self.run_async("generate_dataset", generate_dataset, scen, out, on_done=lambda meta: self.status_message.emit(f"Generated {meta['file']}"))

    # ================================================================== sequences
    def _sequence_handlers(self) -> dict[str, Callable]:
        def h_preset(step: SequenceStep, runner: SequenceRunner):
            preset = self.presets.load(step.configuration["preset"])
            changed, warnings = self.apply_preset(preset, self.default_mapping(preset), step.configuration.get("push_to_hardware", True), force=True)
            return {"changed": len(changed), "warnings": warnings}

        def h_verify(step, runner):
            problems = []
            for dev_id in self.hardware.devices:
                rec = self.hardware.devices[dev_id]
                ov = rec.handle.overflows()
                if ov:
                    problems.append(f"{dev_id}: {ov} overflows")
            if not self.hardware.devices:
                problems.append("no device connected")
            if problems and step.configuration.get("strict", False):
                raise RuntimeError("; ".join(problems))
            return {"problems": problems}

        def h_group(step, runner):
            cfg = step.configuration
            raw = RawRecordingConfig(**cfg.get("raw", {"enabled": False}))
            group, items = self.run_group(cfg.get("name", step.name), cfg["device_id"], cfg["measurements"], step.duration_s or cfg.get("duration_s"), raw, step.save)
            if not group.preflight_ok:
                raise RuntimeError("preflight blocked: " + "; ".join(i.label for i in items if not i.ok))
            while group.status.is_active:
                if runner.stop_requested or runner.skip_requested:
                    group.stop("sequence stopped/skipped")
                    break
                runner.wait_if_paused()
                time.sleep(0.2)
            return {"group_id": group.group_id, "status": group.status.value, "run_dir": str(group.run_dir.path) if group.run_dir else ""}

        def h_delay(step, runner):
            cfg = step.configuration
            dc = DelayCalibration(self.handle(cfg["device_id"]), cfg["reference"], cfg["channels"], cfg.get("binwidth_ps", 10), cfg.get("n_bins", 4000), step.duration_s or cfg.get("duration_s", 2.0), cfg.get("window_ps", 0), cfg.get("reset_first", True))
            res = dc.measure(stop_flag=lambda: runner.stop_requested)
            if cfg.get("auto_apply", False):
                dc.apply({ch: r.significant for ch, r in res.items()})
            self.delay_calibrations[cfg["device_id"]] = dc
            return dc.report()

        def h_wait(step, runner):
            t0 = time.time()
            while time.time() - t0 < (step.duration_s or 0):
                if runner.stop_requested or runner.skip_requested:
                    break
                time.sleep(0.1)
            return {"waited_s": time.time() - t0}

        def h_save(step, runner):
            return {"experiment": self.save_experiment()}

        return {"apply_preset": h_preset, "verify_hardware": h_verify, "measurement_group": h_group, "delay_calibration": h_delay, "wait": h_wait, "save_all": h_save}

    def _sequence_condition(self, cond: str, step: SequenceStep) -> tuple[bool, str]:
        if cond == "hardware_connected":
            return bool(self.hardware.devices), "no device connected"
        if cond == "sync_ok":
            for d in self.hardware.devices:
                st = self.sync_state(d)
                if not st.allows_cross_device:
                    return False, f"{d}: {st.value}"
            return True, ""
        if cond == "no_overflow":
            for d, rec in self.hardware.devices.items():
                if rec.handle.overflows():
                    return False, f"{d} reports overflows"
            return True, ""
        if cond == "not_acquiring":
            return not self.measurements.is_acquiring(), "acquisition active"
        return True, ""

    def run_sequence(self, sequence: Sequence) -> None:
        self.sequence_runner.run(sequence)

    # ================================================================== experiment / settings
    def new_experiment(self, name: str = "Untitled experiment") -> None:
        dm = self.experiment.detector_map
        self.experiment = ExperimentConfig(experiment_name=name, software_version=__version__, swabian_library_version=self.api.version, detector_map=dm)
        self.experiment.devices = [self.hardware.devices[d].handle.describe() for d in self.hardware.devices]
        self.experiment_changed.emit()

    def save_experiment(self, path: Optional[str] = None) -> str:
        p = Path(path) if path else Path(self.settings.data_directory) / self.experiment.experiment_id / "experiment.json"
        dump_json(self.experiment.to_dict(), p)
        self.ui_prefs.add_recent("experiment", str(p))
        self.store.save_ui(self.ui_prefs)
        log.info("Experiment saved to %s", p)
        return str(p)

    def load_experiment(self, path: str) -> None:
        self.experiment = ExperimentConfig.from_dict(load_json(path))
        self.ui_prefs.add_recent("experiment", path)
        self.store.save_ui(self.ui_prefs)
        self.experiment_changed.emit()
        self.detector_map_changed.emit()

    def update_settings(self, new: AppSettings) -> None:
        problems = new.validate()
        if problems:
            raise ValueError("; ".join(problems))
        self.settings = new
        self.store.save_settings(new)
        self.poller.refresh_hz = float(new.plot_refresh_hz)
        self.poller.health_interval_s = float(new.health_poll_s)
        set_level(new.log_level)
        self.presets = PresetStore(new.presets_dir())
        self.settings_changed.emit()

    def save_ui_prefs(self) -> None:
        self.store.save_ui(self.ui_prefs)

    # ================================================================== logs
    def logs_text(self, since: Optional[float] = None) -> str:
        ring = get_ring()
        return ring.dump(since) if ring else ""

    def export_logs(self, path: str, since: Optional[float] = None) -> str:
        Path(path).write_text(self.logs_text(since), encoding="utf-8")
        return path

    # ================================================================== shutdown
    def shutdown(self) -> None:
        log.info("Shutting down")
        try:
            self.sequence_runner.stop()
        except Exception:
            pass
        try:
            self.measurements.stop_all("application shutdown")
        except Exception:
            log.exception("stop_all failed")
        for sid in list(self.replay_sessions):
            try:
                self.close_replay(sid)
            except Exception:
                pass
        for dev in list(self.simulated_devices.values()):
            try:
                dev.stop()
            except Exception:
                pass
        self.poller.stop()
        self.hardware.disconnect_all()
        self.store.save_ui(self.ui_prefs)
        self.store.save_settings(self.settings)
