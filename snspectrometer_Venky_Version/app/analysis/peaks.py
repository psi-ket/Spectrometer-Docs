"""Peak detection and simple width estimation on histograms."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
from scipy.signal import find_peaks, peak_widths


def detect_peaks(index: np.ndarray, counts: np.ndarray, min_prominence: Optional[float] = None, min_distance_bins: int = 1, max_peaks: int = 10) -> list[dict[str, Any]]:
    x = np.asarray(index, dtype=float)
    y = np.asarray(counts, dtype=float)
    if y.size == 0:
        return []
    prom = min_prominence if min_prominence is not None else max(1.0, 5.0 * np.sqrt(max(1.0, float(np.median(y)))))
    idx, props = find_peaks(y, prominence=prom, distance=max(1, int(min_distance_bins)))
    if idx.size == 0:
        return []
    order = np.argsort(props["prominences"])[::-1][:max_peaks]
    idx = idx[order]
    widths, heights, left, right = peak_widths(y, idx, rel_height=0.5)
    dx = float(np.median(np.diff(x))) if x.size > 1 else 1.0
    out = []
    for k, i in enumerate(idx):
        out.append({"bin": int(i), "position": float(x[i]), "height": float(y[i]), "prominence": float(props["prominences"][order][k]), "fwhm": float(widths[k] * dx), "left": float(x[0] + left[k] * dx), "right": float(x[0] + right[k] * dx)})
    return out


def fwhm_discrete(index: np.ndarray, counts: np.ndarray, background: float = 0.0) -> float:
    """Full width at half maximum by linear interpolation around the maximum bin."""
    x = np.asarray(index, dtype=float)
    y = np.asarray(counts, dtype=float) - background
    if y.size < 3:
        return float("nan")
    i = int(np.argmax(y))
    half = y[i] / 2.0
    if half <= 0:
        return float("nan")
    left = i
    while left > 0 and y[left] > half:
        left -= 1
    right = i
    while right < y.size - 1 and y[right] > half:
        right += 1
    if left == i or right == i:
        return float("nan")

    def interp(a, b):
        ya, yb = y[a], y[b]
        if yb == ya:
            return x[a]
        return x[a] + (half - ya) * (x[b] - x[a]) / (yb - ya)

    return float(interp(right - 1, right) - interp(left, left + 1))
