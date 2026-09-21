"""Simulated live device: a TTbin dataset replayed in real time in an endless loop.

The device is a genuine ``TimeTaggerVirtual``; measurements, virtual channels,
FileWriter, SynchronizedMeasurements, setInputDelay and setDeadtime all behave as
on hardware. Hardware-only settings (trigger level, impedance, event divider)
are reported as unsupported, exactly as the library does for virtual taggers.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from swabian_backend import get_api
from swabian_backend.devices import ConnectionType, TaggerHandle
from swabian_backend.replay import scan_file

log = logging.getLogger("snspec.simulator.device")


class SimulatedDevice:
    def __init__(self, dataset_file: str | Path, name: str = "", speed: float = 1.0):
        TT = get_api().require()
        self.dataset_file = str(dataset_file)
        self.name = name or Path(self.dataset_file).stem
        scan = scan_file(self.dataset_file)
        self.duration_ps = max(1, scan.duration_ps)
        self.scan = scan
        self.tagger = TT.createTimeTaggerVirtual(self.dataset_file)
        self.handle = TaggerHandle(self.tagger, ConnectionType.SIMULATOR, ident=f"SIM:{self.name}", serial=f"SIM-{self.name}", model="Simulated Time Tagger (TTbin loop)", source=self.dataset_file)
        self.handle.notes.append("Simulated device: dataset replayed in an endless loop; trigger level / impedance / event divider are not available on TimeTaggerVirtual. The library emits an overflow marker at each loop boundary (every dataset duration).")
        self.speed = float(speed)
        self._queued: list[int] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.loops = 0

    def start(self) -> None:
        self._stop.clear()
        ids = self.tagger.run(self.speed if self.speed > 0 else 1.0)
        self._queued = [int(i) for i in ids]
        self._queued.append(int(self.tagger.appendFile(self.dataset_file, 0, -1, False)))
        self._thread = threading.Thread(target=self._loop, name="sim-loop", daemon=True)
        self._thread.start()
        log.info("Simulated device %s started (%.2f s dataset, loop)", self.handle.id, self.duration_ps / 1e12)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._queued and self.tagger.waitUntilFinished(self._queued[0], 0):
                    self._queued.pop(0)
                    self._queued.append(int(self.tagger.appendFile(self.dataset_file, 0, -1, False)))
                    self.loops += 1
            except Exception as exc:
                log.debug("sim loop: %s", exc)
                return
            self._stop.wait(0.25)

    def stop(self) -> None:
        self._stop.set()
        try:
            self.tagger.stop()
        except Exception:
            pass

    def close(self) -> None:
        self.stop()
        self.handle.close()

    def describe(self) -> dict[str, Any]:
        return {"dataset": self.dataset_file, "duration_s": self.duration_ps / 1e12, "channels": self.scan.channels, "loops": self.loops, "counts_per_channel": self.scan.counts_per_channel}
