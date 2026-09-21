"""
flim_engine.py — FLIM (Fluorescence Lifetime Imaging Microscopy) support with pulsed laser.

Provides measurements and utilities for fluorescence lifetime analysis using pulsed excitation.
Supports both:
  1. Native Time Tagger Flim class (per-pixel histograms) — RECOMMENDED
  2. Custom PulsedLaserFLIM class (global histogram)

Includes automatic hardware configuration, live frame reading with intensity normalization,
and per-pixel lifetime extraction.
"""

import TimeTagger
import numpy as np
import numba
import numba.typed
from typing import Optional, Tuple


class PulsedLaserFLIM(TimeTagger.CustomMeasurement):
    """
    FLIM measurement synchronized to a pulsed laser source.
    
    Records a histogram of photon arrival times relative to the laser pulse,
    binned with edges aligned to integer multiples of the laser period.
    This technique reveals fluorescence lifetime information.
    
    Parameters
    ----------
    tagger : TimeTagger
        Time Tagger instance
    click_channel : int
        Detector channel for fluorescence photons
    laser_channel : int
        Laser trigger or reference channel
    laser_frequency : float
        Laser repetition rate in Hz (typically 80-100 MHz)
    n_hist_bins : int
        Number of histogram bins spanning one laser period
    begin_channel : int, optional
        Marker channel to define region of interest (e.g., raster position)
    end_channel : int, optional
        Marker channel to end region of interest
    exp_start : float, optional
        Logarithmic histogram start exponent (power of 10, in picoseconds)
    exp_stop : float, optional
        Logarithmic histogram stop exponent (power of 10, in picoseconds)
    use_custom_bins : bool
        If True, use period-aligned custom bins. If False, use logarithmic bins.
    binwidth : float, optional
        If provided, use linear bins of this width (in picoseconds).
        Overrides use_custom_bins and exp_start/exp_stop.
        Example: binwidth=200 with n_hist_bins=256 gives 0–51,200 ps range.
    """
    
    def __init__(self, tagger, click_channel, laser_channel, laser_frequency,
                 n_hist_bins=256, begin_channel=None, end_channel=None,
                 exp_start=0, exp_stop=5, use_custom_bins=True,
                 binwidth=None):
        TimeTagger.CustomMeasurement.__init__(self, tagger)
        
        self.click_channel = np.int16(click_channel)
        self.laser_channel = np.int16(laser_channel)
        self.laser_frequency = float(laser_frequency)
        self.laser_period_ps = 1.0 / laser_frequency * 1e12  # Convert to picoseconds
        self.n_hist_bins = int(n_hist_bins)
        self.use_custom_bins = use_custom_bins
        
        # Register channels
        self.register_channel(channel=self.click_channel)
        self.register_channel(channel=self.laser_channel)
        
        # Optional marker channels for ROI
        self.has_begin = begin_channel is not None
        self.begin_channel = np.int16(begin_channel if begin_channel is not None else 0)
        if self.has_begin:
            self.register_channel(channel=self.begin_channel)
        
        self.has_end = end_channel is not None
        self.end_channel = np.int16(end_channel if end_channel is not None else 0)
        if self.has_end:
            self.register_channel(channel=self.end_channel)
        
        # Generate bin edges
        if binwidth is not None:
            # Linear bins: 0, binwidth, 2*binwidth, ... n_hist_bins*binwidth
            self.bin_edges = np.arange(n_hist_bins + 1, dtype=np.float64) * float(binwidth)
        elif use_custom_bins:
            self.bin_edges = self._generate_period_aligned_bins(exp_start, exp_stop)
        else:
            self.bin_edges = np.logspace(exp_start, exp_stop, n_hist_bins + 1, base=10.0, dtype=np.float64)
        
        self.clear_impl()
        self.finalize_init()
    
    def _generate_period_aligned_bins(self, exp_start, exp_stop):
        """Generate histogram bins aligned to laser periods."""
        # Create logarithmic grid
        log_bin_edges = np.logspace(exp_start, exp_stop, num=self.n_hist_bins + 1, 
                                     base=10.0, dtype=np.float64)
        # Round to nearest integer multiple of laser period
        custom_bin_edges = np.round(log_bin_edges / self.laser_period_ps) * self.laser_period_ps
        # Remove duplicates while preserving order
        custom_bin_edges = np.unique(custom_bin_edges)
        return custom_bin_edges
    
    def __del__(self):
        self.stop()
    
    def on_start(self):
        pass
    
    def on_stop(self):
        pass
    
    def clear_impl(self):
        self.histogram = np.zeros(len(self.bin_edges) - 1, dtype=np.uint64)
        # If no begin_channel marker, start with ROI active (free-running mode)
        self.roi_active = not self.has_begin
        self.last_laser_time = 0
        self.total_photons = 0
        self.total_laser_pulses = 0
    
    def get_histogram(self):
        """Get current histogram data (thread-safe)."""
        with self.mutex:
            return self.histogram.copy()
    
    def get_bin_edges(self):
        """Get histogram bin edges (thread-safe)."""
        with self.mutex:
            return self.bin_edges.copy()
    
    def get_statistics(self):
        """Get acquisition statistics (thread-safe)."""
        with self.mutex:
            return {
                'total_photons': self.total_photons,
                'total_laser_pulses': self.total_laser_pulses,
                'detection_efficiency': self.total_photons / max(self.total_laser_pulses, 1) * 100,
            }
    
    @staticmethod
    @numba.jit(nopython=True, nogil=True)
    def fast_process(tags, histogram, bin_edges, click_channel, laser_channel,
                     has_begin, begin_channel, has_end, end_channel,
                     roi_active, last_laser_time, total_photons, total_laser_pulses):
        """
        Fast Numba-compiled tag processing for FLIM histogramming.
        """
        n_bins = len(histogram)
        
        for tag in tags:
            ttype = tag["type"]
            
            if ttype == TimeTagger.TagType["TimeTag"]:
                ch = tag["channel"]
                tm = tag["time"]
                
                # Handle marker channels for ROI
                if has_begin and ch == begin_channel:
                    roi_active = True
                    last_laser_time = tm
                
                if has_end and ch == end_channel:
                    roi_active = False
                
                # Track laser pulses
                if ch == laser_channel:
                    last_laser_time = tm
                    total_laser_pulses += 1
                
                # Record photon in histogram relative to last laser pulse
                if ch == click_channel and roi_active:
                    dt = tm - last_laser_time  # Time since last laser pulse
                    
                    # Find appropriate bin
                    for i in range(n_bins):
                        if bin_edges[i] <= dt < bin_edges[i + 1]:
                            histogram[i] += 1
                            total_photons += 1
                            break
        
        return roi_active, last_laser_time, total_photons, total_laser_pulses
    
    def process(self, incoming_tags, begin_time, end_time):
        """Process incoming timestamp tags."""
        (self.roi_active, self.last_laser_time, 
         self.total_photons, self.total_laser_pulses) = self.fast_process(
            incoming_tags,
            self.histogram,
            self.bin_edges,
            self.click_channel,
            self.laser_channel,
            self.has_begin,
            self.begin_channel,
            self.has_end,
            self.end_channel,
            self.roi_active,
            self.last_laser_time,
            self.total_photons,
            self.total_laser_pulses,
        )


