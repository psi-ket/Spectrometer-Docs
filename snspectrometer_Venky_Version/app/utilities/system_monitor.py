"""CPU / memory / disk / network sampling with psutil."""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import psutil


class SystemMonitor:
    def __init__(self):
        self._proc = psutil.Process(os.getpid())
        self._last_t = time.time()
        self._last_disk = psutil.disk_io_counters() if hasattr(psutil, "disk_io_counters") else None
        self._last_net = psutil.net_io_counters() if hasattr(psutil, "net_io_counters") else None
        try:
            self._proc.cpu_percent(None)
        except Exception:
            pass

    def sample(self) -> dict[str, Any]:
        now = time.time()
        dt = max(1e-3, now - self._last_t)
        out: dict[str, Any] = {"time": now}
        try:
            out["cpu_percent_system"] = psutil.cpu_percent(None)
            out["cpu_percent_process"] = self._proc.cpu_percent(None) / max(1, psutil.cpu_count() or 1)
            vm = psutil.virtual_memory()
            out["memory_percent"] = vm.percent
            out["memory_available_mb"] = vm.available / 1e6
            out["process_rss_mb"] = self._proc.memory_info().rss / 1e6
        except Exception as exc:
            out["error"] = str(exc)
        try:
            d = psutil.disk_io_counters()
            if d is not None and self._last_disk is not None:
                out["disk_write_mb_s"] = (d.write_bytes - self._last_disk.write_bytes) / 1e6 / dt
                out["disk_read_mb_s"] = (d.read_bytes - self._last_disk.read_bytes) / 1e6 / dt
            self._last_disk = d
        except Exception:
            pass
        try:
            n = psutil.net_io_counters()
            if n is not None and self._last_net is not None:
                out["net_rx_mb_s"] = (n.bytes_recv - self._last_net.bytes_recv) / 1e6 / dt
                out["net_tx_mb_s"] = (n.bytes_sent - self._last_net.bytes_sent) / 1e6 / dt
            self._last_net = n
        except Exception:
            pass
        self._last_t = now
        return out

    @staticmethod
    def disk_free_gb(path: str) -> Optional[float]:
        try:
            return psutil.disk_usage(path).free / 1e9
        except Exception:
            return None
