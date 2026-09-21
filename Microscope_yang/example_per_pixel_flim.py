"""
example_per_pixel_flim.py — Per-pixel FLIM imaging examples.

Demonstrates the native Time Tagger Flim measurement class for
fluorescence lifetime imaging microscopy with lifetime maps.
"""

import numpy as np
import matplotlib
matplotlib.use('TkAgg')  # Use interactive backend for displaying plots
import matplotlib.pyplot as plt
from pathlib import Path

from scan_engine import ScanConfig, ScanWorker

try:
    import TimeTagger
except ImportError:
    print("Error: TimeTagger SDK not installed.")
    exit(1)


def example_1_basic_per_pixel_flim():
    """
    Example 1: Basic per-pixel FLIM scan with lifetime map generation.
    """
    print("=" * 70)
    print("EXAMPLE 1: Basic Per-Pixel FLIM Scan")
    print("=" * 70)
    
    config = ScanConfig(
        # Spatial parameters
        nx=50,
        ny=50,
        dwell_s=0.02,  # 20 ms per pixel
        settle_s=0.001,
        
        # Pulsed laser FLIM parameters
        flim_enabled=True,
        flim_use_native=True,      # ⭐ Use native Flim for per-pixel histograms
        flim_laser_channel=1,      # Set to channel 1 for your pulsed laser
        flim_laser_frequency=5e6,  # Set to 5 MHz
        flim_detector_channel=2,
        flim_n_bins=256,           # 256 histogram bins per pixel
        flim_binwidth=200,          # 200 ps per bin → 51.2 ns total range (256 bins)
        flim_use_conditional_filter=True,
        
        save_dir="./flim_data",
        continuous=False,
    )
    
    print(f"\nConfiguration:")
    print(f"  Scan size: {config.nx}×{config.ny} pixels")
    print(f"  Dwell per pixel: {config.dwell_s*1000:.0f} ms")
    print(f"  Total acquisition time: ~{config.nx*config.ny*config.dwell_s:.0f} seconds")
    print(f"  Time range per histogram: {config.flim_n_bins * config.flim_binwidth / 1000:.1f} ns")
    print()
    
    try:
        tagger = TimeTagger.createTimeTagger()
        print(f"Connected to: {tagger.getModel()} ({tagger.getSerial()})\n")
    except Exception as e:
        print(f"Error connecting to Time Tagger: {e}")
        return
    
    worker = ScanWorker(config, tagger)
    
    def on_progress(current, total):
        per_progress = 100 * current / total
        print(f"Progress: {per_progress:5.1f}% ({current}/{total})", end='\r')
    
    def on_scan_complete(result):
        print("\n" + "=" * 70)
        print("SCAN COMPLETE")
        print("=" * 70)
        
        print(f"Data saved to: {result['npz_path']}")
        print(f"Result dictionary keys: {list(result.keys())}")
        
        # ⭐ Per-pixel FLIM results
        if 'flim_lifetime_map' in result:
            lifetime_map = result['flim_lifetime_map']
            intensity_map = result['flim_intensity_map']
            stats = result['flim_stats']
            
            print(f"\nPer-Pixel FLIM Results:")
            print(f"  Lifetime map shape: {lifetime_map.shape}")
            valid = lifetime_map[lifetime_map > 0]
            if len(valid) > 0:
                print(f"  Mean τ:  {np.mean(valid):.0f} ps  ({np.mean(valid)/1000:.2f} ns)")
                print(f"  Median τ: {np.median(valid):.0f} ps")
                print(f"  Std τ:   {np.std(valid):.0f} ps")
                print(f"  Range:   {np.min(valid):.0f} - {np.max(valid):.0f} ps")
            print(f"  Valid pixels: {len(valid)} / {lifetime_map.size}")
            print(f"  Total photons: {stats['total_photons']}")
            print(f"  Mean photons/pixel: {stats['mean_photons_per_pixel']:.0f}")
            print(f"  Max photons in pixel: {stats['max_photons_in_pixel']}")
            
            # Visualize results
            print("Creating matplotlib figure...")
            fig, axes = plt.subplots(1, 2, figsize=(14, 5))
            
            # Lifetime map — mask zero-lifetime pixels and use sensible colour range
            masked_lt = np.where(lifetime_map > 0, lifetime_map, np.nan)
            im1 = axes[0].imshow(masked_lt, cmap='viridis', origin='lower')
            plt.colorbar(im1, ax=axes[0], label='Lifetime τ (ps)')
            axes[0].set_title('Per-Pixel Fluorescence Lifetime')
            axes[0].set_xlabel('X pixel')
            axes[0].set_ylabel('Y pixel')
            
            # Intensity map
            im2 = axes[1].imshow(intensity_map, cmap='hot', origin='lower')
            plt.colorbar(im2, ax=axes[1], label='Photon Count')
            axes[1].set_title('Per-Pixel Intensity')
            axes[1].set_xlabel('X pixel')
            axes[1].set_ylabel('Y pixel')
            
            plt.tight_layout()
            plt.savefig(Path(config.save_dir) / "flim_per_pixel_map.png", dpi=150)
            print(f"Visualization saved: flim_per_pixel_map.png")
            print("Attempting to display plot...")
            plt.show()  # Keep window open
        else:
            print("Warning: Per-pixel FLIM data not found in results")
            print("Available keys:", list(result.keys()))
    
    def on_error(error_msg):
        print(f"ERROR: {error_msg}")
    
    worker.progress.connect(on_progress)
    worker.finished_scan.connect(on_scan_complete)
    worker.error.connect(on_error)
    
    print("Starting per-pixel FLIM scan...\n")
    worker.run()


if __name__ == "__main__":
    print("\n" + "▀" * 70)
    print("Per-Pixel FLIM Example - Basic Scan")
    print("▀" * 70 + "\n")
    
    example_1_basic_per_pixel_flim()

