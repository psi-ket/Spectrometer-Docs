"""Analysis plugin system with the built-in plugins.

Every plugin consumes a :class:`MeasurementResult` (live or loaded) plus explicit
parameters and returns an :class:`AnalysisOutput` with arrays, scalars, plot
specs and provenance. Raw inputs are never modified.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Optional

import numpy as np

from swabian_backend import get_api

from ..measurements.base import FieldSpec, PlotSpec
from ..models.experiment import utc_now_iso, new_id
from ..models.results import MeasurementResult, Provenance
from . import statistics as st
from .fits import fit_model, MODELS
from .peaks import detect_peaks, fwhm_discrete


@dataclass
class AnalysisOutput:
    plugin: str
    arrays: dict[str, np.ndarray] = field(default_factory=dict)
    scalars: dict[str, Any] = field(default_factory=dict)
    plots: list[PlotSpec] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    units: dict[str, str] = field(default_factory=dict)

    def to_result(self, source: MeasurementResult) -> MeasurementResult:
        res = MeasurementResult(measurement_id=new_id("ANA_"), measurement_type=f"analysis:{self.plugin}", timestamp_start=source.timestamp_start, timestamp_end=utc_now_iso(), configuration={"plugin": self.plugin, "parameters": self.provenance.get("parameters", {}), "source_configuration": source.configuration}, arrays=dict(self.arrays), scalars=dict(self.scalars), metadata={"warnings": list(self.warnings), "source_measurement_type": source.measurement_type}, raw_files=list(source.raw_files), provenance=dict(self.provenance), status="COMPLETED", units=dict(self.units))
        return res

    def save(self, directory: str | Path, source: MeasurementResult) -> Path:
        res = self.to_result(source)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        j, _ = res.save(directory, f"analysis_{self.plugin}_{stamp}")
        return j


class AnalysisPlugin:
    name: ClassVar[str] = "base"
    display_name: ClassVar[str] = "Base"
    input_types: ClassVar[tuple[str, ...]] = ()
    parameters: ClassVar[list[FieldSpec]] = []
    version: ClassVar[str] = "1"
    description: ClassVar[str] = ""

    @classmethod
    def default_parameters(cls) -> dict[str, Any]:
        out = {}
        for p in cls.parameters:
            if p.kind == "bool":
                out[p.name] = False
            elif p.kind == "choice":
                out[p.name] = p.choices[0] if p.choices else None
            elif p.kind in ("int", "float"):
                out[p.name] = p.minimum if p.minimum is not None else 0
            else:
                out[p.name] = ""
        out.update(getattr(cls, "DEFAULTS", {}))
        return out

    def validate(self, result: MeasurementResult, params: dict[str, Any]) -> list[str]:
        problems = []
        if self.input_types and result.measurement_type not in self.input_types and not any(result.measurement_type.endswith(t) for t in self.input_types):
            problems.append(f"{self.display_name} expects {self.input_types}, got {result.measurement_type!r}")
        return problems

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:  # pragma: no cover - abstract
        raise NotImplementedError

    def provenance(self, result: MeasurementResult, params: dict[str, Any]) -> dict[str, Any]:
        api = get_api()
        return Provenance(source_raw_files=list(result.raw_files), source_measurement_id=result.measurement_id, analysis_type=self.name, analysis_version=self.version, parameters=dict(params), swabian_library_version=api.version).to_dict()


_PLUGINS: dict[str, type[AnalysisPlugin]] = {}


def register_plugin(cls: type[AnalysisPlugin]) -> type[AnalysisPlugin]:
    _PLUGINS[cls.name] = cls
    return cls


def list_plugins(measurement_type: Optional[str] = None) -> list[type[AnalysisPlugin]]:
    out = list(_PLUGINS.values())
    if measurement_type:
        out = [p for p in out if not p.input_types or measurement_type in p.input_types]
    return out


def get_plugin(name: str) -> AnalysisPlugin:
    return _PLUGINS[name]()


def _get(result: MeasurementResult, key: str) -> np.ndarray:
    if key not in result.arrays:
        raise KeyError(f"result has no array {key!r}; available: {sorted(result.arrays)}")
    return np.asarray(result.arrays[key])


# =========================================================================== G2
@register_plugin
class G2Analysis(AnalysisPlugin):
    name = "g2_analysis"
    display_name = "G2 Analysis"
    input_types = ("g2", "histogram")
    description = "Normalised g2 with Poisson error bars, peak, g2(0), optional model fit (explicit model choice)."
    parameters = [
        FieldSpec("fit_model", "Fit model", "choice", choices=["none"] + sorted(MODELS)),
        FieldSpec("fit_window_ps", "Fit window (full width around peak, 0 = all)", "float", unit="ps", minimum=0),
        FieldSpec("background_mode", "Background", "choice", choices=["stored", "none", "sideband_mean"]),
        FieldSpec("sideband_exclusion_ps", "Sideband exclusion window", "float", unit="ps", minimum=0),
        FieldSpec("zero_window_ps", "g2(0) averaging window", "float", unit="ps", minimum=0),
    ]
    DEFAULTS = {"fit_model": "none", "fit_window_ps": 0.0, "background_mode": "stored", "sideband_exclusion_ps": 2000.0, "zero_window_ps": 0.0}

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:
        out = AnalysisOutput(self.name, provenance=self.provenance(result, params))
        index = _get(result, "index_ps").astype(float)
        counts = _get(result, "counts").astype(float)
        sc = result.scalars
        n1, n2 = int(sc.get("N1", 0)), int(sc.get("N2", 0))
        bw = float(sc.get("binwidth_ps", result.configuration.get("parameters", {}).get("binwidth_ps", 1)))
        T = float(result.metadata.get("capture_duration_ps", sc.get("capture_duration_s", 0) * 1e12))
        mode = params.get("background_mode", "stored")
        if mode == "stored" and "background_per_bin" in result.arrays:
            bg = _get(result, "background_per_bin").astype(float)
        elif mode == "sideband_mean":
            excl = st.window_mask(index, 0, float(params.get("sideband_exclusion_ps", 0)))
            side = counts[~excl]
            bg = np.full(counts.shape, float(side.mean()) if side.size else 0.0)
        else:
            bg = np.zeros(counts.shape)
        corrected = counts - bg
        sigma = st.poisson_sigma(counts)
        if n1 and n2 and T > 0:
            g2 = st.g2_normalization(corrected, bw, T, n1, n2)
            g2_sigma = st.g2_normalization(sigma, bw, T, n1, n2)
            eq = "g2 = (counts - background) * T / (binwidth * N1 * N2); sigma from Poisson sqrt(counts)"
        else:
            g2 = corrected / max(1.0, float(np.median(corrected[corrected > 0])) if (corrected > 0).any() else 1.0)
            g2_sigma = sigma / max(1.0, float(np.median(corrected[corrected > 0])) if (corrected > 0).any() else 1.0)
            eq = "N1/N2 unavailable: normalised to median positive value (NOT a true g2)"
            out.warnings.append(eq)
        zero_w = float(params.get("zero_window_ps", 0))
        zmask = st.window_mask(index, 0, zero_w) if zero_w > 0 else (np.abs(index) == np.min(np.abs(index)))
        g2_zero = float(np.nanmean(g2[zmask])) if zmask.any() else float("nan")
        g2_zero_sigma = float(np.sqrt(np.nansum(g2_sigma[zmask] ** 2)) / max(1, zmask.sum())) if zmask.any() else float("nan")
        peaks = detect_peaks(index, corrected)
        out.arrays = {"index_ps": index, "counts": counts, "background_per_bin": bg, "counts_corrected": corrected, "g2": g2, "g2_sigma": g2_sigma}
        out.units = {"index_ps": "ps", "counts": "counts", "g2": "1"}
        out.scalars = {"g2_zero": g2_zero, "g2_zero_sigma": g2_zero_sigma, "uncertainty_model": "poisson", "normalization_equation": eq, "N1": n1, "N2": n2, "capture_duration_ps": T, "peaks": peaks, "fwhm_main_peak_ps": fwhm_discrete(index, corrected), "background_mode": mode}
        model = params.get("fit_model", "none")
        if model and model != "none":
            win = None
            fw = float(params.get("fit_window_ps", 0))
            if fw > 0:
                center = peaks[0]["position"] if peaks else 0.0
                win = (center - fw / 2, center + fw / 2)
            fit = fit_model(model, index, g2, g2_sigma, win)
            out.scalars["fit"] = fit.to_dict()
            out.scalars["uncertainty_model_fit"] = "fit_covariance"
            if fit.success:
                out.arrays["fit_curve"] = fit.evaluate(index)
            else:
                out.warnings.append(f"fit failed: {fit.message}")
        out.plots = [PlotSpec("histogram", "g²(τ) analysis", x_key="index_ps", y_keys=("g2", "fit_curve"), x_label="Delay τ", x_unit="ps", y_label="g²(τ)", extra={"error_key": "g2_sigma"})]
        return out


# =========================================================================== coincidences
@register_plugin
class CoincidenceAnalysis(AnalysisPlugin):
    name = "coincidence_analysis"
    display_name = "Coincidence Analysis"
    input_types = ("coincidences", "coincidence_matrix")
    description = "Rates, accidental estimates and coincidence-to-accidental ratio (explicit window and singles)."
    parameters = [
        FieldSpec("window_ps", "Coincidence window used for accidentals (0 = from result)", "float", unit="ps", minimum=0),
        FieldSpec("symmetric", "Symmetrise matrix", "bool"),
    ]
    DEFAULTS = {"window_ps": 0.0, "symmetric": True}

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:
        out = AnalysisOutput(self.name, provenance=self.provenance(result, params))
        T = float(result.metadata.get("capture_duration_ps", 0))
        dur_s = T / 1e12
        win = float(params.get("window_ps") or result.scalars.get("window_ps", 0))
        if result.measurement_type == "coincidence_matrix":
            counts = _get(result, "matrix_counts").astype(float)
            singles = _get(result, "singles_total").astype(float)
            if params.get("symmetric", True):
                counts = (counts + counts.T) / 2.0
            acc = np.outer(singles, singles) * win / T if T > 0 else np.full(counts.shape, np.nan)
            np.fill_diagonal(acc, np.nan)
            car = counts / acc
            out.arrays = {"matrix_counts": counts, "matrix_rate_cps": counts / dur_s if dur_s > 0 else counts * np.nan, "accidentals": acc, "car": car, "singles_total": singles, "wavelengths_nm": _get(result, "wavelengths_nm") if "wavelengths_nm" in result.arrays else np.array([])}
            out.scalars = {"window_ps": win, "capture_duration_s": dur_s, "accidental_equation": "acc_ij = N_i * N_j * window / T", "car_equation": "CAR_ij = counts_ij / acc_ij", "uncertainty_model": "poisson"}
            out.plots = [PlotSpec("matrix", "Coincidence-to-accidental ratio", y_keys=("car", "matrix_counts", "matrix_rate_cps", "accidentals"), series_label_key="channel_labels", extra={"wavelength_key": "wavelengths_nm"})]
        else:
            counts = _get(result, "counts_total").astype(float)
            out.arrays = {"counts_total": counts, "rates_cps": counts / dur_s if dur_s > 0 else counts * np.nan, "poisson_sigma": np.sqrt(counts), "groups": _get(result, "groups")}
            out.scalars = {"capture_duration_s": dur_s, "uncertainty_model": "poisson", "labels": result.metadata.get("labels", {}).get("channel_labels", [])}
            out.plots = [PlotSpec("bars", "Coincidence rates", y_keys=("rates_cps",), y_label="coincidence rate", y_unit="cps", series_label_key="channel_labels", extra={"error_key": "poisson_sigma"})]
        out.units = {"matrix_counts": "counts", "rates_cps": "cps", "car": "1"}
        return out


# =========================================================================== JSI
@register_plugin
class JSIAnalysis(AnalysisPlugin):
    name = "jsi_analysis"
    display_name = "JSI Analysis"
    input_types = ("jsi",)
    description = "Marginals, centroid, peak, discrete widths, wavelength cross-sections and an SVD-based Schmidt-number proxy (labelled as such)."
    parameters = [
        FieldSpec("source", "Matrix", "choice", choices=["jsi_corrected", "jsi_raw"]),
        FieldSpec("threshold_fraction", "Mask below fraction of max", "float", minimum=0, maximum=1),
        FieldSpec("cross_section_signal_index", "Signal index for idler cross-section (-1 = peak row)", "int", minimum=-1),
        FieldSpec("cross_section_idler_index", "Idler index for signal cross-section (-1 = peak column)", "int", minimum=-1),
    ]
    DEFAULTS = {"source": "jsi_corrected", "threshold_fraction": 0.0, "cross_section_signal_index": -1, "cross_section_idler_index": -1}

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:
        out = AnalysisOutput(self.name, provenance=self.provenance(result, params))
        key = params.get("source", "jsi_corrected")
        jsi = _get(result, key).astype(float)
        swl = _get(result, "signal_wavelengths_nm").astype(float)
        iwl = _get(result, "idler_wavelengths_nm").astype(float)
        thr = float(params.get("threshold_fraction", 0.0))
        masked = np.where(jsi >= thr * np.nanmax(jsi), jsi, 0.0) if jsi.size and np.nanmax(jsi) > 0 else jsi
        total = float(masked.sum())
        ms = masked.sum(axis=1)
        mi = masked.sum(axis=0)
        pk = np.unravel_index(int(np.argmax(masked)), masked.shape) if masked.size else (0, 0)
        cs = int(params.get("cross_section_signal_index", -1))
        ci = int(params.get("cross_section_idler_index", -1))
        cs = pk[0] if cs < 0 else min(cs, masked.shape[0] - 1)
        ci = pk[1] if ci < 0 else min(ci, masked.shape[1] - 1)
        scalars: dict[str, Any] = {"total": total, "peak_signal_nm": float(swl[pk[0]]) if swl.size else float("nan"), "peak_idler_nm": float(iwl[pk[1]]) if iwl.size else float("nan"), "source_matrix": key, "threshold_fraction": thr}
        if total > 0 and swl.size and iwl.size and np.isfinite(swl).all() and np.isfinite(iwl).all():
            scalars["centroid_signal_nm"] = float((ms * swl).sum() / total)
            scalars["centroid_idler_nm"] = float((mi * iwl).sum() / total)
            scalars["width_signal_nm_fwhm_discrete"] = fwhm_discrete(swl, ms)
            scalars["width_idler_nm_fwhm_discrete"] = fwhm_discrete(iwl, mi)
            try:
                s = np.linalg.svd(np.sqrt(np.maximum(masked, 0.0)), compute_uv=False)
                lam = s**2 / np.sum(s**2)
                scalars["schmidt_number_proxy"] = float(1.0 / np.sum(lam**2))
                scalars["schmidt_number_note"] = "SVD of sqrt(JSI) on the sampled detector grid; ignores spectral phase and grid resolution — a proxy, not the joint-spectral-amplitude Schmidt number"
            except Exception:
                pass
        out.arrays = {"jsi": masked, "signal_wavelengths_nm": swl, "idler_wavelengths_nm": iwl, "marginal_signal": ms, "marginal_idler": mi, "cross_section_idler": masked[cs, :], "cross_section_signal": masked[:, ci]}
        out.scalars = scalars
        out.units = {"jsi": result.units.get(key, "counts"), "signal_wavelengths_nm": "nm", "idler_wavelengths_nm": "nm"}
        out.plots = [
            PlotSpec("jsi", "JSI (analysis)", x_key="idler_wavelengths_nm", y_keys=("jsi",), x_label="Idler wavelength", x_unit="nm", y_label="Signal wavelength", y_unit="nm", extra={"y_axis_key": "signal_wavelengths_nm"}),
            PlotSpec("lines", "Marginal (signal)", x_key="signal_wavelengths_nm", y_keys=("marginal_signal",), x_label="Signal wavelength", x_unit="nm", y_label="Integrated", extra={"symbols": True}),
            PlotSpec("lines", "Marginal (idler)", x_key="idler_wavelengths_nm", y_keys=("marginal_idler",), x_label="Idler wavelength", x_unit="nm", y_label="Integrated", extra={"symbols": True}),
        ]
        return out


# =========================================================================== histogram
@register_plugin
class HistogramAnalysis(AnalysisPlugin):
    name = "histogram_analysis"
    display_name = "Histogram Analysis"
    input_types = ("histogram", "g2", "time_differences")
    description = "Peak detection, FWHM, background level and integrated counts in a window."
    parameters = [
        FieldSpec("window_center_ps", "Integration window center", "float", unit="ps"),
        FieldSpec("window_ps", "Integration window (full width, 0 = around main peak FWHM*3)", "float", unit="ps", minimum=0),
        FieldSpec("background_mode", "Background", "choice", choices=["none", "sideband_mean", "constant"]),
        FieldSpec("background_constant", "Constant background per bin", "float", minimum=0),
        FieldSpec("fit_model", "Fit model", "choice", choices=["none"] + sorted(MODELS)),
    ]
    DEFAULTS = {"window_center_ps": 0.0, "window_ps": 0.0, "background_mode": "none", "background_constant": 0.0, "fit_model": "none"}

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:
        out = AnalysisOutput(self.name, provenance=self.provenance(result, params))
        index = _get(result, "index_ps").astype(float)
        if "counts" in result.arrays:
            counts = _get(result, "counts").astype(float)
        else:
            counts = _get(result, "histograms").astype(float).sum(axis=0)
        peaks = detect_peaks(index, counts)
        main = peaks[0] if peaks else {"position": float(index[int(np.argmax(counts))]) if counts.size else 0.0, "fwhm": float("nan")}
        mode = params.get("background_mode", "none")
        win = float(params.get("window_ps", 0))
        center = float(params.get("window_center_ps", 0)) if win > 0 else main["position"]
        if win <= 0:
            win = 3.0 * (main["fwhm"] if np.isfinite(main.get("fwhm", np.nan)) else 10 * float(np.median(np.diff(index))) if index.size > 1 else 1.0)
        mask = st.window_mask(index, center, win)
        if mode == "sideband_mean":
            bg = float(counts[~mask].mean()) if (~mask).any() else 0.0
        elif mode == "constant":
            bg = float(params.get("background_constant", 0))
        else:
            bg = 0.0
        corrected = counts - bg
        integrated = float(corrected[mask].sum())
        out.arrays = {"index_ps": index, "counts": counts, "counts_corrected": corrected, "window_mask": mask.astype(np.int8)}
        out.scalars = {"peaks": peaks, "main_peak_position_ps": main["position"], "fwhm_ps": fwhm_discrete(index, counts, bg), "background_per_bin": bg, "background_mode": mode, "window_center_ps": center, "window_ps": win, "integrated_counts_in_window": integrated, "integrated_sigma_poisson": float(np.sqrt(max(0.0, counts[mask].sum()))), "uncertainty_model": "poisson"}
        model = params.get("fit_model", "none")
        if model and model != "none":
            fit = fit_model(model, index, corrected, st.poisson_sigma(counts), (center - win, center + win))
            out.scalars["fit"] = fit.to_dict()
            if fit.success:
                out.arrays["fit_curve"] = fit.evaluate(index)
        out.units = {"index_ps": "ps", "counts": "counts"}
        out.plots = [PlotSpec("histogram", "Histogram analysis", x_key="index_ps", y_keys=("counts_corrected", "fit_curve"), x_label="Delay", x_unit="ps", y_label="Counts")]
        return out


# =========================================================================== count statistics
@register_plugin
class CountStatistics(AnalysisPlugin):
    name = "count_statistics"
    display_name = "Count Statistics"
    input_types = ("countrate", "counter", "gated_counter")
    description = "Mean, std, standard error, Poisson expectation and Fano factor per channel."
    parameters = [
        FieldSpec("uncertainty_model", "Uncertainty model", "choice", choices=["sample_std", "standard_error", "poisson"]),
        FieldSpec("confidence", "Confidence level", "float", minimum=0.5, maximum=0.999),
    ]
    DEFAULTS = {"uncertainty_model": "sample_std", "confidence": 0.6827}

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:
        out = AnalysisOutput(self.name, provenance=self.provenance(result, params))
        if result.measurement_type == "counter":
            data = _get(result, "counts").astype(float)
            bw_s = float(result.scalars.get("binwidth_ms", 1.0)) / 1e3
        elif result.measurement_type == "countrate":
            data = _get(result, "trace_cps").astype(float) * float(result.scalars.get("statistics_binwidth_ms", 100.0)) / 1e3
            bw_s = float(result.scalars.get("statistics_binwidth_ms", 100.0)) / 1e3
        else:
            data = _get(result, "values").astype(float)
            widths = _get(result, "bin_widths_ps").astype(float)
            bw_s = float(np.nanmean(widths[widths > 0]) / 1e12) if (widths > 0).any() else float("nan")
        labels = result.metadata.get("labels", {}).get("channel_labels", [])
        model = params.get("uncertainty_model", "sample_std")
        conf = float(params.get("confidence", 0.6827))
        per = {}
        rows = data if data.ndim == 2 else data[None, :]
        for i, row in enumerate(rows):
            summ = st.summarize(row, model, conf)
            fano = float(summ.std**2 / summ.mean) if summ.n > 1 and summ.mean > 0 else float("nan")
            per[labels[i] if i < len(labels) else str(i)] = {**summ.to_dict(), "fano_factor": fano, "mean_rate_cps": summ.mean / bw_s if bw_s and np.isfinite(bw_s) else float("nan")}
        out.scalars = {"per_channel": per, "bin_width_s": bw_s, "uncertainty_model": model, "confidence": conf, "note": "Fano factor = variance/mean of counts per bin; 1 for Poisson"}
        out.arrays = {"counts_per_bin": rows}
        return out


# =========================================================================== background subtraction
@register_plugin
class BackgroundSubtraction(AnalysisPlugin):
    name = "background_subtraction"
    display_name = "Background Subtraction"
    input_types = ("histogram", "g2", "jsi", "coincidence_matrix")
    description = "Explicit background subtraction on a stored array (sideband mean or constant); stores the subtracted values."
    parameters = [
        FieldSpec("array_key", "Array", "str"),
        FieldSpec("mode", "Mode", "choice", choices=["sideband_mean", "constant"]),
        FieldSpec("constant", "Constant", "float"),
        FieldSpec("exclusion_window_ps", "Sideband exclusion window (1-D histograms)", "float", unit="ps", minimum=0),
    ]
    DEFAULTS = {"array_key": "counts", "mode": "sideband_mean", "constant": 0.0, "exclusion_window_ps": 2000.0}

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:
        out = AnalysisOutput(self.name, provenance=self.provenance(result, params))
        key = params.get("array_key") or "counts"
        data = _get(result, key).astype(float)
        if params.get("mode") == "constant":
            bg = float(params.get("constant", 0))
        elif data.ndim == 1 and "index_ps" in result.arrays:
            mask = st.window_mask(_get(result, "index_ps"), 0, float(params.get("exclusion_window_ps", 0)))
            bg = float(data[~mask].mean()) if (~mask).any() else 0.0
        else:
            bg = float(np.percentile(data, 10))
        out.arrays = {key: data, key + "_background_subtracted": data - bg}
        out.scalars = {"background": bg, "mode": params.get("mode"), "array_key": key}
        return out


# =========================================================================== peak detection
@register_plugin
class PeakDetection(AnalysisPlugin):
    name = "peak_detection"
    display_name = "Peak Detection"
    input_types = ("histogram", "g2", "time_differences")
    description = "scipy.signal.find_peaks with prominence threshold; reports position, height, FWHM."
    parameters = [
        FieldSpec("min_prominence", "Minimum prominence (0 = automatic)", "float", minimum=0),
        FieldSpec("min_distance_bins", "Minimum distance (bins)", "int", minimum=1),
        FieldSpec("max_peaks", "Maximum peaks", "int", minimum=1),
    ]
    DEFAULTS = {"min_prominence": 0.0, "min_distance_bins": 3, "max_peaks": 10}

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:
        out = AnalysisOutput(self.name, provenance=self.provenance(result, params))
        index = _get(result, "index_ps").astype(float)
        counts = _get(result, "counts").astype(float) if "counts" in result.arrays else _get(result, "histograms").astype(float).sum(axis=0)
        prom = float(params.get("min_prominence", 0)) or None
        peaks = detect_peaks(index, counts, prom, int(params.get("min_distance_bins", 1)), int(params.get("max_peaks", 10)))
        out.scalars = {"peaks": peaks, "n_peaks": len(peaks)}
        out.arrays = {"index_ps": index, "counts": counts, "peak_positions_ps": np.array([p["position"] for p in peaks]), "peak_heights": np.array([p["height"] for p in peaks])}
        return out


# =========================================================================== fit models
@register_plugin
class FitModelPlugin(AnalysisPlugin):
    name = "fit_models"
    display_name = "Fit Models"
    input_types = ("histogram", "g2")
    description = "Fit a chosen model to a 1-D histogram with Poisson weights; parameters, uncertainties and covariance are stored."
    parameters = [
        FieldSpec("model", "Model", "choice", choices=sorted(MODELS)),
        FieldSpec("array_key", "Y array", "str"),
        FieldSpec("window_center_ps", "Window center", "float", unit="ps"),
        FieldSpec("window_ps", "Window (0 = all)", "float", unit="ps", minimum=0),
    ]
    DEFAULTS = {"model": "gaussian", "array_key": "counts", "window_center_ps": 0.0, "window_ps": 0.0}

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:
        out = AnalysisOutput(self.name, provenance=self.provenance(result, params))
        index = _get(result, "index_ps").astype(float)
        y = _get(result, params.get("array_key") or "counts").astype(float)
        win = None
        if float(params.get("window_ps", 0)) > 0:
            c = float(params.get("window_center_ps", 0))
            w = float(params["window_ps"])
            win = (c - w / 2, c + w / 2)
        sigma = st.poisson_sigma(y) if (params.get("array_key") or "counts") == "counts" else None
        fit = fit_model(params.get("model", "gaussian"), index, y, sigma, win)
        out.scalars = {"fit": fit.to_dict(), "uncertainty_model": "fit_covariance"}
        out.arrays = {"index_ps": index, "y": y}
        if fit.success:
            out.arrays["fit_curve"] = fit.evaluate(index)
        else:
            out.warnings.append(fit.message)
        out.plots = [PlotSpec("histogram", "Fit", x_key="index_ps", y_keys=("y", "fit_curve"), x_label="Delay", x_unit="ps")]
        return out


# =========================================================================== delay calibration analysis
@register_plugin
class DelayCalibrationAnalysis(AnalysisPlugin):
    name = "delay_calibration"
    display_name = "Delay Calibration"
    input_types = ("g2", "histogram", "coincidence_matrix")
    description = "Delay from the count-weighted centre of the correlation peak (manual 3.1.3 recipe) within a window around the maximum."
    parameters = [FieldSpec("window_ps", "Window around maximum (full width, 0 = whole histogram)", "float", unit="ps", minimum=0)]
    DEFAULTS = {"window_ps": 2000.0}

    def run(self, result: MeasurementResult, params: dict[str, Any]) -> AnalysisOutput:
        out = AnalysisOutput(self.name, provenance=self.provenance(result, params))
        index = _get(result, "index_ps").astype(float)
        win = float(params.get("window_ps", 0))
        if result.measurement_type == "coincidence_matrix":
            hist = _get(result, "histograms").astype(float)
            chans = _get(result, "channels")
            delays = np.full((len(chans), len(chans)), np.nan)
            for i in range(len(chans)):
                for j in range(len(chans)):
                    if i == j:
                        continue
                    h = hist[i, j]
                    if h.sum() <= 0:
                        continue
                    c = index[int(np.argmax(h))]
                    mask = st.window_mask(index, c, win) if win > 0 else None
                    delays[i, j] = st.weighted_center(index, h, mask)
            out.arrays = {"delay_matrix_ps": delays, "channels": chans}
            out.scalars = {"note": "delay[i,j] = weighted centre of histogram (start i, stop j)"}
            out.plots = [PlotSpec("matrix", "Pairwise delays (ps)", y_keys=("delay_matrix_ps",), series_label_key="channel_labels")]
        else:
            counts = _get(result, "counts").astype(float)
            c = index[int(np.argmax(counts))] if counts.size else 0.0
            mask = st.window_mask(index, c, win) if win > 0 else None
            d = st.weighted_center(index, counts, mask)
            out.scalars = {"delay_ps": d, "peak_bin_ps": float(c), "window_ps": win, "equation": "sum(t*c)/sum(c) inside window"}
            out.arrays = {"index_ps": index, "counts": counts}
        return out
