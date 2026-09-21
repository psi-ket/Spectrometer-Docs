"""
calibrate_hardware_delay.py — Estimate hardware delay from existing FLIM scan data.

This script estimates the equipment delay by analyzing the global histogram
from previously acquired scans. Run this once to determine your system's
hardware delay, then update the HARDWARE_DELAY_PS constant in
example_SinglePixelFLIM_scan.py
"""

import numpy as np
from pathlib import Path
import json


def estimate_delay_from_scan(scan_file):
    """
    Estimate hardware delay from a single .npz scan file.
    
    Parameters
    ----------
    scan_file : str or Path
        Path to .npz file from previous scan
        
    Returns
    -------
    float
        Estimated hardware delay in picoseconds
    """
    scan_file = Path(scan_file)
    
    try:
        data = np.load(scan_file)
    except Exception as e:
        print(f"ERROR: Could not load {scan_file}: {e}")
        return None
    
    # Check for required data
    if 'flim_frame_3d' not in data or 'flim_bin_edges_ps' not in data:
        print(f"  Skipping {scan_file.name}: Missing flim_frame_3d or flim_bin_edges_ps")
        return None
    
    frame_3d = data['flim_frame_3d']
    bin_edges = data['flim_bin_edges_ps']
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    
    # Sum all pixels to get global histogram
    global_hist = np.sum(frame_3d, axis=(0, 1))
    
    if np.sum(global_hist) == 0:
        print(f"  Skipping {scan_file.name}: Empty histogram")
        return None
    
    # Find peak (hardware delay location)
    peak_idx = int(np.argmax(global_hist))
    hardware_delay_ps = float(bin_centers[peak_idx])
    
    return hardware_delay_ps


def main():
    print("=" * 70)
    print("FLIM Hardware Delay Calibration")
    print("=" * 70)
    
    flim_data_dir = Path(__file__).resolve().parent.parent / "flim_data"
    
    if not flim_data_dir.exists():
        print(f"ERROR: flim_data directory not found at {flim_data_dir}")
        return
    
    # Find all .npz scan files
    scan_files = sorted(flim_data_dir.glob("scan_*.npz"), 
                       key=lambda p: p.stat().st_mtime, reverse=True)
    
    if not scan_files:
        print(f"ERROR: No scan_*.npz files found in {flim_data_dir}")
        return
    
    print(f"\nFound {len(scan_files)} scan file(s) in {flim_data_dir}\n")
    
    delays = []
    
    for scan_file in scan_files:
        print(f"Analyzing: {scan_file.name}")
        delay = estimate_delay_from_scan(scan_file)
        
        if delay is not None:
            delays.append(delay)
            print(f"  → Hardware delay: {delay:.1f} ps\n")
    
    if not delays:
        print("\nERROR: Could not estimate delay from any scan files")
        return
    
    # Compute statistics
    mean_delay = np.mean(delays)
    std_delay = np.std(delays)
    
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Number of measurements: {len(delays)}")
    print(f"Mean hardware delay:    {mean_delay:.1f} ps")
    print(f"Std deviation:          {std_delay:.1f} ps")
    print(f"Range:                  {np.min(delays):.1f} - {np.max(delays):.1f} ps")
    
    print(f"\n{'=' * 70}")
    print(f"RECOMMENDED SETTING")
    print(f"{'=' * 70}")
    
    # Round to nearest 200ps to match Time Tagger accuracy
    rounded_delay = round(mean_delay / 200) * 200
    
    print(f"Time Tagger accuracy: 200 ps")
    print(f"Measured delay:       {mean_delay:.1f} ps")
    print(f"Rounded to 200ps bin: {rounded_delay:.0f} ps\n")
    
    print(f"Add this to example_SinglePixelFLIM_scan.py CONFIGURATION section:\n")
    print(f"    HARDWARE_DELAY_PS = {rounded_delay:.0f}  # ± {std_delay:.1f} ps (±{std_delay/200:.1f} bins)")
    print(f"\n(This is a fixed property of your instrument setup)\n")
    
    if abs(rounded_delay - mean_delay) > std_delay:
        print(f"⚠️  WARNING: Rounding error ({abs(rounded_delay - mean_delay):.0f} ps) exceeds uncertainty ({std_delay:.1f} ps)")
        print(f"   Consider verifying with additional calibration scans.")


if __name__ == "__main__":
    main()
