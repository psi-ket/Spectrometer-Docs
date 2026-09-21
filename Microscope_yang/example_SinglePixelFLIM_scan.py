"""
example_SinglePixelFLIM_scan.py — Single-pixel FLIM lifetime curve acquisition.

Workflow:
  1. Load a previous per-pixel FLIM scan and display the intensity map.
  2. Click on a pixel to select the target position.
  3. Move the laser (galvo mirrors via LabJack) to that position.
  4. Acquire a high-statistics lifetime histogram at that single pixel.
  5. Fit and display the fluorescence lifetime decay curve.
"""

import numpy as np
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from pathlib import Path
import json
import time
import gc

try:
    from labjack import ljm
except ImportError:
    print("Error: labjack-ljm library not installed.")
    exit(1)

try:
    import TimeTagger
except ImportError:
    print("Error: TimeTagger SDK not installed.")
    exit(1)

from flim_analysis import FLIMAnalyzer, plot_flim_with_fit


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

# Hardware channel settings (must match your setup)
LASER_CHANNEL = 1          # Pulsed laser trigger channel
DETECTOR_CHANNEL = 2       # Photon detector channel
LASER_FREQUENCY = 5e6      # 5 MHz laser repetition rate

# LabJack DAC channels for galvo mirrors
TDAC_X = "TDAC2"
TDAC_Y = "TDAC3"

# Single-pixel acquisition settings
DWELL_TIME_S = 5.0         # Seconds to dwell on the pixel (longer = more photons)
N_HIST_BINS = 256          # Number of histogram bins
BINWIDTH_PS = 200          # 200 ps per bin (matches native Flim: 256 × 200 ps = 51.2 ns)

# Hardware delay (system calibration constant)
# Measure this once using calibrate_hardware_delay.py, then fix it here.
# This is the equipment delay where exponential decay begins.
# Value rounded to nearest 200ps to match Time Tagger resolution.
HARDWARE_DELAY_PS = 22400  # Measured: 22380 ± 98 ps → rounded to 200ps bin: 22400 ps

# Data directory (relative to this script's location)
FLIM_DATA_DIR = str(Path(__file__).resolve().parent.parent / "flim_data")


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def load_latest_scan(data_dir):
    """Load the most recent per-pixel FLIM scan and its metadata."""
    save_dir = Path(data_dir)
    npz_files = list(save_dir.glob("scan_*.npz"))

    if not npz_files:
        print(f"No scan files found in {save_dir}")
        return None, None, None

    # Sort by modification time so we always get the truly latest file
    npz_file = max(npz_files, key=lambda p: p.stat().st_mtime)
    meta_file = npz_file.with_suffix(".json")

    print(f"Loading: {npz_file.name}")

    data = np.load(npz_file)
    meta = {}
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

    return data, meta, npz_file


def pick_pixel_on_image(intensity_map, x_axis_volts, y_axis_volts):
    """
    Display the intensity map and let the user click to select a pixel.

    Returns
    -------
    pixel_x, pixel_y : int
        Pixel indices.
    volt_x, volt_y : float
        Corresponding galvo voltages.
    """
    fig, ax = plt.subplots(figsize=(8, 8))

    im = ax.imshow(
        intensity_map,
        cmap='hot',
        origin='lower',
        extent=[x_axis_volts[0], x_axis_volts[-1],
                y_axis_volts[0], y_axis_volts[-1]],
        aspect='auto',
    )
    plt.colorbar(im, ax=ax, label='Photon Count')
    ax.set_xlabel('X voltage (V)')
    ax.set_ylabel('Y voltage (V)')
    ax.set_title('Click on a pixel to select it for single-pixel FLIM')

    selected = {}

    def on_click(event):
        if event.inaxes != ax:
            return
        vx, vy = event.xdata, event.ydata
        # Convert voltage to nearest pixel index
        px = int(np.argmin(np.abs(x_axis_volts - vx)))
        py = int(np.argmin(np.abs(y_axis_volts - vy)))
        selected['px'] = px
        selected['py'] = py
        selected['vx'] = float(x_axis_volts[px])
        selected['vy'] = float(y_axis_volts[py])

        # Draw crosshair on selected point
        for artist in list(ax.lines):
            artist.remove()
        ax.axhline(selected['vy'], color='cyan', linewidth=0.8, linestyle='--')
        ax.axvline(selected['vx'], color='cyan', linewidth=0.8, linestyle='--')
        ax.set_title(
            f'Selected pixel ({px}, {py})  |  '
            f'Voltage ({selected["vx"]:.4f}, {selected["vy"]:.4f})  |  '
            f'Close window to confirm'
        )
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect('button_press_event', on_click)
    plt.show()
    plt.close(fig)
    gc.collect()  # Clean up Tk objects before TimeTagger threads start

    if not selected:
        return None, None, None, None
    return selected['px'], selected['py'], selected['vx'], selected['vy']


