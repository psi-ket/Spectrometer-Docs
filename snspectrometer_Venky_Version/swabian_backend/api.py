"""Single import point for the Swabian TimeTagger Python library.

The library is imported lazily. If it is missing, :func:`get_api` still returns
an object whose ``available`` flag is False and whose ``error`` explains why, so
the application can start in simulator/offline mode and show a diagnostic.
"""
from __future__ import annotations

import importlib
import logging
import threading
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("snspec.backend")

#: API generations this adapter was written and tested against.
SUPPORTED_VERSION_PREFIXES = ("2.21", "2.22")
EXPECTED_API_DESCRIPTION = "2.21.x - 2.22.x"


class SwabianUnavailable(RuntimeError):
    """Raised when an operation needs the Swabian library but it is not importable."""


@dataclass
class SwabianAPI:
    module: Any = None
    version: str = ""
    error: str = ""
    source_name: str = ""
    is_mock: bool = False
    _logger_installed: bool = field(default=False, repr=False)

    # ------------------------------------------------------------------ status
    @property
    def available(self) -> bool:
        return self.module is not None

    def require(self) -> Any:
        if self.module is None:
            raise SwabianUnavailable(
                "The Swabian TimeTagger Python package is not available: " + (self.error or "unknown error")
            )
        return self.module

    def has(self, name: str) -> bool:
        return self.module is not None and hasattr(self.module, name)

    def version_supported(self) -> bool:
        if not self.version:
            return False
        return any(self.version.startswith(p) for p in SUPPORTED_VERSION_PREFIXES)

    def capabilities(self) -> dict[str, bool]:
        """Feature flags derived from the installed module (never guessed)."""
        names = [
            "GatedCounter", "CountBetweenMarkers", "CorrelationPairs", "Correlation",
            "Histogram", "Histogram2D", "HistogramND", "HistogramLogBins", "Counter", "Countrate",
            "Coincidences", "Coincidence", "Combinations", "DelayedChannels", "GatedChannels",
            "TimeDifferences", "TimeDifferencesND", "TimeTagStream", "FileWriter", "FileReader",
            "SynchronizedMeasurements", "createTimeTaggerVirtual", "createTimeTaggerNetwork",
            "scanTimeTagger", "scanTimeTaggerServers", "mergeStreamFiles", "setLogger",
            "Experimental", "hasTimeTaggerVirtualLicense", "getTimeTaggerServerInfo",
        ]
        return {n: self.has(n) for n in names}

    def diagnostics(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "available": self.available,
            "version": self.version,
            "expected_api": EXPECTED_API_DESCRIPTION,
            "version_supported": self.version_supported(),
            "source": self.source_name,
            "error": self.error,
            "is_mock": self.is_mock,
        }
        if self.available:
            d["capabilities"] = self.capabilities()
            try:
                d["virtual_license"] = bool(self.module.hasTimeTaggerVirtualLicense())
            except Exception as exc:  # pragma: no cover - depends on installation
                d["virtual_license"] = f"unknown ({exc})"
            for fn in ("getCompilerVersion", "getCompilationTimestamp"):
                try:
                    d[fn] = str(getattr(self.module, fn)())
                except Exception:
                    pass
        return d

    # ------------------------------------------------------------------ logging
    def install_logger(self, callback: Callable[[int, str], None]) -> bool:
        """Route library log messages into the application via ``setLogger``."""
        if not self.has("setLogger"):
            return False
        try:
            self.module.setLogger(callback)
            self._logger_installed = True
            return True
        except Exception as exc:  # pragma: no cover
            log.warning("setLogger failed: %s", exc)
            return False

    # ------------------------------------------------------------------ constants
    @property
    def CHANNEL_UNUSED(self) -> int:
        m = self.require()
        return int(getattr(m, "CHANNEL_UNUSED"))


_lock = threading.Lock()
_api: Optional[SwabianAPI] = None


def _import_library() -> SwabianAPI:
    errors = []
    for name in ("Swabian.TimeTagger", "TimeTagger"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mod = importlib.import_module(name)
            version = ""
            try:
                version = str(mod.getVersion())
            except Exception as exc:  # pragma: no cover
                errors.append(f"{name}: getVersion failed: {exc}")
            api = SwabianAPI(module=mod, version=version, source_name=name)
            log.info("Swabian TimeTagger library loaded from %s, version %s", name, version)
            return api
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return SwabianAPI(module=None, error="; ".join(errors), source_name="")


def get_api() -> SwabianAPI:
    global _api
    with _lock:
        if _api is None:
            _api = _import_library()
        return _api


def set_api_module(module: Any, version: str = "mock", is_mock: bool = True) -> SwabianAPI:
    """Replace the library module (used by unit tests and the mock backend)."""
    global _api
    with _lock:
        _api = SwabianAPI(module=module, version=version, source_name="mock" if is_mock else "custom", is_mock=is_mock)
        return _api


def reset_api() -> None:
    global _api
    with _lock:
        _api = None
