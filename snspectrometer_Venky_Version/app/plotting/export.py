"""Publication export via matplotlib, re-plotted from the stored arrays.

Formats: png, svg, pdf. Display settings never alter the data.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


@dataclass
class PlotExportOptions:
    width_in: float = 6.0
    height_in: float = 4.0
    dpi: int = 300
    font_size: float = 10.0
    line_width: float = 1.2
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    legend: bool = True
    grid: bool = True
    log_x: bool = False
    log_y: bool = False
    x_limits: Optional[tuple[float, float]] = None
    y_limits: Optional[tuple[float, float]] = None
    colormap: str = "viridis"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _axis_label(label: str, unit: str) -> str:
    return f"{label} [{unit}]" if unit else label


def export_lines(path: str | Path, x: np.ndarray, ys: list[np.ndarray], labels: list[str], opts: PlotExportOptions, x_unit: str = "", y_unit: str = "", step: bool = False, errors: Optional[list[np.ndarray]] = None, symbols: bool = False) -> Path:
    plt.rcParams.update({"font.size": opts.font_size})
    fig, ax = plt.subplots(figsize=(opts.width_in, opts.height_in))
    for i, y in enumerate(ys):
        lab = labels[i] if i < len(labels) else f"series {i}"
        if errors is not None and i < len(errors) and errors[i] is not None:
            ax.errorbar(x, y, yerr=errors[i], label=lab, lw=opts.line_width, fmt="o-" if symbols else "-", ms=3, capsize=2)
        elif step:
            ax.step(x, y, where="post", label=lab, lw=opts.line_width)
        else:
            ax.plot(x, y, "o-" if symbols else "-", label=lab, lw=opts.line_width, ms=3)
    ax.set_xlabel(_axis_label(opts.x_label, x_unit))
    ax.set_ylabel(_axis_label(opts.y_label, y_unit))
    if opts.title:
        ax.set_title(opts.title)
    if opts.log_x:
        ax.set_xscale("log")
    if opts.log_y:
        ax.set_yscale("log")
    if opts.x_limits:
        ax.set_xlim(*opts.x_limits)
    if opts.y_limits:
        ax.set_ylim(*opts.y_limits)
    ax.grid(opts.grid, alpha=0.3)
    if opts.legend and len(ys) > 0:
        ax.legend(fontsize=opts.font_size * 0.9)
    fig.tight_layout()
    p = Path(path)
    fig.savefig(p, dpi=opts.dpi, metadata={"Title": opts.title} if p.suffix in (".png", ".pdf", ".svg") else None)
    plt.close(fig)
    return p


def export_heatmap(path: str | Path, matrix: np.ndarray, x_axis: Optional[np.ndarray], y_axis: Optional[np.ndarray], opts: PlotExportOptions, x_unit: str = "", y_unit: str = "", value_label: str = "", x_ticklabels: Optional[list[str]] = None, y_ticklabels: Optional[list[str]] = None, annotate: bool = False) -> Path:
    plt.rcParams.update({"font.size": opts.font_size})
    fig, ax = plt.subplots(figsize=(opts.width_in, opts.height_in))
    m = np.asarray(matrix, dtype=float)
    norm = None
    if opts.log_y:
        from matplotlib.colors import LogNorm
        pos = m[m > 0]
        if pos.size:
            norm = LogNorm(vmin=pos.min(), vmax=max(pos.max(), pos.min() * 1.0001))
    if x_axis is not None and y_axis is not None and len(x_axis) == m.shape[1] and len(y_axis) == m.shape[0] and x_ticklabels is None:
        xe = _edges(np.asarray(x_axis, dtype=float))
        ye = _edges(np.asarray(y_axis, dtype=float))
        im = ax.pcolormesh(xe, ye, m, cmap=opts.colormap, norm=norm, shading="flat")
    else:
        im = ax.imshow(m, origin="lower", aspect="auto", cmap=opts.colormap, norm=norm, interpolation="nearest")
        if x_ticklabels:
            ax.set_xticks(range(len(x_ticklabels)))
            ax.set_xticklabels(x_ticklabels, rotation=45, ha="right")
        if y_ticklabels:
            ax.set_yticks(range(len(y_ticklabels)))
            ax.set_yticklabels(y_ticklabels)
        if annotate and m.size <= 400:
            for (i, j), v in np.ndenumerate(m):
                ax.text(j, i, f"{v:.3g}", ha="center", va="center", fontsize=opts.font_size * 0.7, color="w" if np.isfinite(v) and v > np.nanmax(m) / 2 else "k")
    cb = fig.colorbar(im, ax=ax)
    if value_label:
        cb.set_label(value_label)
    ax.set_xlabel(_axis_label(opts.x_label, x_unit))
    ax.set_ylabel(_axis_label(opts.y_label, y_unit))
    if opts.title:
        ax.set_title(opts.title)
    fig.tight_layout()
    p = Path(path)
    fig.savefig(p, dpi=opts.dpi)
    plt.close(fig)
    return p


def export_bars(path: str | Path, values: np.ndarray, labels: list[str], opts: PlotExportOptions, y_unit: str = "", errors: Optional[np.ndarray] = None) -> Path:
    plt.rcParams.update({"font.size": opts.font_size})
    fig, ax = plt.subplots(figsize=(opts.width_in, opts.height_in))
    x = np.arange(len(values))
    ax.bar(x, values, yerr=errors, capsize=2)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel(_axis_label(opts.y_label, y_unit))
    if opts.log_y:
        ax.set_yscale("log")
    if opts.title:
        ax.set_title(opts.title)
    ax.grid(opts.grid, axis="y", alpha=0.3)
    fig.tight_layout()
    p = Path(path)
    fig.savefig(p, dpi=opts.dpi)
    plt.close(fig)
    return p


def _edges(centers: np.ndarray) -> np.ndarray:
    c = np.asarray(centers, dtype=float)
    if c.size == 1:
        return np.array([c[0] - 0.5, c[0] + 0.5])
    mids = (c[1:] + c[:-1]) / 2
    return np.concatenate([[c[0] - (mids[0] - c[0])], mids, [c[-1] + (c[-1] - mids[-1])]])