def move_galvo(handle, volt_x, volt_y):
    """Move galvo mirrors to the specified voltage position."""
    ljm.eWriteName(handle, TDAC_X, float(volt_x))
    ljm.eWriteName(handle, TDAC_Y, float(volt_y))


def acquire_single_pixel_histogram(tagger, dwell_s):
    """
    Acquire a fluorescence lifetime histogram at the current galvo position.

    Uses PulsedLaserFLIM (no pixel markers needed) to collect photon arrival
    times relative to laser pulses.

    Returns
    -------
    histogram : ndarray
        Photon counts per bin.
    bin_edges : ndarray
        Bin edges in picoseconds.
    stats : dict
        Acquisition statistics.
    """
    from flim_engine import PulsedLaserFLIM

    flim = PulsedLaserFLIM(
        tagger,
        click_channel=DETECTOR_CHANNEL,
        laser_channel=LASER_CHANNEL,
        laser_frequency=LASER_FREQUENCY,
        n_hist_bins=N_HIST_BINS,
        begin_channel=None,     # No pixel markers; free-running
        end_channel=None,
        binwidth=BINWIDTH_PS,   # Linear bins: 256 × 200 ps = 51.2 ns
    )

    print(f"  Acquiring for {dwell_s:.1f} s ...")
    flim.start()

    # Print progress
    t0 = time.time()
    while time.time() - t0 < dwell_s:
        elapsed = time.time() - t0
        stats = flim.get_statistics()
        print(
            f"  {elapsed:5.1f}s / {dwell_s:.1f}s  |  "
            f"photons: {stats['total_photons']:,}  |  "
            f"laser pulses: {stats['total_laser_pulses']:,}",
            end='\r',
        )
        time.sleep(0.5)

    flim.stop()
    print()

    histogram = flim.get_histogram()
    bin_edges = flim.get_bin_edges()
    stats = flim.get_statistics()

    return histogram, bin_edges, stats


