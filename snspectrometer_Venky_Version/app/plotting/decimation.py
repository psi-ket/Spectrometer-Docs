"""Min/max decimation for display only. Stored data are never modified."""
from __future__ import annotations

import numpy as np


def decimate_minmax(x: np.ndarray, y: np.ndarray, max_points: int = 4000) -> tuple[np.ndarray, np.ndarray]:
    """Reduce (x, y) to at most ~2*max_points points keeping each bucket's min and max.

    Peaks survive the reduction, which plain subsampling would not guarantee.
    """
    x = np.asarray(x)
    y = np.asarray(y)
    n = x.size
    if n <= 2 * max_points or n == 0:
        return x, y
    buckets = max_points
    edges = np.linspace(0, n, buckets + 1).astype(int)
    xs = np.empty(2 * buckets, dtype=x.dtype)
    ys = np.empty(2 * buckets, dtype=y.dtype)
    k = 0
    for i in range(buckets):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        seg = y[a:b]
        if np.all(np.isnan(seg)):
            xs[k], ys[k] = x[a], seg[0]
            k += 1
            continue
        imin = int(np.nanargmin(seg))
        imax = int(np.nanargmax(seg))
        first, second = (imin, imax) if imin <= imax else (imax, imin)
        xs[k], ys[k] = x[a + first], seg[first]
        k += 1
        if second != first:
            xs[k], ys[k] = x[a + second], seg[second]
            k += 1
    return xs[:k], ys[:k]


def decimate_matrix(m: np.ndarray, max_side: int = 1024) -> np.ndarray:
    """Block-sum a large 2-D array so that neither side exceeds ``max_side`` (display only)."""
    m = np.asarray(m)
    if m.ndim != 2:
        return m
    fy = int(np.ceil(m.shape[0] / max_side))
    fx = int(np.ceil(m.shape[1] / max_side))
    if fy <= 1 and fx <= 1:
        return m
    h = (m.shape[0] // fy) * fy
    w = (m.shape[1] // fx) * fx
    trimmed = m[:h, :w]
    return trimmed.reshape(h // fy, fy, w // fx, fx).sum(axis=(1, 3))
