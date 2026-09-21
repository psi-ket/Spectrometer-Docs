"""Unit tests: statistics, g2 normalisation, JSI/coincidence analysis, fits, decimation, peaks."""
from __future__ import annotations

import numpy as np

from app.analysis import statistics as st
from app.analysis.fits import fit_model
from app.analysis.peaks import detect_peaks, fwhm_discrete
from app.analysis.plugins import get_plugin, list_plugins
from app.models.results import MeasurementResult
from app.plotting.decimation import decimate_minmax, decimate_matrix
from simulator.synthetic import g2_histogram, jsi_matrix, coincidence_matrix, antibunching_histogram


def test_g2_normalization_and_accidentals():
    index, counts = g2_histogram(binwidth_ps=100, n_bins=2001, n1=2_000_000, n2=1_500_000, duration_s=10.0, g2_zero=5.0)
    T = 10e12
    g2 = st.g2_normalization(counts, 100, T, 2_000_000, 1_500_000)
    acc = st.accidental_counts_per_bin(100, T, 2_000_000, 1_500_000)
    assert abs(acc - 30.0) < 1e-9
    side = g2[np.abs(index) > 2000]
    assert 0.8 < side.mean() < 1.2
    assert 4.0 < g2[np.argmin(np.abs(index))] < 6.5
    assert np.isnan(st.g2_normalization(counts, 100, 0, 1, 1)).all()


def test_window_mask_and_weighted_center():
    idx = np.arange(-1000, 1001, 10)
    m = st.window_mask(idx, 100, 200)
    assert idx[m].min() == 0 and idx[m].max() == 200
    counts = np.exp(-0.5 * ((idx - 130) / 40) ** 2) * 100
    assert abs(st.weighted_center(idx, counts, st.window_mask(idx, 130, 400)) - 130) < 2


def test_summary_and_poisson_interval():
    s = st.summarize(np.array([10, 12, 11, 9, 13.0]), "standard_error", 0.68)
    assert s.n == 5 and abs(s.mean - 11) < 1e-9 and s.confidence_interval[0] < 11 < s.confidence_interval[1]
    lo, hi = st.poisson_interval(0)
    assert lo == 0.0 and hi > 0
    lo, hi = st.poisson_interval(100)
    assert 85 < lo < 100 < hi < 115


def test_fit_gaussian_recovers_parameters():
    x = np.linspace(-500, 500, 401)
    y = 5 + 100 * np.exp(-0.5 * ((x - 40) / 30) ** 2)
    fit = fit_model("gaussian", x, y)
    assert fit.success and abs(fit.param("center") - 40) < 1 and abs(fit.derived["fwhm"] - 2.3548 * 30) < 1
    assert len(fit.covariance) == 4
    dip = fit_model("exponential_dip", *antibunching_histogram(dip=0.1, width_ps=800))
    assert dip.success and dip.param("depth") > 0.5


def test_peaks_and_fwhm():
    idx = np.arange(-1000, 1001, 10).astype(float)
    counts = 3 + 200 * np.exp(-0.5 * (idx / 50) ** 2)
    peaks = detect_peaks(idx, counts)
    assert peaks and abs(peaks[0]["position"]) < 10
    assert abs(fwhm_discrete(idx, counts, 3) - 2.3548 * 50) < 12


def test_decimation_preserves_extrema():
    x = np.arange(100_000, dtype=float)
    y = np.zeros_like(x); y[54321] = 100; y[777] = -50
    xd, yd = decimate_minmax(x, y, 2000)
    assert yd.max() == 100 and yd.min() == -50 and xd.size <= 4000
    m = decimate_matrix(np.ones((3000, 10)), 1024)
    assert m.shape[0] <= 1024 and m.sum() == 3000 * 10 - (3000 % 3) * 10


def _g2_result():
    index, counts = g2_histogram(binwidth_ps=100, n_bins=2001, n1=2_000_000, n2=1_500_000, duration_s=10.0, g2_zero=5.0)
    res = MeasurementResult(measurement_type="g2", arrays={"index_ps": index, "counts": counts, "background_per_bin": np.zeros_like(counts, dtype=float)}, scalars={"N1": 2_000_000, "N2": 1_500_000, "binwidth_ps": 100}, metadata={"capture_duration_ps": 10e12}, configuration={"parameters": {"binwidth_ps": 100}})
    return res


def test_g2_analysis_plugin_with_fit():
    res = _g2_result()
    out = get_plugin("g2_analysis").run(res, {"fit_model": "gaussian", "fit_window_ps": 1500, "background_mode": "stored", "zero_window_ps": 0, "sideband_exclusion_ps": 2000})
    assert 4.0 < out.scalars["g2_zero"] < 6.5
    assert out.scalars["fit"]["success"] and abs(out.scalars["fit"]["parameters"][1]) < 30
    assert out.scalars["uncertainty_model"] == "poisson"
    assert "g2" in out.arrays and "fit_curve" in out.arrays
    assert out.provenance["analysis_type"] == "g2_analysis"


def test_jsi_analysis_plugin():
    swl = np.array([780.0, 785.0, 790.0, 795.0]); iwl = np.array([825.0, 830.0, 835.0, 840.0])
    jsi = jsi_matrix(swl, iwl, center_sum_nm=1620.0)
    res = MeasurementResult(measurement_type="jsi", arrays={"jsi_corrected": jsi.astype(float), "jsi_raw": jsi, "signal_wavelengths_nm": swl, "idler_wavelengths_nm": iwl}, units={"jsi_corrected": "counts"})
    out = get_plugin("jsi_analysis").run(res, get_plugin("jsi_analysis").default_parameters())
    assert out.arrays["marginal_signal"].shape == (4,) and out.arrays["marginal_idler"].shape == (4,)
    assert 780 <= out.scalars["centroid_signal_nm"] <= 795
    assert out.scalars["schmidt_number_proxy"] >= 1.0
    assert "proxy" in out.scalars["schmidt_number_note"]


def test_coincidence_matrix_analysis_car():
    m = coincidence_matrix(4, 1e4, 300.0)
    singles = np.full(4, 100_000.0)
    res = MeasurementResult(measurement_type="coincidence_matrix", arrays={"matrix_counts": m, "singles_total": singles, "wavelengths_nm": np.array([1, 2, 3, 4.0])}, scalars={"window_ps": 1000}, metadata={"capture_duration_ps": 10e12})
    out = get_plugin("coincidence_analysis").run(res, {"window_ps": 0, "symmetric": True})
    acc = 100_000.0 * 100_000.0 * 1000 / 10e12  # = 1000
    assert np.allclose(out.arrays["accidentals"][0, 1], acc)
    assert np.isnan(out.arrays["car"][0, 0])
    assert np.allclose(out.arrays["car"][0, 1], m[0, 1] / acc, rtol=0.5)


def test_plugin_registry_filters_by_type():
    names = {p.name for p in list_plugins("g2")}
    assert {"g2_analysis", "histogram_analysis", "fit_models", "peak_detection", "delay_calibration"} <= names
    assert "jsi_analysis" not in names