def analyze_and_plot(histogram, bin_edges, stats, pixel_x, pixel_y,
                     volt_x, volt_y, save_dir):
    """Analyze the histogram, fit decay curve, and display results."""
    analyzer = FLIMAnalyzer(histogram, bin_edges, fixed_peak_time_ps=HARDWARE_DELAY_PS)
    basic = analyzer.get_statistics()
    fit_single = analyzer.fit_exponential()
    fit_double = analyzer.fit_biexponential()

    print(f"\nAcquisition statistics:")
    print(f"  Total photons:       {stats['total_photons']:,}")
    print(f"  Laser pulses:        {stats['total_laser_pulses']:,}")
    print(f"  Detection efficiency: {stats['detection_efficiency']:.3f}%")

    print(f"\nHistogram statistics:")
    if HARDWARE_DELAY_PS is not None:
        print(f"  Hardware delay (fixed): {HARDWARE_DELAY_PS:.0f} ps")
    else:
        print(f"  Peak position (auto):   {basic['peak_time_ps']:.0f} ps")
    print(f"  Mean arrival:    {basic['mean_arrival_ps']:.1f} ps")
    print(f"  Median arrival:  {basic['median_arrival_ps']:.1f} ps")

    if fit_single['fit_success']:
        print(f"\nSingle exponential fit:")
        print(f"  Lifetime (tau) = {fit_single['tau_ps']:.1f} ps  ({fit_single['tau_ps']/1000:.2f} ns)")
        print(f"  Baseline       = {fit_single.get('baseline', 0):.1f} counts")
        print(f"  R^2            = {fit_single['r_squared']:.4f}")

    if fit_double['fit_success']:
        print(f"\nBiexponential fit:")
        print(f"  tau1 = {fit_double['tau1_ps']:.1f} ps  (A1={fit_double['A1']:.2e})")
        print(f"  tau2 = {fit_double['tau2_ps']:.1f} ps  (A2={fit_double['A2']:.2e})")
        print(f"  Avg lifetime   = {fit_double.get('tau_avg_ps', 0):.1f} ps  ({fit_double.get('tau_avg_ps', 0)/1000:.2f} ns)")
        print(f"  Baseline       = {fit_double.get('baseline', 0):.1f} counts")
        print(f"  R^2            = {fit_double['r_squared']:.4f}")

    # --- Build figure ---
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # Left panel: decay curve with fit
    if fit_single['fit_success']:
        plot_flim_with_fit(histogram, bin_edges, fit_single, ax=axes[0],
                           title="Lifetime Decay Curve")
    else:
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        axes[0].bar(bin_centers, histogram, width=np.diff(bin_edges),
                    alpha=0.7, edgecolor='black')
        axes[0].set_xlabel('Photon Arrival Time (ps)')
        axes[0].set_ylabel('Counts')
        axes[0].set_title('Lifetime Decay Curve (fit failed)')
        axes[0].set_yscale('log')
        axes[0].grid(True, alpha=0.3)

    # Right panel: summary text
    ax_txt = axes[1]
    ax_txt.axis('off')
    summary = (
        f"Single-Pixel FLIM\n"
        f"{'=' * 35}\n\n"
        f"Pixel:    ({pixel_x}, {pixel_y})\n"
        f"Voltage:  ({volt_x:.4f}, {volt_y:.4f}) V\n"
        f"Dwell:    {DWELL_TIME_S:.1f} s\n\n"
        f"Total photons:  {stats['total_photons']:,}\n"
        f"Laser pulses:   {stats['total_laser_pulses']:,}\n"
        f"Efficiency:     {stats['detection_efficiency']:.3f}%\n\n"
        f"Peak at:        {basic['peak_time_ps']:.0f} ps\n"
        f"Mean arrival:   {basic['mean_arrival_ps']:.1f} ps\n"
    )
    if fit_single['fit_success']:
        summary += (
            f"\nSingle exp. fit:\n"
            f"  Lifetime = {fit_single['tau_ps']:.1f} ps ({fit_single['tau_ps']/1000:.2f} ns)\n"
            f"  Baseline = {fit_single.get('baseline', 0):.1f}\n"
            f"  R^2      = {fit_single['r_squared']:.4f}\n"
        )
    if fit_double['fit_success']:
        summary += (
            f"\nBiexp. fit:\n"
            f"  tau1 = {fit_double['tau1_ps']:.1f} ps\n"
            f"  tau2 = {fit_double['tau2_ps']:.1f} ps\n"
            f"  Avg  = {fit_double.get('tau_avg_ps', 0):.1f} ps ({fit_double.get('tau_avg_ps', 0)/1000:.2f} ns)\n"
            f"  R^2  = {fit_double['r_squared']:.4f}\n"
        )
    ax_txt.text(0.05, 0.95, summary, fontfamily='monospace', fontsize=11,
                verticalalignment='top', transform=ax_txt.transAxes)

    plt.tight_layout()
    out_path = Path(save_dir) / f"single_pixel_flim_({pixel_x},{pixel_y}).png"
    plt.savefig(out_path, dpi=150)
    print(f"\nPlot saved to: {out_path}")
    plt.show()
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# QUICK LOOKUP MODE
# ═══════════════════════════════════════════════════════════════════════════

