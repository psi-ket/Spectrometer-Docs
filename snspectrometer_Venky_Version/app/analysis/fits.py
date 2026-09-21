"""Fit models with explicit parameters and covariance (scipy.optimize.curve_fit)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

import numpy as np
from scipy.optimize import curve_fit


def gaussian_peak(x, amplitude, center, sigma, offset):
    return offset + amplitude * np.exp(-0.5 * ((x - center) / sigma) ** 2)


def lorentzian_peak(x, amplitude, center, gamma, offset):
    return offset + amplitude * gamma**2 / ((x - center) ** 2 + gamma**2)


def exponential_dip(x, depth, center, tau, level):
    """g2 antibunching-like model: level * (1 - depth * exp(-|x-center|/tau))."""
    return level * (1.0 - depth * np.exp(-np.abs(x - center) / tau))


def exponential_peak(x, amplitude, center, tau, offset):
    return offset + amplitude * np.exp(-np.abs(x - center) / tau)


MODELS: dict[str, tuple[Callable, list[str]]] = {
    "gaussian": (gaussian_peak, ["amplitude", "center", "sigma", "offset"]),
    "lorentzian": (lorentzian_peak, ["amplitude", "center", "gamma", "offset"]),
    "exponential_peak": (exponential_peak, ["amplitude", "center", "tau", "offset"]),
    "exponential_dip": (exponential_dip, ["depth", "center", "tau", "level"]),
}


@dataclass
class FitResult:
    model: str
    parameter_names: list[str]
    parameters: list[float]
    uncertainties: list[float]
    covariance: list[list[float]]
    chi2: float
    reduced_chi2: float
    n_points: int
    window: Optional[tuple[float, float]]
    success: bool
    message: str = ""
    derived: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def param(self, name: str) -> float:
        return float(self.parameters[self.parameter_names.index(name)])

    def evaluate(self, x: np.ndarray) -> np.ndarray:
        fn = MODELS[self.model][0]
        return fn(np.asarray(x, dtype=float), *self.parameters)


def _initial_guess(model: str, x: np.ndarray, y: np.ndarray) -> list[float]:
    off = float(np.median(y))
    if model == "exponential_dip":
        level = float(np.percentile(y, 90)) or 1.0
        i = int(np.argmin(y))
        return [max(0.0, 1.0 - float(y[i]) / level), float(x[i]), max(1.0, (x.max() - x.min()) / 10.0), level]
    i = int(np.argmax(y))
    amp = float(y[i] - off)
    width = max(1.0, (x.max() - x.min()) / 20.0)
    return [amp, float(x[i]), width, off]


def fit_model(model: str, x: np.ndarray, y: np.ndarray, sigma: Optional[np.ndarray] = None, window: Optional[tuple[float, float]] = None, p0: Optional[list[float]] = None) -> FitResult:
    if model not in MODELS:
        raise ValueError(f"Unknown fit model {model!r}; available: {sorted(MODELS)}")
    fn, names = MODELS[model]
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if window is not None:
        mask &= (x >= window[0]) & (x <= window[1])
    xs, ys = x[mask], y[mask]
    ss = None
    if sigma is not None:
        ss = np.asarray(sigma, dtype=float)[mask]
        ss = np.where(ss > 0, ss, np.nan)
        if np.isnan(ss).any():
            ss = np.where(np.isnan(ss), np.nanmin(ss[np.isfinite(ss)]) if np.isfinite(ss).any() else 1.0, ss)
    if xs.size < len(names) + 1:
        return FitResult(model, names, [float("nan")] * len(names), [float("nan")] * len(names), [], float("nan"), float("nan"), int(xs.size), window, False, "not enough points")
    guess = p0 or _initial_guess(model, xs, ys)
    try:
        popt, pcov = curve_fit(fn, xs, ys, p0=guess, sigma=ss, absolute_sigma=ss is not None, maxfev=20000)
    except Exception as exc:
        return FitResult(model, names, [float("nan")] * len(names), [float("nan")] * len(names), [], float("nan"), float("nan"), int(xs.size), window, False, str(exc))
    resid = ys - fn(xs, *popt)
    if ss is not None:
        chi2 = float(np.sum((resid / ss) ** 2))
    else:
        chi2 = float(np.sum(resid**2))
    dof = max(1, xs.size - len(names))
    unc = [float(np.sqrt(abs(pcov[i, i]))) if np.isfinite(pcov[i, i]) else float("nan") for i in range(len(names))]
    res = FitResult(model, names, [float(p) for p in popt], unc, [[float(v) for v in row] for row in pcov], chi2, chi2 / dof, int(xs.size), window, True)
    if model == "gaussian":
        res.derived["fwhm"] = 2.0 * np.sqrt(2.0 * np.log(2.0)) * abs(res.param("sigma"))
    elif model == "lorentzian":
        res.derived["fwhm"] = 2.0 * abs(res.param("gamma"))
    elif model == "exponential_dip":
        res.derived["value_at_center"] = float(res.param("level") * (1.0 - res.param("depth")))
    return res
