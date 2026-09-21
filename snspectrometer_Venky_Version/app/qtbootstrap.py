"""Select a working Qt binding before qtpy / pyqtgraph are imported.

Preference: PySide6 -> PyQt6 -> PyQt5 -> PySide2. A binding whose QtCore/QtWidgets
extension modules fail to load (e.g. a Qt6 DLL/runtime mismatch in the Python
environment) is skipped with the error recorded in :data:`PROBE_ERRORS`, so the
application still starts on another binding. Set ``QT_API`` to force one.
"""
from __future__ import annotations

import importlib
import os
import sys

PREFERRED = ("pyside6", "pyqt6", "pyqt5", "pyside2")
MODULES = {"pyside6": "PySide6", "pyqt6": "PyQt6", "pyqt5": "PyQt5", "pyside2": "PySide2"}
PROBE_ERRORS: dict[str, str] = {}
BINDING: str = ""


def _purge(module_name: str) -> None:
    for k in list(sys.modules):
        if k == module_name or k.startswith(module_name + "."):
            sys.modules.pop(k, None)


def select_binding() -> str:
    global BINDING
    if BINDING:
        return BINDING
    forced = os.environ.get("QT_API", "").lower().strip()
    order = ([forced] + [p for p in PREFERRED if p != forced]) if forced in MODULES else list(PREFERRED)
    for api in order:
        mod = MODULES[api]
        try:
            importlib.import_module(f"{mod}.QtCore")
            importlib.import_module(f"{mod}.QtWidgets")
        except Exception as exc:  # DLL load failures, missing packages
            PROBE_ERRORS[api] = f"{type(exc).__name__}: {exc}"
            _purge(mod)
            continue
        BINDING = api
        os.environ["QT_API"] = api
        os.environ["FORCE_QT_API"] = "1"
        os.environ["PYQTGRAPH_QT_LIB"] = mod
        return api
    raise ImportError("No working Qt binding found. Tried: " + "; ".join(f"{k}: {v}" for k, v in PROBE_ERRORS.items()))


select_binding()
