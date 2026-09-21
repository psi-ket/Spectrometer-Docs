"""Pure-NumPy synthetic arrays for unit tests and plots (no library needed)."""
from __future__ import annotations

import numpy as np


def poisson_counts(rate_cps: float, binwidth_s: float, n_bins: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.poisson(rate_cps * binwidth_s, size=n_bins)


def g2_histogram(binwidth_ps: int = 100, n_bins: int = 2001, n1: int = 200_000, n2: int = 150_000, duration_s: float = 10.0, peak_delay_ps: float = 0.0, peak_width_ps: float = 200.0, g2_zero: float = 5.0, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Correlated-pair histogram: Poisson accidentals + Gaussian peak; returns (index_ps, counts)."""
    rng = np.random.default_rng(seed)
    index = (np.arange(n_bins) - n_bins // 2) * binwidth_ps
    acc = n1 * n2 * binwidth_ps / (duration_s * 1e12)
    peak = (g2_zero - 1.0) * acc * np.exp(-0.5 * ((index - peak_delay_ps) / peak_width_ps) ** 2)
    counts = rng.poisson(acc + peak)
    return index.astype(np.int64), counts.astype(np.int64)


def antibunching_histogram(binwidth_ps: int = 100, n_bins: int = 2001, level: float = 400.0, dip: float = 0.1, width_ps: float = 800.0, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    index = (np.arange(n_bins) - n_bins // 2) * binwidth_ps
    expected = level * (1.0 - (1.0 - dip) * np.exp(-np.abs(index) / width_ps))
    return index.astype(np.int64), rng.poisson(expected).astype(np.int64)


def jsi_matrix(signal_wl: np.ndarray, idler_wl: np.ndarray, center_sum_nm: float, pump_bw_nm: float = 4.0, pm_bw_nm: float = 12.0, peak_counts: float = 1000.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    s = np.asarray(signal_wl, dtype=float)[:, None]
    i = np.asarray(idler_wl, dtype=float)[None, :]
    amp = np.exp(-((s + i) / 2 - center_sum_nm / 2) ** 2 / pump_bw_nm**2) * np.exp(-((s - i) - (s.mean() - i.mean())) ** 2 / pm_bw_nm**2)
    return rng.poisson(peak_counts * amp).astype(np.int64)


def coincidence_matrix(n: int, diag_rate: float = 1e4, pair_rate: float = 300.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    m = rng.poisson(pair_rate, size=(n, n))
    m = (m + m.T) // 2
    np.fill_diagonal(m, rng.poisson(diag_rate, size=n))
    return m.astype(np.int64)
