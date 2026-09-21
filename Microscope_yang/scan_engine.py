"""
scan_engine.py — Scanning logic running in a QThread.

Refactored from the user's raster-scan script to emit Qt signals
for live progress and image reconstruction.
"""

import time
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

# ---------------------------------------------------------------------------
# Hardware imports — wrapped so the GUI can *launch* without them
# ---------------------------------------------------------------------------
try:
    from labjack import ljm
    HAS_LABJACK = True
except ImportError:
    ljm = None
    HAS_LABJACK = False

try:
    import TimeTagger
    HAS_TIMETAGGER = True
except ImportError:
    TimeTagger = None
    HAS_TIMETAGGER = False

try:
    from CBMMC import CountBetweenMarkersMultiChannels
    HAS_CBMMC = True
except ImportError:
    CountBetweenMarkersMultiChannels = None
    HAS_CBMMC = False

try:
    from flim_engine import (PulsedLaserFLIM, PulsedLaserConfig, 
                             NativeFlimWrapper, FlimConfig, 
                             configure_pulsed_laser_hardware)
    HAS_FLIM = True
except ImportError:
    PulsedLaserFLIM = None
    PulsedLaserConfig = None
    NativeFlimWrapper = None
    FlimConfig = None
    configure_pulsed_laser_hardware = None
    HAS_FLIM = False


# ═══════════════════════════════════════════════════════════════════════════
# CONFIG DATACLASS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ScanConfig:
    """All tuneable parameters for a single scan."""
    nx: int = 100
    ny: int = 100
    dwell_s: float = 0.004
    settle_s: float = 0.0
    x_vmin: float = -0.8
    x_vmax: float = 0.222
    y_vmin: float = 0.26
    y_vmax: float = 0.9
    bidirectional: bool = True
    tdac_x: str = "TDAC2"
    tdac_y: str = "TDAC3"
    marker_dio: str = "FIO1"
    marker_pulse_s: float = 0.0001
    click_channels: list = field(default_factory=lambda: [2])
    marker_channel: int = 4
    marker_trigger_level: float = 0.2
    click_trigger_level: float = 0.125
    save_dir: str = "."
    continuous: bool = False
    
    # ⭐ FLIM (Fluorescence Lifetime Imaging Microscopy) - Per-Pixel (RECOMMENDED)
    flim_enabled: bool = False
    flim_use_native: bool = True  # Use native Time Tagger Flim (vs custom)
    flim_laser_channel: int = 3  # Laser trigger channel
    flim_detector_channel: int = 2  # Detector channel
    flim_n_bins: int = 1024  # Histogram bins per pixel
    flim_binwidth: int = 200  # Bin width in picoseconds (200 ps × 1024 = 204.8 ns)
    flim_use_conditional_filter: bool = True
    flim_use_reference_clock: bool = False
    flim_hardware_delay_ps: float = 0.0  # Hardware delay for MLE peak position
    flim_fit_end_ps: float = 0.0           # Truncate fit window at this time (0 = full window)
    
    # Legacy: Custom FLIM (global histogram, kept for backward compatibility)
    flim_custom_enabled: bool = False
    flim_laser_frequency: float = 5e6  # 5 MHz
    flim_use_custom_bins: bool = True
    flim_exp_start: int = 0  # 10^0 = 1 ps
    flim_exp_stop: int = 4   # 10^4 = 10 ns


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS (pure functions, no hardware)
# ═══════════════════════════════════════════════════════════════════════════

def generate_raster_points(nx, ny, x_vmin, x_vmax, y_vmin, y_vmax,
                           bidirectional=True):
    xs = np.linspace(x_vmin, x_vmax, nx, dtype=np.float64)
    ys = np.linspace(y_vmin, y_vmax, ny, dtype=np.float64)

    x_list, y_list, ij_list = [], [], []
    for iy, yv in enumerate(ys):
        if bidirectional and (iy % 2 == 1):
            x_row = xs[::-1]
            ix_iter = range(nx - 1, -1, -1)
        else:
            x_row = xs
            ix_iter = range(nx)
        for xv, ix in zip(x_row, ix_iter):
            x_list.append(xv)
            y_list.append(yv)
            ij_list.append((iy, ix))

    return np.array(x_list), np.array(y_list), ij_list, xs, ys


