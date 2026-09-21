"""
Comprehensive diagnostic for FLIM channel issues.
Tests what data is actually being collected by the native Flim measurement.
"""
import numpy as np
import time
try:
    import TimeTagger
except ImportError:
    print("Error: TimeTagger SDK not installed.")
    exit(1)

def diagnose_flim_channels():
    """Check if native Flim is collecting any data."""
    
    print("=" * 70)
    print("FLIM CHANNEL DIAGNOSTIC")
    print("=" * 70)
    
    try:
        tagger = TimeTagger.createTimeTagger()
        print(f"\n✓ Connected to: {tagger.getModel()} ({tagger.getSerial()})")
    except Exception as e:
        print(f"✗ Error connecting to Time Tagger: {e}")
        return
    
    # Configuration from your setup
    LASER_CHANNEL = 1
    DETECTOR_CHANNEL = 2  
    PIXEL_MARKER_CHANNEL = 4
    DWELL_MS = 5000  # 5 seconds to collect data
    
    print(f"\nConfiguration:")
    print(f"  - Laser (ch{LASER_CHANNEL}): Pulsed laser trigger")
    print(f"  - Detector (ch{DETECTOR_CHANNEL}): Fluorescence photons")
    print(f"  - Pixel marker (ch{PIXEL_MARKER_CHANNEL}): Pixel synchronization")
    print(f"  - Test duration: {DWELL_MS}ms")
    
    print(f"\nTesting NATIVE FLIM measurement...")
    
    # Create Flim measurement directly with raw tagger
    # ⚠️ IMPORTANT: Do NOT use SynchronizedMeasurements context
    # Native Flim measurements don't work well within sync contexts
    
    try:
        flim_test = TimeTagger.Flim(
            tagger,  # ⭐ Use raw tagger, not sync.getTagger()
            start_channel=LASER_CHANNEL,
            click_channel=DETECTOR_CHANNEL,
            pixel_begin_channel=PIXEL_MARKER_CHANNEL,
            n_pixels=2500,  # 50x50
            n_bins=256,
            binwidth=200
        )
        print(f"  ✓ Flim measurement created")
        print(f"    - start_channel (laser): {LASER_CHANNEL}")
        print(f"    - click_channel (detector): {DETECTOR_CHANNEL}")
        print(f"    - pixel_begin_channel (marker): {PIXEL_MARKER_CHANNEL}")
        print(f"    - n_pixels: 2500 (50x50)")
        print(f"    - n_bins: 256")
        print(f"    - binwidth: 200 ps")
    except Exception as e:
        print(f"  ✗ Failed to create Flim: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Start measurement (without SynchronizedMeasurements)
    try:
        print(f"\n  Starting measurements...")
        flim_test.clear()
        flim_test.start()
        print(f"  ✓ Measurements started")
    except Exception as e:
        print(f"  ✗ Failed to start: {e}")
        import traceback
        traceback.print_exc()
        return
    
    time.sleep(DWELL_MS / 1000)
    
    print(f"  Stopping measurements...")
    flim_test.stop()
    print(f"  ✓ Measurements stopped")
    
    # Read data
    try:
        print(f"\nReading collected data...")
        current = flim_test.getCurrentFrame()
        ready = flim_test.getReadyFrame()
        summed = flim_test.getSummedFrames()
        
        current_sum = np.sum(current)
        ready_sum = np.sum(ready)
        summed_sum = np.sum(summed)
        
        current_nz = np.count_nonzero(current)
        ready_nz = np.count_nonzero(ready)
        summed_nz = np.count_nonzero(summed)
        
        print(f"  Current frame: {current_sum:,} photons ({current_nz:,} non-zero bins)")
        print(f"  Ready frame:   {ready_sum:,} photons ({ready_nz:,} non-zero bins)")
        print(f"  Summed frame:  {summed_sum:,} photons ({summed_nz:,} non-zero bins)")
        
        if summed_sum == 0:
            print(f"\n  ✗ FLIM collected NO DATA!")
            print(f"\n  This means:")
            print(f"    1. Laser pulses NOT detected on channel {LASER_CHANNEL}, OR")
            print(f"    2. Detector events NOT detected on channel {DETECTOR_CHANNEL}, OR")
            print(f"    3. Pixel markers NOT detected on channel {PIXEL_MARKER_CHANNEL}, OR")
            print(f"    4. The channels are not properly synchronized/connected")
            print(f"\n  TROUBLESHOOTING:")
            print(f"    - Check that your laser trigger is on channel {LASER_CHANNEL}")
            print(f"    - Check that your photon detector output is on channel {DETECTOR_CHANNEL}")
            print(f"    - Check that pixel markers are on channel {PIXEL_MARKER_CHANNEL}")
            print(f"    - Verify all signals are connected to the Time Tagger")
        else:
            print(f"\n  ✓ FLIM collected data successfully!")
            # Show sample histogram
            summed_3d = summed.reshape((50, 50, 256))
            sample = summed_3d[0, 0, :]
            nonzero_idx = np.where(sample > 0)[0]
            if len(nonzero_idx) > 0:
                print(f"\n  Sample pixel [0,0] histogram:")
                print(f"    First non-zero bin: {nonzero_idx[0]} ({nonzero_idx[0]*200} ps)")
                print(f"    Last non-zero bin:  {nonzero_idx[-1]} ({nonzero_idx[-1]*200} ps)")
                print(f"    Total photons in pixel:      {np.sum(sample)}")
    
    except Exception as e:
        print(f"  ✗ Error reading frames: {e}")
        import traceback
        traceback.print_exc()
    
    tagger = None  # Delete reference to release Time Tagger
    print(f"\n" + "=" * 70)


if __name__ == "__main__":
    diagnose_flim_channels()