def quick_pixel_lookup():
    """
    Fast query mode: Load lifetime map and per-pixel histograms from latest scan.
    Click on pixels to view their lifetime distribution and fitted decay curve.
    """
    print("=" * 70)
    print("Quick Pixel Lookup (with histogram & fitting)")
    print("=" * 70)

    data, meta, npz_file = load_latest_scan(FLIM_DATA_DIR)
    if data is None:
        return

    # Check for required FLIM data
    if 'flim_lifetime_map_ps' not in data:
        print("No lifetime map found in scanned data.")
        return
    if 'flim_frame_3d' not in data:
        print("ERROR: Per-pixel histograms (frame_3d) not found in scanned data.")
        print("       Please run a new scan with the updated scan_engine.py")
        return
    if 'flim_bin_edges_ps' not in data:
        print("ERROR: FLIM bin edges not found in scanned data.")
        return

    lifetime_map = data['flim_lifetime_map_ps']
    intensity_map = data.get('flim_intensity_map', np.ones_like(lifetime_map))
    frame_3d = data['flim_frame_3d']  # Shape: (ny, nx, nbins)
    bin_edges = data['flim_bin_edges_ps']
    x_axis = data['x_axis_volts']
    y_axis = data['y_axis_volts']

    print(f"Loaded: {npz_file.name}")
    print(f"  Image size: {lifetime_map.shape}")
    print(f"  Frame shape: {frame_3d.shape}")
    print(f"  Bins: {len(bin_edges)-1} edges, {bin_edges[0]:.0f}-{bin_edges[-1]:.0f} ps")
    print(f"  X range: {x_axis[0]:.4f} to {x_axis[-1]:.4f} V")
    print(f"  Y range: {y_axis[0]:.4f} to {y_axis[-1]:.4f} V")
    print(f"\nInstructions:")
    print(f"  - Click a pixel to view its histogram and fitted decay curve")
    print(f"  - Close the scatter plot window to proceed")

    # Create figure with lifetime map (mask zero-lifetime pixels)
    fig, ax = plt.subplots(figsize=(8, 8))
    masked_lifetime = np.where(lifetime_map > 0, lifetime_map, np.nan)
    im = ax.imshow(
        masked_lifetime,
        cmap='hot',
        origin='lower',
        extent=[x_axis[0], x_axis[-1], y_axis[0], y_axis[-1]],
        aspect='auto',
    )
    plt.colorbar(im, ax=ax, label='Lifetime (ps)')
    ax.set_xlabel('X voltage (V)')
    ax.set_ylabel('Y voltage (V)')
    ax.set_title('Click pixel to view histogram + fit  |  Close window to finish')

    selected_pixel = {'px': None, 'py': None}

    def on_click(event):
        if event.inaxes != ax or selected_pixel['px'] is not None:
            return  # Only allow one selection
        vx, vy = event.xdata, event.ydata
        px = int(np.argmin(np.abs(x_axis - vx)))
        py = int(np.argmin(np.abs(y_axis - vy)))
        selected_pixel['px'] = px
        selected_pixel['py'] = py

        # Mark with red crosshair
        ax.plot(x_axis[px], y_axis[py], 'r+', markersize=15, markeredgewidth=2)
        ax.set_title(f'Selected ({px}, {py})  |  Close window to analyze')
        fig.canvas.draw_idle()
        plt.close(fig)  # Auto-close after selection

    fig.canvas.mpl_connect('button_press_event', on_click)
    plt.show()
    plt.close(fig)
    gc.collect()

    if selected_pixel['px'] is None:
        print("No pixel selected.")
        return

    # Extract and analyze the selected pixel's histogram
    px, py = selected_pixel['px'], selected_pixel['py']
    histogram = frame_3d[py, px, :]
    total_photons = np.sum(histogram)

    print(f"\n" + "=" * 70)
    print(f"Pixel ({px}, {py}) Analysis")
    print(f"=" * 70)
    print(f"Total photons: {total_photons:.0f}")

    if total_photons < 10:
        print(f"WARNING: Very few photons ({total_photons:.0f}), fitting may not be reliable.")

    # Fit the histogram with fixed hardware delay (if calibrated) or auto-detect peak
    try:
        analyzer = FLIMAnalyzer(histogram, bin_edges, fixed_peak_time_ps=HARDWARE_DELAY_PS)
        basic = analyzer.get_statistics()
        fit_single = analyzer.fit_exponential()
        fit_double = analyzer.fit_biexponential()

        print(f"\nHistogram statistics:")
        if HARDWARE_DELAY_PS is not None:
            print(f"  Hardware delay (fixed): {HARDWARE_DELAY_PS:.0f} ps")
        else:
            print(f"  Peak position (auto):   {basic['peak_time_ps']:.0f} ps")
        print(f"  Mean arrival:    {basic['mean_arrival_ps']:.1f} ps")
        print(f"  Median arrival:  {basic['median_arrival_ps']:.1f} ps")

        if fit_single['fit_success']:
            print(f"\nSingle exponential fit:")
            print(f"  Lifetime (tau) = {fit_single['tau_ps']:.1f} ps  ({fit_single['tau_ps']/1000:.2f} ns)")
            print(f"  Baseline       = {fit_single.get('baseline', 0):.1f} counts")
            print(f"  R^2            = {fit_single['r_squared']:.4f}")

        if fit_double['fit_success']:
            print(f"\nBiexponential fit:")
            print(f"  tau1 = {fit_double['tau1_ps']:.1f} ps  (A1={fit_double['A1']:.2e})")
            print(f"  tau2 = {fit_double['tau2_ps']:.1f} ps  (A2={fit_double['A2']:.2e})")
            print(f"  Avg lifetime   = {fit_double.get('tau_avg_ps', 0):.1f} ps  ({fit_double.get('tau_avg_ps', 0)/1000:.2f} ns)")
            print(f"  Baseline       = {fit_double.get('baseline', 0):.1f} counts")
            print(f"  R^2            = {fit_double['r_squared']:.4f}")

        # Build visualization figure
        fig_out, axes = plt.subplots(1, 2, figsize=(16, 6))

        # Left panel: decay curve with fit
        if fit_single['fit_success']:
            plot_flim_with_fit(histogram, bin_edges, fit_single, ax=axes[0],
                               title="Lifetime Decay Curve (from scanned data)")
        else:
            bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
            axes[0].bar(bin_centers, histogram, width=np.diff(bin_edges),
                        alpha=0.7, edgecolor='black')
            axes[0].set_xlabel('Photon Arrival Time (ps)')
            axes[0].set_ylabel('Counts')
            axes[0].set_title('Lifetime Decay Curve (fit failed)')
            axes[0].set_yscale('log')
            axes[0].grid(True, alpha=0.3)

        # Right panel: summary text
        ax_txt = axes[1]
        ax_txt.axis('off')
        summary = (
            f"Quick Lookup Analysis\n"
            f"{'=' * 35}\n\n"
            f"Pixel:    ({px}, {py})\n"
            f"Total photons: {total_photons:.0f}\n"
            f"Peak at:  {basic['peak_time_ps']:.0f} ps\n"
            f"Mean arrival: {basic['mean_arrival_ps']:.1f} ps\n\n"
        )
        if fit_single['fit_success']:
            summary += (
                f"Single exp. fit:\n"
                f"  Lifetime = {fit_single['tau_ps']:.1f} ps\n"
                f"             ({fit_single['tau_ps']/1000:.2f} ns)\n"
                f"  Baseline = {fit_single.get('baseline', 0):.1f}\n"
                f"  R^2      = {fit_single['r_squared']:.4f}\n"
            )
        if fit_double['fit_success']:
            summary += (
                f"\nBiexp. fit:\n"
                f"  tau1 = {fit_double['tau1_ps']:.1f} ps\n"
                f"  tau2 = {fit_double['tau2_ps']:.1f} ps\n"
                f"  Avg  = {fit_double.get('tau_avg_ps', 0):.1f} ps\n"
                f"         ({fit_double.get('tau_avg_ps', 0)/1000:.2f} ns)\n"
                f"  R^2  = {fit_double['r_squared']:.4f}\n"
            )
        ax_txt.text(0.05, 0.95, summary, fontfamily='monospace', fontsize=11,
                    verticalalignment='top', transform=ax_txt.transAxes)

        plt.tight_layout()
        out_path = Path(FLIM_DATA_DIR) / f"quick_lookup_pixel_({px},{py}).png"
        plt.savefig(out_path, dpi=150)
        print(f"\nPlot saved to: {out_path}")
        plt.show()
        plt.close(fig_out)

    except Exception as e:
        print(f"ERROR during fitting: {e}")
        import traceback
        traceback.print_exc()


