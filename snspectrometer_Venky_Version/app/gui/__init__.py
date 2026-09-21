"""Qt GUI (qtpy: PySide6 preferred, PyQt6/PyQt5 fallback). Widgets never call the Swabian API directly; they use the controller."""
from .. import qtbootstrap  # noqa: F401  (select Qt binding first)