def reshape_counts_to_image(data_ch_by_bin, ij_list, ny, nx):
    n_ch = data_ch_by_bin.shape[0]
    img = np.zeros((n_ch, ny, nx), dtype=data_ch_by_bin.dtype)
    for k, (iy, ix) in enumerate(ij_list):
        img[:, iy, ix] = data_ch_by_bin[:, k]
    return img


# ═══════════════════════════════════════════════════════════════════════════
# SCAN WORKER (QThread)
# ═══════════════════════════════════════════════════════════════════════════

class ScanWorker(QThread):
    """
    Runs a full raster scan in a background thread.

    Signals
    -------
    progress(int, int)  — (current_pixel, total_pixels)
    row_done(int, ndarray) — (row_index, partial_image)   for live display
    finished_scan(dict)  — full result payload
    error(str)           — human-readable error message
    """

    progress = pyqtSignal(int, int)
    row_done = pyqtSignal(int, object)      # row_idx, img array (n_ch, ny, nx)
    finished_scan = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, config: ScanConfig, tagger_instance, parent=None):
        super().__init__(parent)
        self.cfg = config
        self.tagger = tagger_instance
        self._abort = False

    # -- public API ----------------------------------------------------------

    def abort(self):
        self._abort = True

    # -- thread entry --------------------------------------------------------

    def run(self):  # noqa: C901
        cfg = self.cfg
        try:
            self._do_scan(cfg)
        except Exception as exc:
            self.error.emit(str(exc))

    # -- internal ------------------------------------------------------------

    def _do_scan(self, cfg: ScanConfig):
        if not HAS_LABJACK:
            self.error.emit("labjack-ljm library not found. Install it to scan.")
            return
        if not HAS_TIMETAGGER:
            self.error.emit("TimeTagger library not found. Install the Swabian SDK.")
            return
        if not HAS_CBMMC:
            self.error.emit("CBMMC module not found.")
            return

        n_pixels = cfg.nx * cfg.ny
        x_vals, y_vals, ij_list, x_axis, y_axis = generate_raster_points(
            cfg.nx, cfg.ny, cfg.x_vmin, cfg.x_vmax,
            cfg.y_vmin, cfg.y_vmax, bidirectional=cfg.bidirectional,
        )

        # ---- Open LabJack ---------------------------------------------------
        handle = ljm.openS("T7", "ANY", "ANY")
        ljm.eWriteName(handle, cfg.marker_dio, 0)

        # ---- Prepare TimeTagger ---------------------------------------------
        tagger = self.tagger
        tagger.setTriggerLevel(cfg.marker_channel, cfg.marker_trigger_level)
        for ch in cfg.click_channels:
            tagger.setTriggerLevel(ch, cfg.click_trigger_level)
        try:
            if tagger.getModel() != "Time Tagger 20":
                tagger.xtra_setHighPrioChannel(cfg.marker_channel, True)
        except Exception:
            pass

        while True:
            if self._abort:
                break

            # Configure pulsed laser hardware if FLIM is enabled
            if cfg.flim_enabled and HAS_FLIM:
                # Create PulsedLaserConfig for hardware configuration
                laser_cfg = PulsedLaserConfig()
                laser_cfg.enabled = True
                laser_cfg.laser_channel = cfg.flim_laser_channel
                laser_cfg.detector_channel = cfg.flim_detector_channel
                laser_cfg.laser_frequency = cfg.flim_laser_frequency
                # ⭐ IMPORTANT: Disable conditional filter for native Flim 
                # (it needs full laser pulse train for histogram timing)
                laser_cfg.use_conditional_filter = cfg.flim_use_conditional_filter and not cfg.flim_use_native
                laser_cfg.use_reference_clock = cfg.flim_use_reference_clock
                laser_cfg.use_custom_bins = cfg.flim_use_custom_bins
                laser_cfg.n_hist_bins = cfg.flim_n_bins
                laser_cfg.exp_start = cfg.flim_exp_start
                laser_cfg.exp_stop = cfg.flim_exp_stop
                
                # Configure hardware for BOTH native and custom FLIM
                print(f"\n[FLIM] Configuring pulsed laser hardware...")
                configure_pulsed_laser_hardware(tagger, laser_cfg)
                print(f"[FLIM]   - Hardware delays aligned")
                print(f"[FLIM]   - Conditional filtering: {laser_cfg.use_conditional_filter} (disabled for native Flim)")
                print(f"[FLIM]   - Reference clock: {cfg.flim_use_reference_clock}")


            sync = TimeTagger.SynchronizedMeasurements(tagger)
            cbm = CountBetweenMarkersMultiChannels(
                sync.getTagger(),
                click_channels=cfg.click_channels,
                begin_channel=cfg.marker_channel,
                end_channel=None,
                n_bins=n_pixels + 4,
            )
            
            # Initialize FLIM measurement if enabled
            flim = None
            flim_native = None
            
            if cfg.flim_enabled and HAS_FLIM:
                if cfg.flim_use_native:
                    # ⭐ Use native Time Tagger Flim (per-pixel histograms)
                    # ⚠️ IMPORTANT: Create Flim with raw tagger, NOT sync.getTagger()
                    # Native measurements don't work within SynchronizedMeasurements context
                    try:
                        print(f"\n[FLIM] Initializing native Flim measurement...")
                        print(f"[FLIM]   Laser channel: {cfg.flim_laser_channel}")
                        print(f"[FLIM]   Detector channel: {cfg.flim_detector_channel}")
                        print(f"[FLIM]   Pixel marker channel: {cfg.marker_channel}")
                        print(f"[FLIM]   Pixels: {cfg.nx}x{cfg.ny} = {cfg.nx*cfg.ny}")
                        print(f"[FLIM]   Bins: {cfg.flim_n_bins} x {cfg.flim_binwidth} ps = {cfg.flim_n_bins * cfg.flim_binwidth} ps time range")
                        
                        flim_native = NativeFlimWrapper(
                            tagger,  # ⭐ Use raw tagger, not sync.getTagger()
                            laser_channel=cfg.flim_laser_channel,
                            detector_channel=cfg.flim_detector_channel,
                            pixel_begin_channel=cfg.marker_channel,
                            nx_pixels=cfg.nx,
                            ny_pixels=cfg.ny,
                            n_bins=cfg.flim_n_bins,
                            binwidth=cfg.flim_binwidth,
                        )
                        print(f"[FLIM] ✓ Native Flim initialized successfully")
                    except Exception as e:
                        self.error.emit(f"Failed to initialize native FLIM: {e}")
                        import traceback
                        print(f"[ERROR FLIM INIT] {traceback.format_exc()}")
                        break
                else:
                    # Backward compatibility: use custom PulsedLaserFLIM (global histogram)
                    flim = PulsedLaserFLIM(
                        sync.getTagger(),
                        click_channel=cfg.flim_detector_channel,
                        laser_channel=cfg.flim_laser_channel,
                        laser_frequency=cfg.flim_laser_frequency,
                        n_hist_bins=cfg.flim_n_bins,
                        begin_channel=cfg.marker_channel,
                        end_channel=None,
                        exp_start=cfg.flim_exp_start,
                        exp_stop=cfg.flim_exp_stop,
                        use_custom_bins=cfg.flim_use_custom_bins,
                    )
            
            # Start FLIM measurement if initialized
            if flim_native is not None:
                print(f"[FLIM] Starting native Flim measurement...")
                flim_native.clear()  # Clear any residual data before starting
                flim_native.start()
                print(f"[FLIM] ✓ Flim measurement started")
            elif flim is not None:
                flim.start()
            
            sync.start()
            time.sleep(0.05)

            # ---- Partial image buffer -------------------------------------------
            n_ch = 16
            img_partial = np.zeros((n_ch, cfg.ny, cfg.nx), dtype=np.float64)

            # ---- Scan loop -------------------------------------------------------
            current_row = -1
            for k in range(n_pixels):
                if self._abort:
                    break

                ljm.eWriteName(handle, cfg.tdac_x, float(x_vals[k]))
                ljm.eWriteName(handle, cfg.tdac_y, float(y_vals[k]))

                if cfg.settle_s > 0:
                    time.sleep(cfg.settle_s)

                # marker pulse
                ljm.eWriteName(handle, cfg.marker_dio, 1)
                time.sleep(cfg.marker_pulse_s)
                ljm.eWriteName(handle, cfg.marker_dio, 0)

                # dwell
                time.sleep(cfg.dwell_s)

                self.progress.emit(k + 1, n_pixels)

                # Detect row boundary → emit live image
                iy, ix = ij_list[k]
                if iy != current_row:
                    if current_row >= 0:
                        # Read intermediate data for live display
                        try:
                            data_tmp = cbm.getData()
                            used = min(k + 1, data_tmp.shape[1])
                            img_partial = reshape_counts_to_image(
                                data_tmp[:, :used], ij_list[:used], cfg.ny, cfg.nx,
                            )
                            self.row_done.emit(current_row, img_partial.copy())
                        except Exception:
                            pass
                    current_row = iy

            # ---- Final marker to close last bin ---------------------------------
            if not self._abort:
                ljm.eWriteName(handle, cfg.marker_dio, 1)
                time.sleep(cfg.marker_pulse_s)
                ljm.eWriteName(handle, cfg.marker_dio, 0)
                time.sleep(0.01)

            # ---- Stop & read data -----------------------------------------------
            sync.stop()
            
            # Stop FLIM measurement to finalize data
            if flim_native is not None:
                print(f"[FLIM] Stopping FLIM measurement...")
                flim_native.stop()
                print(f"[FLIM] ✓ FLIM stopped")
                
                # Check measurement status before reading
                try:
                    current_raw = flim_native.flim.getCurrentFrame()
                    ready_raw = flim_native.flim.getReadyFrame()
                    summed_raw = flim_native.flim.getSummedFrames()
                except Exception as e:
                    pass

            data = cbm.getData()
            idx = cbm.getIndex()
            binw = cbm.getBinWidths()

            data_pix = data[:, :n_pixels]
            idx_pix = idx[:n_pixels]
            binw_pix = binw[:n_pixels]

            img_counts = reshape_counts_to_image(data_pix, ij_list, cfg.ny, cfg.nx)
            
            # Collect FLIM data if enabled
            flim_histogram = None
            flim_bin_edges = None
            flim_stats = None
            flim_lifetime_map = None
            flim_intensity_map = None
            frame_3d = None  # ⭐ Per-pixel histogram data
            
            if flim_native is not None:
                # ⭐ Native per-pixel FLIM
                try:
                    frame_3d = flim_native.get_summed_frames_3d()
                    
                    flim_lifetime_map, flim_intensity_map = flim_native.extract_per_pixel_lifetimes(
                        frame_3d=frame_3d, bidirectional=cfg.bidirectional,
                        hardware_delay_ps=cfg.flim_hardware_delay_ps if cfg.flim_hardware_delay_ps > 0 else None,
                        fit_end_ps=cfg.flim_fit_end_ps if cfg.flim_fit_end_ps > 0 else None
                    )
                    flim_bin_edges = flim_native.get_bin_edges_ps()
                    # Total photons from intensity map (sum of all pixels)
                    total_photons = int(np.sum(flim_intensity_map))
                    flim_stats = {
                        'total_photons': total_photons,
                        'mean_photons_per_pixel': float(np.mean(flim_intensity_map)),
                        'max_photons_in_pixel': int(np.max(flim_intensity_map)),
                    }
                except Exception as e:
                    self.error.emit(f"Failed to extract FLIM data: {e}")
                    import traceback
                    print(f"[ERROR FLIM] {traceback.format_exc()}")
            elif flim is not None:
                # Backward compatibility: custom global FLIM histogram
                flim_histogram = flim.get_histogram()
                flim_bin_edges = flim.get_bin_edges()
                flim_stats = flim.get_statistics()

            # ---- Save ------------------------------------------------------------
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = f"scan_{cfg.nx}x{cfg.ny}_{ts}"
            save_dir = Path(cfg.save_dir)
            save_dir.mkdir(parents=True, exist_ok=True)

            npz_path = str(save_dir / (base + ".npz"))
            meta_path = str(save_dir / (base + ".json"))

            meta = {
                "NX": cfg.nx, "NY": cfg.ny,
                "DWELL_S": cfg.dwell_s, "SETTLE_S": cfg.settle_s,
                "X_VMIN": cfg.x_vmin, "X_VMAX": cfg.x_vmax,
                "Y_VMIN": cfg.y_vmin, "Y_VMAX": cfg.y_vmax,
                "BIDIRECTIONAL": cfg.bidirectional,
                "TDAC_X": cfg.tdac_x, "TDAC_Y": cfg.tdac_y,
                "MARKER_DIO": cfg.marker_dio,
                "MARKER_PULSE_S": cfg.marker_pulse_s,
                "CLICK_CHANNELS": cfg.click_channels,
                "MARKER_CHANNEL": cfg.marker_channel,
                "timestamp": datetime.now().isoformat(),
                "continuous": cfg.continuous,
            }
            
            # Add FLIM metadata if enabled
            if cfg.flim_enabled:
                meta.update({
                    "FLIM_ENABLED": True,
                    "FLIM_USE_NATIVE": cfg.flim_use_native,
                    "FLIM_LASER_CHANNEL": cfg.flim_laser_channel,
                    "FLIM_DETECTOR_CHANNEL": cfg.flim_detector_channel,
                    "FLIM_USE_CONDITIONAL_FILTER": cfg.flim_use_conditional_filter,
                    "FLIM_USE_REFERENCE_CLOCK": cfg.flim_use_reference_clock,
                })
                
                if cfg.flim_use_native:
                    # Per-pixel FLIM metadata
                    meta.update({
                        "FLIM_N_BINS": cfg.flim_n_bins,
                        "FLIM_BINWIDTH_PS": cfg.flim_binwidth,
                    })
                    if flim_stats:
                        meta.update({
                            "FLIM_TOTAL_PHOTONS": int(flim_stats['total_photons']),
                            "FLIM_MEAN_PHOTONS_PER_PIXEL": float(flim_stats['mean_photons_per_pixel']),
                            "FLIM_MAX_PHOTONS_IN_PIXEL": int(flim_stats['max_photons_in_pixel']),
                        })
                else:
                    # Legacy custom FLIM metadata
                    meta.update({
                        "FLIM_LASER_FREQUENCY": cfg.flim_laser_frequency,
                        "FLIM_N_HIST_BINS": cfg.flim_n_bins,
                        "FLIM_EXP_START": cfg.flim_exp_start,
                        "FLIM_EXP_STOP": cfg.flim_exp_stop,
                    })
                    if flim_stats:
                        meta.update({
                            "FLIM_TOTAL_PHOTONS": int(flim_stats['total_photons']),
                            "FLIM_TOTAL_LASER_PULSES": int(flim_stats.get('total_laser_pulses', 0)),
                            "FLIM_DETECTION_EFFICIENCY_PCT": float(flim_stats.get('detection_efficiency', 0)),
                        })

            # Prepare save data
            save_arrays = {
                'counts_ch_y_x': img_counts,
                'counts_ch_bin': data_pix,
                'bin_index_ps': idx_pix,
                'bin_width_ps': binw_pix,
                'x_axis_volts': x_axis,
                'y_axis_volts': y_axis,
            }
            
            # Add FLIM data if available
            if flim_lifetime_map is not None:
                # Per-pixel FLIM data from native Flim
                save_arrays['flim_lifetime_map_ps'] = flim_lifetime_map
                save_arrays['flim_intensity_map'] = flim_intensity_map
                save_arrays['flim_frame_3d'] = frame_3d        # ⭐ Full per-pixel histograms (ny, nx, nbins)
                save_arrays['flim_bin_edges_ps'] = flim_bin_edges
            elif flim_histogram is not None:
                # Legacy: global FLIM histogram
                save_arrays['flim_histogram'] = flim_histogram
                save_arrays['flim_bin_edges_ps'] = flim_bin_edges

            np.savez_compressed(npz_path, **save_arrays)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            # ---- Signal completion of this frame --------------------------------
            scan_result = {
                "image": img_counts,
                "data_pix": data_pix,
                "idx_pix": idx_pix,
                "binw_pix": binw_pix,
                "x_axis": x_axis,
                "y_axis": y_axis,
                "meta": meta,
                "npz_path": npz_path,
                "meta_path": meta_path,
            }
            
            # Add FLIM data to result if available
            if flim_lifetime_map is not None:
                # Per-pixel FLIM from native Flim
                scan_result['flim_lifetime_map'] = flim_lifetime_map
                scan_result['flim_intensity_map'] = flim_intensity_map
                scan_result['flim_stats'] = flim_stats
            elif flim_histogram is not None:
                # Legacy: global FLIM histogram
                scan_result['flim_histogram'] = flim_histogram
                scan_result['flim_bin_edges'] = flim_bin_edges
                scan_result['flim_stats'] = flim_stats
            
            self.finished_scan.emit(scan_result)

            if not cfg.continuous:
                break

        # ---- Cleanup ---------------------------------------------------------
        ljm.close(handle)
