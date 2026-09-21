"""Preset CRUD on a directory of JSON files."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from ..models.presets import Preset
from ..models.results import dump_json, load_json


class PresetStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_. " else "_" for c in name).strip() or "preset"
        return self.directory / f"{safe}.json"

    def list(self) -> list[str]:
        names = []
        for p in sorted(self.directory.glob("*.json")):
            try:
                names.append(str(load_json(p).get("preset_name", p.stem)))
            except Exception:
                continue
        return names

    def load(self, name: str) -> Preset:
        p = self._path(name)
        if not p.exists():
            for q in self.directory.glob("*.json"):
                try:
                    if load_json(q).get("preset_name") == name:
                        p = q
                        break
                except Exception:
                    continue
        return Preset.from_dict(load_json(p))

    def save(self, preset: Preset) -> Path:
        p = self._path(preset.preset_name)
        dump_json(preset.to_dict(), p)
        return p

    def delete(self, name: str) -> None:
        p = self._path(name)
        if p.exists():
            p.unlink()

    def rename(self, old: str, new: str) -> Preset:
        preset = self.load(old)
        self.delete(old)
        preset.preset_name = new
        self.save(preset)
        return preset

    def duplicate(self, name: str, new_name: Optional[str] = None) -> Preset:
        preset = self.load(name)
        preset.preset_name = new_name or f"{name} (copy)"
        self.save(preset)
        return preset

    def export(self, name: str, target: str | Path) -> Path:
        src = self._path(name)
        dst = Path(target)
        shutil.copyfile(src, dst)
        return dst

    def import_file(self, source: str | Path, new_name: Optional[str] = None) -> Preset:
        preset = Preset.from_dict(load_json(source))
        if new_name:
            preset.preset_name = new_name
        self.save(preset)
        return preset
