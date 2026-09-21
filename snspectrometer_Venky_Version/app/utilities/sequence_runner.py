"""Measurement sequence engine (steps executed by controller-provided handlers)."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

log = logging.getLogger("snspec.sequence")

STEP_TYPES = ("apply_preset", "verify_hardware", "measurement_group", "delay_calibration", "wait", "save_all")


@dataclass
class SequenceStep:
    name: str
    type: str
    configuration: dict[str, Any] = field(default_factory=dict)
    duration_s: Optional[float] = None
    preconditions: list[str] = field(default_factory=list)   # e.g. "hardware_connected", "sync_ok", "no_overflow"
    postconditions: list[str] = field(default_factory=list)  # e.g. "results_saved", "min_counts:1000"
    save: bool = True
    enabled: bool = True
    status: str = "pending"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "SequenceStep":
        allowed = set(SequenceStep.__dataclass_fields__)
        return SequenceStep(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class Sequence:
    name: str
    steps: list[SequenceStep] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "steps": [s.to_dict() for s in self.steps]}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Sequence":
        return Sequence(str(d.get("name", "Sequence")), [SequenceStep.from_dict(s) for s in d.get("steps", [])], str(d.get("description", "")))


StepHandler = Callable[[SequenceStep, "SequenceRunner"], dict[str, Any]]
ConditionCheck = Callable[[str, SequenceStep], tuple[bool, str]]


class SequenceRunner:
    def __init__(self, handlers: dict[str, StepHandler], condition_check: Optional[ConditionCheck] = None, on_event: Optional[Callable[[str, dict[str, Any]], None]] = None):
        self.handlers = handlers
        self.condition_check = condition_check or (lambda cond, step: (True, ""))
        self.on_event = on_event or (lambda e, p: None)
        self.sequence: Optional[Sequence] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._skip = threading.Event()
        self._repeat = threading.Event()
        self.current_index = -1
        self.state = "idle"
        self.results: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ control
    def run(self, sequence: Sequence) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("A sequence is already running")
        self.sequence = sequence
        self._stop.clear()
        self._pause.clear()
        self._skip.clear()
        self.results = []
        self._thread = threading.Thread(target=self._loop, name="sequence-runner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()

    def pause(self) -> None:
        self._pause.set()
        self.state = "paused"
        self.on_event("paused", {})

    def resume(self) -> None:
        self._pause.clear()
        self.state = "running"
        self.on_event("resumed", {})

    def skip(self) -> None:
        self._skip.set()

    def repeat(self) -> None:
        self._repeat.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    @property
    def skip_requested(self) -> bool:
        return self._skip.is_set()

    def wait_if_paused(self) -> None:
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.1)

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        self.state = "running"
        self.on_event("started", {"sequence": self.sequence.name})
        i = 0
        steps = self.sequence.steps
        while i < len(steps):
            if self._stop.is_set():
                break
            self.wait_if_paused()
            step = steps[i]
            self.current_index = i
            if not step.enabled:
                step.status = "skipped"
                i += 1
                continue
            self._skip.clear()
            self._repeat.clear()
            step.status = "running"
            step.message = ""
            self.on_event("step_started", {"index": i, "step": step.to_dict()})
            ok, why = self._check_conditions(step.preconditions, step)
            if not ok:
                step.status = "blocked"
                step.message = f"precondition failed: {why}"
                self.on_event("step_finished", {"index": i, "step": step.to_dict()})
                log.error("Sequence step %s blocked: %s", step.name, why)
                break
            handler = self.handlers.get(step.type)
            result: dict[str, Any] = {}
            try:
                if handler is None:
                    raise KeyError(f"no handler for step type {step.type!r}")
                result = handler(step, self) or {}
                if self._skip.is_set():
                    step.status = "skipped"
                else:
                    ok, why = self._check_conditions(step.postconditions, step, result)
                    step.status = "done" if ok else "failed"
                    step.message = "" if ok else f"postcondition failed: {why}"
            except Exception as exc:
                log.exception("Sequence step %s failed", step.name)
                step.status = "failed"
                step.message = str(exc)
            self.results.append({"index": i, "name": step.name, "status": step.status, "message": step.message, "result": result})
            self.on_event("step_finished", {"index": i, "step": step.to_dict(), "result": result})
            if step.status == "failed" and not self._repeat.is_set():
                break
            if self._repeat.is_set():
                self._repeat.clear()
                continue
            i += 1
        self.state = "stopped" if self._stop.is_set() else "finished"
        self.current_index = -1
        self.on_event("finished", {"state": self.state, "results": self.results})

    def _check_conditions(self, conds: list[str], step: SequenceStep, result: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
        for c in conds:
            try:
                ok, why = self.condition_check(c, step) if result is None else self.condition_check(c, step)
            except Exception as exc:
                ok, why = False, str(exc)
            if not ok:
                return False, f"{c}: {why}"
        return True, ""