def acquire_continuous_pixel_histogram(tagger, pixel_x, pixel_y, max_dwell_s=300):
    """
    Continuously acquire a fluorescence lifetime histogram at the current galvo position.
    Displays live updating plot with decay curve and fitting parameters.
    User can click "Stop" button to end acquisition.

    Parameters
    ----------
    tagger : TimeTagger
        Time Tagger device handle
    pixel_x, pixel_y : int
        Pixel indices (for display)
    max_dwell_s : float
        Maximum dwell time before auto-stop (default 5 minutes)

    Returns
    -------
    histogram : ndarray
        Accumulated photon counts per bin.
    bin_edges : ndarray
        Bin edges in picoseconds.
    stats : dict
        Final acquisition statistics.
    """
    from flim_engine import PulsedLaserFLIM

    flim = PulsedLaserFLIM(
        tagger,
        click_channel=DETECTOR_CHANNEL,
        laser_channel=LASER_CHANNEL,
        laser_frequency=LASER_FREQUENCY,
        n_hist_bins=N_HIST_BINS,
        begin_channel=None,
        end_channel=None,
        binwidth=BINWIDTH_PS,
    )

    print(f"  Starting continuous acquisition (max {max_dwell_s:.0f}s)...")
    flim.start()

    # Create interactive figure with Stop button
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle(f'Live Single-Pixel FLIM: Pixel ({pixel_x}, {pixel_y})', fontsize=14)

    # Add Stop button
    ax_stop = plt.axes([0.45, 0.05, 0.1, 0.075])
    btn_stop = plt.Button(ax_stop, 'STOP', color='red', hovercolor='darkred')
    
    stop_flag = {'stop': False}

    def on_stop_click(event):
        stop_flag['stop'] = True
        btn_stop.label.set_text('Stopping...')
        fig.canvas.draw_idle()

    btn_stop.on_clicked(on_stop_click)

    # Enable interactive mode
    plt.ion()

    t0 = time.time()
    update_interval = 0.5  # Update plot every 0.5 seconds
    t_last_update = t0

    print("  Acquiring (click STOP button to end)...\n")

    try:
        while not stop_flag['stop'] and (time.time() - t0) < max_dwell_s:
            elapsed = time.time() - t0

            # Periodically update plot
            if elapsed - (t_last_update - t0) >= update_interval:
                t_last_update = time.time()
                histogram = flim.get_histogram()
                bin_edges = flim.get_bin_edges()
                stats = flim.get_statistics()
                total_photons = stats['total_photons']

                # Clear and redraw plots
                for ax in axes:
                    ax.clear()

                # Left: histogram + decay curve
                if total_photons > 10:
                    try:
                        analyzer = FLIMAnalyzer(histogram, bin_edges, fixed_peak_time_ps=HARDWARE_DELAY_PS)
                        fit_result = analyzer.fit_exponential()
                        plot_flim_with_fit(histogram, bin_edges, fit_result, ax=axes[0],
                                         title="Live Decay Curve")
                    except:
                        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
                        axes[0].bar(bin_centers, histogram, width=np.diff(bin_edges),
                                   alpha=0.7, edgecolor='black')
                        axes[0].set_xlabel('Photon Arrival Time (ps)')
                        axes[0].set_ylabel('Counts')
                        axes[0].set_title('Live Decay Curve')
                        axes[0].set_yscale('log')
                else:
                    axes[0].text(0.5, 0.5, 'Acquiring photons...', ha='center', va='center',
                                transform=axes[0].transAxes, fontsize=12)
                    axes[0].set_xlim(0, 1)
                    axes[0].set_ylim(0, 1)

                # Right: live statistics
                axes[1].axis('off')
                summary = (
                    f"LIVE Acquisition Status\n"
                    f"{'=' * 35}\n\n"
                    f"Elapsed time:    {elapsed:.1f} s\n"
                    f"Total photons:   {total_photons:,}\n"
                    f"Laser pulses:    {stats['total_laser_pulses']:,}\n"
                    f"Detection eff.:  {stats['detection_efficiency']:.3f}%\n"
                )

                if total_photons > 10:
                    try:
                        analyzer = FLIMAnalyzer(histogram, bin_edges, fixed_peak_time_ps=HARDWARE_DELAY_PS)
                        basic = analyzer.get_statistics()
                        fit_single = analyzer.fit_exponential()
                        fit_double = analyzer.fit_biexponential()

                        if HARDWARE_DELAY_PS is not None:
                            summary += (
                                f"\nHardware delay (fixed): {HARDWARE_DELAY_PS:.0f} ps\n"
                                f"Mean arrival:          {basic['mean_arrival_ps']:.1f} ps\n"
                            )
                        else:
                            summary += (
                                f"\nPeak at:           {basic['peak_time_ps']:.0f} ps\n"
                                f"Mean arrival:      {basic['mean_arrival_ps']:.1f} ps\n"
                            )

                        if fit_single['fit_success']:
                            summary += (
                                f"\nSingle exp fit:\n"
                                f"  τ = {fit_single['tau_ps']:.1f} ps\n"
                                f"    = {fit_single['tau_ps']/1000:.2f} ns\n"
                                f"  R² = {fit_single['r_squared']:.4f}\n"
                            )

                        if fit_double['fit_success']:
                            summary += (
                                f"\nBiexp fit:\n"
                                f"  τ_avg = {fit_double.get('tau_avg_ps', 0):.1f} ps\n"
                                f"        = {fit_double.get('tau_avg_ps', 0)/1000:.2f} ns\n"
                                f"  R² = {fit_double['r_squared']:.4f}\n"
                            )
                    except:
                        pass

                axes[1].text(0.05, 0.95, summary, fontfamily='monospace', fontsize=10,
                            verticalalignment='top', transform=axes[1].transAxes)

                fig.suptitle(
                    f'Live Single-Pixel FLIM: Pixel ({pixel_x}, {pixel_y}) | '
                    f'Elapsed: {elapsed:.1f}s | Photons: {total_photons:,}',
                    fontsize=14
                )
                plt.tight_layout(rect=[0, 0.1, 1, 0.96])
                fig.canvas.draw_idle()

            plt.pause(0.01)  # Allow GUI interaction

    except KeyboardInterrupt:
        print("Interrupted by user")
    finally:
        flim.stop()
        plt.ioff()
        plt.close(fig)
        gc.collect()

    histogram = flim.get_histogram()
    bin_edges = flim.get_bin_edges()
    stats = flim.get_statistics()

    print(f"\nAcquisition stopped after {elapsed:.1f}s")
    return histogram, bin_edges, stats