class PulsedLaserConfig:
    """Configuration parameters for pulsed laser FLIM acquisition."""
    
    def __init__(self):
        self.enabled = False
        self.laser_channel = 3  # Laser trigger channel (adjust to your setup)
        self.laser_frequency = 80e6  # 80 MHz (common pulsed laser frequency)
        self.detector_channel = 2  # Detector channel
        self.use_conditional_filter = True
        self.use_reference_clock = False
        self.use_custom_bins = True
        self.n_hist_bins = 256
        self.exp_start = 0  # 10^0 ps = 1 ps (minimum bin)
        self.exp_stop = 4  # 10^4 ps = 10 ns (typical FLIM range)
    
    def get_laser_period_ps(self):
        """Get laser period in picoseconds."""
        return 1.0 / self.laser_frequency * 1e12
    
    def to_dict(self):
        """Convert to dictionary for serialization."""
        return {
            'enabled': self.enabled,
            'laser_channel': self.laser_channel,
            'laser_frequency': self.laser_frequency,
            'detector_channel': self.detector_channel,
            'use_conditional_filter': self.use_conditional_filter,
            'use_reference_clock': self.use_reference_clock,
            'use_custom_bins': self.use_custom_bins,
            'n_hist_bins': self.n_hist_bins,
            'exp_start': self.exp_start,
            'exp_stop': self.exp_stop,
        }
    
    @classmethod
    def from_dict(cls, data):
        """Create from dictionary."""
        cfg = cls()
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg


