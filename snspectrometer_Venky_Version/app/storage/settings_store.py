"""Application settings (scientific defaults) and UI preferences, stored separately."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..models.results import dump_json, load_json


def app_home() -> Path:
    base = os.environ.get("SNSPEC_HOME")
    p = Path(base) if base else Path.home() / ".snspectrometer"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class AppSettings:
    swabian_package_path: str = ""
    data_directory: str = str(Path.home() / "SNSPD_Data")
    raw_directory: str = ""      # empty -> inside run directory
    results_directory: str = ""  # empty -> inside run directory
    presets_directory: str = ""  # empty -> <home>/presets
    demo_directory: str = ""     # empty -> <home>/demo_datasets
    autosave: bool = True
    raw_recording_policy: str = "ASK"  # ALWAYS | ASK | NEVER
    plot_refresh_hz: float = 5.0
    health_poll_s: float = 1.0
    log_level: str = "INFO"
    max_ui_memory_mb: int = 512
    theme: str = "system"
    app_mode: str = "BASIC"
    default_duration_s: float = 10.0
    default_max_file_size_mb: int = 1024
    simulator_dataset: str = ""
    auto_connect_simulator_when_no_hardware: bool = True
    scan_network_on_startup: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "AppSettings":
        allowed = set(AppSettings.__dataclass_fields__)
        return AppSettings(**{k: v for k, v in d.items() if k in allowed})

    def presets_dir(self) -> Path:
        return Path(self.presets_directory) if self.presets_directory else app_home() / "presets"

    def demo_dir(self) -> Path:
        return Path(self.demo_directory) if self.demo_directory else app_home() / "demo_datasets"

    def validate(self) -> list[str]:
        p = []
        if self.plot_refresh_hz <= 0 or self.plot_refresh_hz > 60:
            p.append("Plot refresh must be between 0 and 60 Hz")
        if self.raw_recording_policy not in ("ALWAYS", "ASK", "NEVER"):
            p.append("Invalid raw recording policy")
        if self.app_mode not in ("BASIC", "ADVANCED", "DEVELOPER"):
            p.append("Invalid application mode")
        return p


@dataclass
class UIPreferences:
    window_geometry: str = ""
    window_state: str = ""
    tab_order: list[str] = field(default_factory=list)
    hidden_tabs: list[str] = field(default_factory=list)
    disabled_tabs: list[str] = field(default_factory=list)
    plot_styles: dict[str, Any] = field(default_factory=dict)
    recent_experiments: list[str] = field(default_factory=list)
    recent_files: list[str] = field(default_factory=list)
    default_directories: dict[str, str] = field(default_factory=dict)
    last_tab: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "UIPreferences":
        allowed = set(UIPreferences.__dataclass_fields__)
        return UIPreferences(**{k: v for k, v in d.items() if k in allowed})

    def add_recent(self, kind: str, value: str, limit: int = 15) -> None:
        lst = self.recent_files if kind == "file" else self.recent_experiments
        if value in lst:
            lst.remove(value)
        lst.insert(0, value)
        del lst[limit:]


class SettingsStore:
    def __init__(self, home: Path | None = None):
        self.home = home or app_home()
        self.settings_path = self.home / "settings.json"
        self.ui_path = self.home / "ui_preferences.json"

    def load_settings(self) -> AppSettings:
        if self.settings_path.exists():
            try:
                return AppSettings.from_dict(load_json(self.settings_path))
            except Exception:
                pass
        return AppSettings()

    def save_settings(self, s: AppSettings) -> None:
        dump_json(s.to_dict(), self.settings_path)

    def load_ui(self) -> UIPreferences:
        if self.ui_path.exists():
            try:
                return UIPreferences.from_dict(load_json(self.ui_path))
            except Exception:
                pass
        return UIPreferences()

    def save_ui(self, u: UIPreferences) -> None:
        dump_json(u.to_dict(), self.ui_path)
