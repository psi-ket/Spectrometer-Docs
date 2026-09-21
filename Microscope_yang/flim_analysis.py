"""flim_analysis.py — FLIM histogram analysis and visualization."""

import numpy as np
import matplotlib.pyplot as plt
from scipy import optimize


class FLIMAnalyzer:
    """Analyse a FLIM photon-arrival-time histogram.

    The peak of the histogram can be detected automatically or fixed at a
    known hardware delay. Exponential fits are performed on the decay *tail*
    only (peak → end) using the model
    A · exp(-(t − t_peak) / τ) + B so the hardware-delay offset is handled
    correctly.

    Parameters
    ----------
    histogram : ndarray
        Photon counts per bin
    bin_edges : ndarray
        Bin edges in picoseconds
    fixed_peak_time_ps : float, optional
        Fixed hardware delay in picoseconds. If provided, use this as the
        exponential decay start point instead of auto-detecting from the peak.
    fit_end_ps : float, optional
        Truncate the fit window at this time (ps) to exclude reflection
        artifacts. 0 or None means use the full tail.
    """

    def __init__(self, histogram, bin_edges, fixed_peak_time_ps=None, fit_end_ps=None):
        self.histogram = np.asarray(histogram, dtype=np.float64)
        self.bin_edges = np.asarray(bin_edges, dtype=np.float64)
        self.bin_centers = (self.bin_edges[:-1] + self.bin_edges[1:]) / 2

        # Use fixed delay or auto-detect peak
        if fixed_peak_time_ps is not None:
            # Fixed hardware delay (calibrated property)
            self.peak_time_ps = float(fixed_peak_time_ps)
            self.peak_idx = int(np.argmin(np.abs(self.bin_centers - self.peak_time_ps)))
        else:
            # Auto-detect peak from histogram
            if np.sum(self.histogram) > 0:
                self.peak_idx = int(np.argmax(self.histogram))
            else:
                self.peak_idx = 0
            self.peak_time_ps = float(self.bin_centers[self.peak_idx])

        # Estimate background baseline from bins well before the peak
        if self.peak_idx >= 4:
            bg_end = max(2, self.peak_idx // 2)
            self.background = float(np.mean(self.histogram[:bg_end]))
        else:
            self.background = 0.0

        # End index for fitting window (to exclude reflection artifacts)
        if fit_end_ps and fit_end_ps > self.peak_time_ps:
            self.end_idx = int(np.argmin(np.abs(self.bin_centers - fit_end_ps))) + 1
            self.end_idx = min(self.end_idx, len(self.bin_centers))
        else:
            self.end_idx = len(self.bin_centers)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------
    def get_statistics(self):
        """Return basic histogram statistics (all on the raw time axis)."""
        n = int(np.sum(self.histogram))
        if n == 0:
            return dict(total_photons=0, peak_time_ps=0.0,
                        mean_arrival_ps=0.0, median_arrival_ps=0.0)

        mean = float(np.dot(self.bin_centers, self.histogram) / n)
        cumsum = np.cumsum(self.histogram)
        med_idx = min(int(np.searchsorted(cumsum, n / 2)),
                      len(self.bin_centers) - 1)
        return dict(
            total_photons=n,
            peak_time_ps=self.peak_time_ps,
            mean_arrival_ps=mean,
            median_arrival_ps=float(self.bin_centers[med_idx]),
        )

    # ------------------------------------------------------------------
    # Exponential fit  (decay tail only: peak → end)
    # ------------------------------------------------------------------
    def fit_exponential(self):
        """Fit A · exp(-(t − t_peak) / τ) + B on data from peak to fit_end."""
        x = self.bin_centers[self.peak_idx:self.end_idx]
        y = self.histogram[self.peak_idx:self.end_idx]
        t0 = self.peak_time_ps
        B0 = self.background

        fail = dict(amplitude=0, tau_ps=0, baseline=0, r_squared=0,
                    peak_time_ps=t0, fit_success=False)
        if len(x) < 3 or np.sum(y) == 0:
            return fail

        def model(t, A, tau, B):
            return A * np.exp(-(t - t0) / tau) + B

        A0 = float(y[0] - B0) if y[0] > B0 else float(np.max(y))
        drop_idx = np.searchsorted(-y, -A0 * 0.37)      # y < 37 % of peak
        tau0 = max(float(x[drop_idx] - x[0]) if 0 < drop_idx < len(x)
                   else float(x[-1] - x[0]) / 3, 100.0)

        try:
            popt, _ = optimize.curve_fit(model, x, y, p0=[A0, tau0, B0],
                                         bounds=([0, 1, 0], [np.inf, np.inf, np.inf]),
                                         maxfev=10000)
            yp = model(x, *popt)
            ss_res = np.sum((y - yp) ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
            return dict(amplitude=float(popt[0]), tau_ps=float(popt[1]),
                        baseline=float(popt[2]), r_squared=r2,
                        peak_time_ps=t0, fit_success=True)
        except Exception as e:
            print(f"Fitting failed: {e}")
            return fail

    # ------------------------------------------------------------------
    # Bi-exponential fit  (decay tail only: peak → end)
    # ------------------------------------------------------------------
    def fit_biexponential(self):
        """Fit A1·exp(-(t−t_peak)/τ1) + A2·exp(-(t−t_peak)/τ2) + B."""
        x = self.bin_centers[self.peak_idx:self.end_idx]
        y = self.histogram[self.peak_idx:self.end_idx]
        t0 = self.peak_time_ps
        B0 = self.background

        fail = dict(A1=0, tau1_ps=0, A2=0, tau2_ps=0, baseline=0,
                    r_squared=0, fit_success=False)
        if len(x) < 5 or np.sum(y) == 0:
            return fail

        def model(t, A1, tau1, A2, tau2, B):
            dt = t - t0
            return A1 * np.exp(-dt / tau1) + A2 * np.exp(-dt / tau2) + B

        s = self.fit_exponential()
        if s['fit_success']:
            p0 = [s['amplitude']/2, s['tau_ps']*0.5,
                  s['amplitude']/2, s['tau_ps']*2.0, s['baseline']]
        else:
            mx, span = float(np.max(y)), float(x[-1] - x[0])
            p0 = [mx/2, span/4, mx/2, span, B0]

        try:
            popt, _ = optimize.curve_fit(
                model, x, y, p0=p0,
                bounds=([0, 1, 0, 1, 0],
                        [np.inf, np.inf, np.inf, np.inf, np.inf]),
                maxfev=10000)
            yp = model(x, *popt)
            ss_res = np.sum((y - yp) ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
            # Amplitude-weighted average lifetime
            A1, tau1, A2, tau2 = popt[0], popt[1], popt[2], popt[3]
            tau_avg = (A1*tau1 + A2*tau2) / (A1 + A2) if (A1+A2) > 0 else 0.0
            return dict(A1=float(A1), tau1_ps=float(tau1),
                        A2=float(A2), tau2_ps=float(tau2),
                        baseline=float(popt[4]),
                        tau_avg_ps=float(tau_avg),
                        r_squared=r2, fit_success=True)
        except Exception as e:
            print(f"Biexponential fitting failed: {e}")
            return fail


# ======================================================================
# Plotting helpers
# ======================================================================

def plot_flim_histogram(histogram, bin_edges, ax=None,
                        title="FLIM Histogram", log_scale=True):
    """Bar-plot of raw photon counts with peak marker."""
    if ax is None:
        _, ax = plt.subplots()
    bc = (bin_edges[:-1] + bin_edges[1:]) / 2
    bw = np.diff(bin_edges)

    ax.bar(bc, histogram, width=bw, alpha=0.7, edgecolor='black')
    if np.sum(histogram) > 0:
        pk = np.argmax(histogram)
        ax.axvline(bc[pk], color='red', ls='--', lw=1,
                   label=f'Peak @ {bc[pk]:.0f} ps')
        ax.legend(fontsize=9)
    if log_scale:
        ax.set_yscale('log')
    ax.set(xlabel='Photon Arrival Time (ps)', ylabel='Counts', title=title)
    ax.grid(True, alpha=0.3)
    return ax


def plot_flim_with_fit(histogram, bin_edges, fit_result, ax=None,
                       title="Lifetime Decay Curve", use_nanoseconds=False):
    """Full raw histogram with the exponential fit overlaid from the peak."""
    if ax is None:
        _, ax = plt.subplots()
    
    # Convert to display units if needed
    scale = 1000.0 if use_nanoseconds else 1.0
    bc = (bin_edges[:-1] + bin_edges[1:]) / 2
    bc = bc / scale
    bw = np.diff(bin_edges) / scale

    # ---- full raw histogram ----
    ax.bar(bc, histogram, width=bw, alpha=0.5,
           edgecolor='black', color='steelblue', label='Data')

    tp = fit_result.get('peak_time_ps', 0)

    # ---- fit overlay (peak → end) ----
    if fit_result.get('fit_success'):
        tau = fit_result['tau_ps']
        amp = fit_result['amplitude']
        B = fit_result.get('baseline', 0)
        xf = np.linspace(tp / scale, bc[-1], 500)
        ax.plot(xf, amp * np.exp(-(xf * scale - tp) / tau) + B, 'r-', lw=2,
                label=f'\u03c4 = {tau / scale:.3f} {"ns" if use_nanoseconds else "ps"}  (R\u00b2 = {fit_result["r_squared"]:.4f})')
        if B > 0:
            ax.axhline(B, color='orange', ls='--', lw=1, alpha=0.6,
                       label=f'Baseline = {B:.1f}')

    ax.set_yscale('log')
    unit = "ns" if use_nanoseconds else "ps"
    ax.set_xlabel(f'Photon Arrival Time ({unit})', color="#8b90a5", fontsize=10)
    ax.set_ylabel('Counts', color="#8b90a5", fontsize=10)
    ax.set_title(title, color="#e2e4eb", fontsize=12, fontweight="bold")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    return ax


def load_flim_data(npz_file):
    """
    Load FLIM data from save file.
    
    Parameters
    ----------
    npz_file : str
        Path to .npz file saved by scan_engine
    
    Returns
    -------
    tuple
        (histogram, bin_edges, metadata) or None if FLIM data not found
    """
    try:
        data = np.load(npz_file)
        
        if 'flim_histogram' in data and 'flim_bin_edges_ps' in data:
            histogram = data['flim_histogram']
            bin_edges = data['flim_bin_edges_ps']
            return histogram, bin_edges, data
        else:
            print("FLIM data not found in .npz file")
            return None
    except Exception as e:
        print(f"Error loading FLIM data: {e}")
        return None


# Example usage
if __name__ == "__main__":
    # Create synthetic FLIM data for demonstration
    bin_edges = np.logspace(0, 4, 257)  # 1 ps to 10 ns
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    
    # Synthetic single-exponential decay with tau=2000 ps
    tau_true = 2000  # ps
    histogram = 1000 * np.exp(-bin_centers / tau_true)
    histogram = np.random.poisson(histogram)  # Add Poisson noise
    
    # Analyze
    analyzer = FLIMAnalyzer(histogram, bin_edges)
    stats = analyzer.get_statistics()
    fit = analyzer.fit_exponential()
    
    print("Statistics:")
    print(f"  Total photons: {stats['total_photons']}")
    print(f"  Peak at:       {stats['peak_time_ps']:.1f} ps")
    print(f"  Mean arrival:  {stats['mean_arrival_ps']:.1f} ps")
    print()
    print("Single Exponential Fit:")
    print(f"  Amplitude: {fit['amplitude']:.2f}")
    print(f"  Lifetime (τ): {fit['tau_ps']:.1f} ps")
    print(f"  R²: {fit['r_squared']:.4f}")
    
    # Plot
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    plot_flim_histogram(histogram, bin_edges, ax=ax1)
    plot_flim_with_fit(histogram, bin_edges, fit, ax=ax2)
    plt.tight_layout()
    plt.show()