def configure_pulsed_laser_hardware(tagger, laser_cfg):
    """
    Configure TimeTagger hardware for optimal pulsed laser FLIM.
    
    Based on Swabian Instruments recommendations:
    - setDelayHardware(): Minimize delays to stay within histogram window
    - setConditionalFilter(): Reduce data bandwidth (disabled for native Flim)
    - setReferenceClock(): Lock to laser for phase stability (optional)
    
    Parameters
    ----------
    tagger : TimeTagger
        Time Tagger instance
    laser_cfg : PulsedLaserConfig
        Pulsed laser configuration
    """
    if not laser_cfg.enabled:
        return
    
    laser_period_ps = laser_cfg.get_laser_period_ps()
    
    # Hardware delay: Keep delays minimal for FLIM
    # We want photons to fall within the histogram window (0 to n_bins*binwidth)
    # Setting zero delays ensures events arrive with native timing
    tagger.setDelayHardware(laser_cfg.laser_channel, 0)     # Laser as reference (no delay)
    tagger.setDelayHardware(laser_cfg.detector_channel, 0)  # Detector at natural timing
    
    # Conditional filtering: transmit laser pulse for each detector event
    # This reduces data rate from ~100 MHz to ~1-10 MHz
    # ⚠️ NOTE: Use only with custom FLIM, NOT with native Flim!
    # Native Flim needs full laser pulse train to establish histogram timing
    if laser_cfg.use_conditional_filter:
        tagger.setConditionalFilter(
            trigger=[laser_cfg.detector_channel],
            filtered=[laser_cfg.laser_channel]
        )
        # Push laser back in software to undo hardware delay for analysis
        tagger.setDelaySoftware(laser_cfg.laser_channel, 0)
    
    # Optional: Lock Time Tagger clock to laser for extended measurements
    if laser_cfg.use_reference_clock:
        try:
            # Requires laser providing sync output (~100k to 1MHz square wave)
            tagger.setReferenceClock(laser_cfg.laser_channel)
        except Exception as e:
            print(f"Warning: Could not set reference clock: {e}")


