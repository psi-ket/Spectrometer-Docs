"""Run card: live status of one measurement group."""
from __future__ import annotations

import json
from typing import Any, Optional

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget

from ...models.units import format_bytes, format_rate


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.5g}"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {_fmt(x)}" for k, x in list(v.items())[:6]) + (" …" if len(v) > 6 else "")
    if isinstance(v, list):
        return ", ".join(_fmt(x) for x in v[:6]) + (" …" if len(v) > 6 else "")
    return str(v)


class RunCard(QGroupBox):
    action = Signal(str)  # pause | resume | stop | clear

    def __init__(self, title: str = "Run", parent=None):
        super().__init__(title, parent)
        self.status = QLabel("—")
        self.status.setStyleSheet("font-weight: bold;")
        self.progress = QProgressBar(); self.progress.setRange(0, 1000); self.progress.setTextVisible(True)
        self.elapsed = QLabel("0.0 s"); self.raw = QLabel("raw: off"); self.overflow = QLabel("overflows: 0")
        self.details = QTextEdit(); self.details.setReadOnly(True); self.details.setMaximumHeight(110); self.details.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        self.btn_pause = QPushButton("Pause"); self.btn_resume = QPushButton("Resume"); self.btn_stop = QPushButton("Stop"); self.btn_clear = QPushButton("Clear")
        for b, a in ((self.btn_pause, "pause"), (self.btn_resume, "resume"), (self.btn_stop, "stop"), (self.btn_clear, "clear")):
            b.clicked.connect(lambda _=False, a=a: self.action.emit(a))
        grid = QGridLayout(self)
        grid.addWidget(QLabel("Status:"), 0, 0); grid.addWidget(self.status, 0, 1)
        grid.addWidget(QLabel("Elapsed:"), 0, 2); grid.addWidget(self.elapsed, 0, 3)
        grid.addWidget(self.raw, 1, 0, 1, 2); grid.addWidget(self.overflow, 1, 2, 1, 2)
        grid.addWidget(self.progress, 2, 0, 1, 4)
        grid.addWidget(self.details, 3, 0, 1, 4)
        row = QHBoxLayout()
        for b in (self.btn_pause, self.btn_resume, self.btn_stop, self.btn_clear):
            row.addWidget(b)
        grid.addLayout(row, 4, 0, 1, 4)
        self.set_idle()

    def set_idle(self) -> None:
        self.status.setText("IDLE")
        self.progress.setValue(0)
        for b in (self.btn_pause, self.btn_resume, self.btn_stop, self.btn_clear):
            b.setEnabled(False)

    def update_card(self, card: dict[str, Any]) -> None:
        st = card.get("status", "")
        self.status.setText(st + (f" — {card['reason']}" if card.get("reason") else ""))
        colors = {"RUNNING": "#1b7f3b", "PAUSED": "#b36b00", "COMPLETED": "#1f4e79", "INTERRUPTED": "#b00020", "FAILED": "#b00020", "ARMED": "#555"}
        self.status.setStyleSheet(f"font-weight: bold; color: {colors.get(st, '#444')};")
        el = card.get("elapsed_s", 0.0); dur = card.get("duration_s")
        self.elapsed.setText(f"{el:.1f} s" + (f" / {dur:.1f} s" if dur else " (until stopped)"))
        prog = card.get("progress")
        if prog is None:
            self.progress.setRange(0, 0 if st == "RUNNING" else 1)
        else:
            self.progress.setRange(0, 1000); self.progress.setValue(int(prog * 1000))
        raw = card.get("raw", {})
        if raw.get("filename"):
            self.raw.setText(f"raw: {raw.get('total_events', 0):,} events, {format_bytes(raw.get('total_bytes', 0))}, {len(raw.get('files', []))} file(s)")
        else:
            self.raw.setText("raw: " + ("enabled" if raw.get("enabled") else "off"))
        self.overflow.setText(f"overflows: {card.get('overflows', 0)}")
        self.overflow.setStyleSheet("color: #b00020; font-weight: bold;" if card.get("overflows") else "")
        lines = []
        for m in card.get("measurements", []):
            sc = {k: v for k, v in m.get("scalars", {}).items() if k not in ("rolling_statistics_cps",)}
            lines.append(f"[{m['type']}] {m['name']}: " + "; ".join(f"{k}={_fmt(v)}" for k, v in list(sc.items())[:8]))
        if card.get("run_dir"):
            lines.append(f"run dir: {card['run_dir']}")
        self.details.setPlainText("\n".join(lines))
        active = st in ("RUNNING", "PAUSED", "ARMED")
        self.btn_pause.setEnabled(st == "RUNNING"); self.btn_resume.setEnabled(st == "PAUSED"); self.btn_stop.setEnabled(active); self.btn_clear.setEnabled(active)
