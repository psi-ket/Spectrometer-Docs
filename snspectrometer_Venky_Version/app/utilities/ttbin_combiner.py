"""TTbin combiner built on ``mergeStreamFiles``."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from swabian_backend.replay import merge_files, scan_file, FileScanResult

log = logging.getLogger("snspec.combiner")

MERGE_WARNING = (
    "Merging TTbin files does not automatically rescale incompatible time bases. "
    "Files recorded on different devices or at different times may need external "
    "synchronization / reference-clock information (manual: mergeStreamFiles does not rescale)."
)


@dataclass
class CombinerInput:
    path: str
    channel_offset: int = 0
    time_offset_ps: int = 0
    scan: Optional[FileScanResult] = None

    def preview(self) -> FileScanResult:
        self.scan = scan_file(self.path)
        return self.scan

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "channel_offset": self.channel_offset, "time_offset_ps": self.time_offset_ps, "scan": self.scan.to_dict() if self.scan else None}


@dataclass
class CombinerJob:
    inputs: list[CombinerInput] = field(default_factory=list)
    output: str = ""
    overlap_only: bool = False

    def add(self, path: str) -> CombinerInput:
        ci = CombinerInput(str(path))
        self.inputs.append(ci)
        return ci

    def remove(self, index: int) -> None:
        del self.inputs[index]

    def move(self, index: int, delta: int) -> None:
        j = index + delta
        if 0 <= index < len(self.inputs) and 0 <= j < len(self.inputs):
            self.inputs[index], self.inputs[j] = self.inputs[j], self.inputs[index]

    def validate(self) -> list[str]:
        p = []
        if len(self.inputs) < 1:
            p.append("Add at least one input file")
        if not self.output:
            p.append("Choose an output filename")
        for ci in self.inputs:
            if not Path(ci.path).exists():
                p.append(f"Missing file: {ci.path}")
        chans: dict[int, list[str]] = {}
        for ci in self.inputs:
            if ci.scan:
                for c in ci.scan.channels:
                    chans.setdefault(c + ci.channel_offset, []).append(Path(ci.path).name)
        dup = {c: f for c, f in chans.items() if len(f) > 1}
        if dup:
            p.append("Channel collisions after offsets: " + ", ".join(f"{c} <- {', '.join(f)}" for c, f in dup.items()) + " (consider channel offsets)")
        return p

    def run(self, progress: Optional[Callable[[str], None]] = None) -> str:
        problems = [x for x in self.validate() if not x.startswith("Channel collisions")]
        if problems:
            raise ValueError("; ".join(problems))
        if progress:
            progress("merging with mergeStreamFiles ...")
        out = merge_files(self.output, [ci.path for ci in self.inputs], [ci.channel_offset for ci in self.inputs], [ci.time_offset_ps for ci in self.inputs], self.overlap_only)
        log.info("Merged %d files into %s", len(self.inputs), out)
        if progress:
            progress("scanning result ...")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"inputs": [ci.to_dict() for ci in self.inputs], "output": self.output, "overlap_only": self.overlap_only, "warning": MERGE_WARNING}