# ═══════════════════════════════════════════════════════════════════════════
# PRECISION MEASUREMENT MODE
# ═══════════════════════════════════════════════════════════════════════════

def precision_single_pixel_scan():
    """
    Precision mode: Full 5-second single-pixel acquisition with high-statistics
    fitting (quality: high, time: slow).
    """
    print("=" * 70)
    print("Precision Single-Pixel FLIM (5 sec acquisition)")
    print("=" * 70)

    # -- Step 1: Load the latest per-pixel FLIM scan -------------------
    data, meta, npz_file = load_latest_scan(FLIM_DATA_DIR)
    if data is None:
        return

    # Get the maps; prefer per-pixel data, fall back to counts
    if 'flim_intensity_map' in data and np.any(data['flim_intensity_map'] > 0):
        intensity_map = data['flim_intensity_map']
    elif 'counts_ch_y_x' in data:
        intensity_map = data['counts_ch_y_x'][0]  # Channel 0
    else:
        print("No usable image data found in the file.")
        return

    x_axis = data['x_axis_volts']
    y_axis = data['y_axis_volts']

    print(f"  Image size: {intensity_map.shape}")
    print(f"  X range: {x_axis[0]:.4f} to {x_axis[-1]:.4f} V")
    print(f"  Y range: {y_axis[0]:.4f} to {y_axis[-1]:.4f} V")

    # -- Step 2: Let the user click a pixel ----------------------------
    print("\nA plot window will open. Click on a pixel, then close the window.")
    pixel_x, pixel_y, volt_x, volt_y = pick_pixel_on_image(
        intensity_map, x_axis, y_axis,
    )
    if pixel_x is None:
        print("No pixel selected. Exiting.")
        return

    print(f"\nSelected pixel ({pixel_x}, {pixel_y})")
    print(f"  Target voltage: X={volt_x:.4f} V, Y={volt_y:.4f} V")

    # -- Step 3: Open hardware -----------------------------------------
    print("\nConnecting to hardware...")
    handle = ljm.openS("T7", "ANY", "ANY")
    tagger = TimeTagger.createTimeTagger()
    print(f"  LabJack: connected")
    print(f"  Time Tagger: {tagger.getModel()} ({tagger.getSerial()})")

    # -- Step 4: Move to the selected pixel ----------------------------
    print(f"\nMoving galvo to ({volt_x:.4f}, {volt_y:.4f}) V ...")
    move_galvo(handle, volt_x, volt_y)
    time.sleep(0.1)  # Let mirrors settle
    print("  Done.")

    # -- Step 4b: Choose acquisition mode ----------------------------
    print(f"\n" + "=" * 70)
    print("ACQUISITION MODE")
    print("=" * 70)
    print("1. Fixed Time    - Acquire for {:.1f} seconds (recommended for quick results)".format(DWELL_TIME_S))
    print("2. Continuous    - Real-time acquisition with live curve (watch τ improve)")
    print("=" * 70)
    
    mode_choice = input("Choose mode (1 or 2): ").strip()
    
    if mode_choice == "2":
        # -- Continuous acquisition with live plot
        print(f"\nAcquiring continuously at pixel ({pixel_x}, {pixel_y})...")
        histogram, bin_edges, stats = acquire_continuous_pixel_histogram(
            tagger, pixel_x, pixel_y, max_dwell_s=300
        )
    else:
        # -- Fixed time acquisition
        print(f"\nAcquiring single-pixel FLIM ({DWELL_TIME_S:.1f} s dwell)...")
        histogram, bin_edges, stats = acquire_single_pixel_histogram(
            tagger, DWELL_TIME_S,
        )

    # -- Step 5: Analyze & display -------------------------------------
    analyze_and_plot(
        histogram, bin_edges, stats,
        pixel_x, pixel_y, volt_x, volt_y,
        save_dir=FLIM_DATA_DIR,
    )

    # -- Cleanup -------------------------------------------------------
    ljm.close(handle)
    print("\nDone.")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """
    Main entry point: Let user choose between fast query or precision measurement.
    """
    print("\n" + "=" * 70)
    print("SINGLE-PIXEL FLIM OPTIONS")
    print("=" * 70)
    print("1. Quick Lookup    - Read lifetime from existing scan (fast, ~20ms/pixel quality)")
    print("2. Precision Scan  - Full 5-second acquisition (slow, ~2% precision)")
    print("=" * 70)

    choice = input("Choose mode (1 or 2): ").strip()

    if choice == "1":
        quick_pixel_lookup()
    elif choice == "2":
        precision_single_pixel_scan()
    else:
        print("Invalid choice.")


if __name__ == "__main__":
    main()