class NativeFlimWrapper:
    """
    Wrapper for Time Tagger's native Flim measurement class.
    
    Provides per-pixel fluorescence lifetime histograms with automatic
    intensity normalization and live frame reading.
    
    This is the RECOMMENDED approach for production use. The native Flim class:
    - Accumulates per-pixel lifetime histograms
    - Provides normalized intensity frames
    - Optimized performance (compiled C++)
    - Ready/Current/Summed frame access
    
    Parameters
    ----------
    tagger : TimeTagger
        Time Tagger instance
    laser_channel : int
        Laser pulse trigger channel
    detector_channel : int
        Photon detector channel
    pixel_begin_channel : int
        Channel signaling start of each pixel (marker)
    nx_pixels : int
        Number of pixels along x-axis
    ny_pixels : int
        Number of pixels along y-axis
    n_bins : int, optional
        Number of histogram bins per pixel (default: 256)
    binwidth : int, optional
        Width of each bin in picoseconds (default: 50 ps)
    """
    
    def __init__(self, tagger, laser_channel, detector_channel, pixel_begin_channel,
                 nx_pixels, ny_pixels, n_bins=256, binwidth=50):
        self.tagger = tagger
        self.laser_channel = int(laser_channel)
        self.detector_channel = int(detector_channel)
        self.pixel_begin_channel = int(pixel_begin_channel)
        self.nx_pixels = int(nx_pixels)
        self.ny_pixels = int(ny_pixels)
        self.n_pixels = nx_pixels * ny_pixels
        self.n_bins = int(n_bins)
        self.binwidth = int(binwidth)
        
        # Calculate time range in picoseconds
        self.time_range_ps = n_bins * binwidth
        
        # Create native Flim measurement
        try:
            self.flim = TimeTagger.Flim(
                tagger,
                start_channel=self.laser_channel,
                click_channel=self.detector_channel,
                pixel_begin_channel=self.pixel_begin_channel,
                n_pixels=self.n_pixels,
                n_bins=self.n_bins,
                binwidth=self.binwidth
            )
        except Exception as e:
            raise RuntimeError(f"Failed to create native Flim measurement: {e}")
    
    def start(self):
        """Start the measurement."""
        self.flim.start()
    
    def stop(self):
        """Stop the measurement."""
        self.flim.stop()
    
    def clear(self):
        """Clear accumulated data."""
        self.flim.clear()
    
    def get_current_frame_3d(self) -> np.ndarray:
        """
        Get current frame as 3D array (x, y, time_bins).
        
        Returns
        -------
        ndarray
            Shape (ny_pixels, nx_pixels, n_bins) with histogram counts
        """
        frame = self.flim.getCurrentFrame()
        # Reshape from (n_pixels, n_bins) to (ny, nx, n_bins)
        return frame.reshape((self.ny_pixels, self.nx_pixels, self.n_bins))
    
    def get_ready_frame_3d(self) -> np.ndarray:
        """
        Get ready frame as 3D array (x, y, time_bins).
        
        Returns
        -------
        ndarray
            Shape (ny_pixels, nx_pixels, n_bins) with histogram counts
        """
        frame = self.flim.getReadyFrame()
        return frame.reshape((self.ny_pixels, self.nx_pixels, self.n_bins))
    
    def get_summed_frames_3d(self) -> np.ndarray:
        """
        Get summed frames as 3D array (x, y, time_bins).
        
        Returns
        -------
        ndarray
            Shape (ny_pixels, nx_pixels, n_bins) with cumulative histogram counts
        """
        frame = self.flim.getSummedFrames()
        return frame.reshape((self.ny_pixels, self.nx_pixels, self.n_bins))
    
    def get_current_intensity_frame(self) -> np.ndarray:
        """
        Get current intensity frame (normalized by integration time).
        
        Returns
        -------
        ndarray
            Shape (ny_pixels, nx_pixels) with normalized intensities
        """
        intensity = self.flim.getCurrentFrameIntensity()
        return intensity.reshape((self.ny_pixels, self.nx_pixels))
    
    def get_ready_intensity_frame(self) -> np.ndarray:
        """
        Get ready intensity frame (normalized by integration time).
        
        Returns
        -------
        ndarray
            Shape (ny_pixels, nx_pixels) with normalized intensities
        """
        intensity = self.flim.getReadyFrameIntensity()
        return intensity.reshape((self.ny_pixels, self.nx_pixels))
    
    def get_summed_intensities(self) -> np.ndarray:
        """
        Get summed intensities (normalized by integration time).
        
        Returns
        -------
        ndarray
            Shape (ny_pixels, nx_pixels) with cumulative normalized intensities
        """
        intensity = self.flim.getSummedFramesIntensity()
        return intensity.reshape((self.ny_pixels, self.nx_pixels))
    
    def get_bin_edges_ps(self) -> np.ndarray:
        """
        Get histogram bin edges in picoseconds.
        
        Returns
        -------
        ndarray
            Bin edges from 0 to n_bins*binwidth
        """
        return np.arange(self.n_bins + 1) * self.binwidth
    
    def extract_per_pixel_lifetimes(self, frame_3d: Optional[np.ndarray] = None,
                                   bidirectional: bool = False,
                                   min_photons: int = 5,
                                   hardware_delay_ps: float = None,
                                   fit_end_ps: float = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Extract fluorescence lifetime (τ) for each pixel via MLE.

        Uses τ_MLE = <t> − t_peak on the decay tail, with background
        subtraction estimated from the pre-peak region.

        Parameters
        ----------
        frame_3d : ndarray, optional
            3D frame array (ny, nx, n_bins). If None, uses getCurrentFrame.
        bidirectional : bool
            If True, flips odd rows to correct bidirectional scan order.
        min_photons : int
            Minimum photon count in the decay tail for a pixel to get a
            lifetime value; below this the pixel is set to 0.
        hardware_delay_ps : float, optional
            Known hardware delay in picoseconds. If provided, use this as the
            peak position instead of auto-detecting from the histogram.
        fit_end_ps : float, optional
            Truncate the fitting window at this time (ps). Useful to exclude
            electronic reflection artifacts appearing later in the window.
            If None or <= hardware_delay_ps, the full tail is used.

        Returns
        -------
        lifetimes : ndarray
            Shape (ny, nx) with τ in picoseconds (0 where insufficient data).
        intensities : ndarray
            Shape (ny, nx) with total photon counts per pixel.
        """
        if frame_3d is None:
            frame_3d = self.get_current_frame_3d()

        if bidirectional:
            frame_3d = frame_3d.copy()
            frame_3d[1::2, :, :] = frame_3d[1::2, ::-1, :]

        bin_centers = np.arange(self.n_bins) * self.binwidth + self.binwidth / 2

        # Determine the peak position (start of exponential decay)
        if hardware_delay_ps is not None:
            peak_idx = int(np.argmin(np.abs(bin_centers - hardware_delay_ps)))
        else:
            global_hist = frame_3d.sum(axis=(0, 1))
            peak_idx = int(np.argmax(global_hist))

        t_peak = bin_centers[peak_idx]

        # Determine end index (window truncation to exclude reflection artifacts)
        if fit_end_ps is not None and fit_end_ps > t_peak:
            end_idx = int(np.argmin(np.abs(bin_centers - fit_end_ps))) + 1
            end_idx = min(end_idx, self.n_bins)
        else:
            end_idx = self.n_bins

        x_tail = bin_centers[peak_idx:end_idx]

        # Background estimate from pre-peak region of the global histogram
        global_hist = frame_3d.sum(axis=(0, 1)).astype(np.float64)
        if peak_idx >= 4:
            bg_per_bin_global = float(np.mean(global_hist[:max(2, peak_idx // 2)]))
        else:
            bg_per_bin_global = 0.0
        total_global = float(np.sum(global_hist))
        bg_scale = bg_per_bin_global / total_global if total_global > 0 else 0.0

        lifetimes = np.zeros((self.ny_pixels, self.nx_pixels), dtype=np.float32)
        intensities = np.zeros((self.ny_pixels, self.nx_pixels), dtype=np.uint32)

        for iy in range(self.ny_pixels):
            for ix in range(self.nx_pixels):
                hist = frame_3d[iy, ix, :]
                total_pixel = int(np.sum(hist))
                intensities[iy, ix] = total_pixel

                y_tail = hist[peak_idx:end_idx].astype(np.float64)
                n_tail = np.sum(y_tail)
                if n_tail < min_photons:
                    continue

                # Background-corrected MLE: τ = <t> − t_peak
                bg_per_bin = bg_scale * total_pixel
                tail_corr = np.maximum(y_tail - bg_per_bin, 0)
                n_corr = np.sum(tail_corr)
                if n_corr >= min_photons:
                    tau_mle = np.dot(tail_corr, x_tail) / n_corr - t_peak
                    if tau_mle > 0:
                        lifetimes[iy, ix] = tau_mle

        return lifetimes, intensities
    
    def get_pixel_histogram(self, x: int, y: int, 
                           frame_3d: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Get lifetime histogram for a single pixel.
        
        Parameters
        ----------
        x : int
            X pixel coordinate
        y : int
            Y pixel coordinate
        frame_3d : ndarray, optional
            3D frame array. If None, uses getCurrentFrame
        
        Returns
        -------
        ndarray
            Histogram counts for that pixel
        """
        if frame_3d is None:
            frame_3d = self.get_current_frame_3d()
        
        return frame_3d[y, x, :]


class FlimConfig:
    """Configuration for native Time Tagger Flim measurement."""
    
    def __init__(self):
        self.enabled = False
        self.laser_channel = 3
        self.detector_channel = 2
        self.pixel_begin_channel = 4  # Same as marker_channel in scanner
        self.nx_pixels = 100
        self.ny_pixels = 100
        self.n_bins = 256
        self.binwidth = 50  # picoseconds
        self.use_native_flim = True  # Use native Flim class (vs custom)
    
    def to_dict(self):
        """Convert to dictionary for serialization."""
        return {
            'enabled': self.enabled,
            'laser_channel': self.laser_channel,
            'detector_channel': self.detector_channel,
            'pixel_begin_channel': self.pixel_begin_channel,
            'nx_pixels': self.nx_pixels,
            'ny_pixels': self.ny_pixels,
            'n_bins': self.n_bins,
            'binwidth': self.binwidth,
            'use_native_flim': self.use_native_flim,
        }
    
    @classmethod
    def from_dict(cls, data):
        """Create from dictionary."""
        cfg = cls()
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg

