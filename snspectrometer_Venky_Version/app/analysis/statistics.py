"""Statistics helpers: explicit normalisations, uncertainty models, windows."""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Optional

import numpy as np
from scipy import stats as _sps

UNCERTAINTY_MODELS = ("none", "poisson", "sample_std", "standard_error", "fit_covariance")


def window_mask(index_ps: np.ndarray, center_ps: float, width_ps: float) -> np.ndarray:
    """Boolean mask of bins with |index - center| <= width/2."""
    idx = np.asarray(index_ps, dtype=float)
    return np.abs(idx - float(center_ps)) <= float(width_ps) / 2.0


def g2_normalization(counts: np.ndarray, binwidth_ps: float, duration_ps: float, n1: int, n2: int) -> np.ndarray:
    """g2(tau) = counts(tau) * T / (binwidth * N1 * N2); NaN when undefined."""
    c = np.asarray(counts, dtype=float)
    denom = float(binwidth_ps) * float(n1) * float(n2)
    if denom <= 0 or duration_ps <= 0:
        return np.full(c.shape, np.nan)
    return c * float(duration_ps) / denom


def accidental_counts_per_bin(binwidth_ps: float, duration_ps: float, n1: int, n2: int) -> float:
    """Expected accidental coincidences per histogram bin for uncorrelated Poisson streams."""
    if duration_ps <= 0:
        return float("nan")
    return float(n1) * float(n2) * float(binwidth_ps) / float(duration_ps)


def poisson_sigma(counts: np.ndarray) -> np.ndarray:
    return np.sqrt(np.maximum(np.asarray(counts, dtype=float), 0.0))


def poisson_interval(counts: int, confidence: float = 0.6827) -> tuple[float, float]:
    """Exact (Garwood) Poisson confidence interval for an observed count."""
    k = int(counts)
    alpha = 1.0 - confidence
    lo = 0.0 if k == 0 else _sps.chi2.ppf(alpha / 2, 2 * k) / 2.0
    hi = _sps.chi2.ppf(1 - alpha / 2, 2 * k + 2) / 2.0
    return float(lo), float(hi)


@dataclass
class SummaryStatistics:
    n: int
    mean: float
    std: float
    standard_error: float
    minimum: float
    maximum: float
    uncertainty_model: str
    confidence_level: float
    confidence_interval: tuple[float, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(values: np.ndarray, uncertainty_model: str = "sample_std", confidence: float = 0.6827) -> SummaryStatistics:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    n = int(v.size)
    if n == 0:
        nan = float("nan")
        return SummaryStatistics(0, nan, nan, nan, nan, nan, uncertainty_model, confidence, (nan, nan))
    mean = float(v.mean())
    std = float(v.std(ddof=1)) if n > 1 else 0.0
    se = std / math.sqrt(n) if n > 1 else 0.0
    if uncertainty_model == "poisson":
        lo, hi = poisson_interval(int(round(v.sum())), confidence)
        ci = (lo / n, hi / n)
    elif uncertainty_model == "standard_error" and n > 1:
        z = _sps.t.ppf(0.5 + confidence / 2, n - 1)
        ci = (mean - z * se, mean + z * se)
    elif uncertainty_model == "sample_std":
        ci = (mean - std, mean + std)
    else:
        ci = (float("nan"), float("nan"))
    return SummaryStatistics(n, mean, std, se, float(v.min()), float(v.max()), uncertainty_model, confidence, ci)


def weighted_center(index_ps: np.ndarray, counts: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    """Count-weighted centre of a histogram (the manual's delay-estimation recipe)."""
    idx = np.asarray(index_ps, dtype=float)
    c = np.asarray(counts, dtype=float)
    if mask is not None:
        idx, c = idx[mask], c[mask]
    s = c.sum()
    if s <= 0:
        return float("nan")
    return float((idx * c).sum() / s)
