"""
Scan all Time Tagger input channels to find actual signal locations.
"""
import numpy as np
import time
try:
    import TimeTagger
except ImportError:
    print("Error: TimeTagger SDK not installed.")
    exit(1)

def scan_all_channels():
    """Scan channels 1-18 to find where signals are."""
    
    print("=" * 70)
    print("CHANNEL SCAN - Find Actual Signal Locations")
    print("=" * 70)
    
    try:
        tagger = TimeTagger.createTimeTagger()
        print(f"\nConnected to: {tagger.getModel()} ({tagger.getSerial()})\n")
    except Exception as e:
        print(f"Error connecting: {e}")
        return
    
    # Test each channel
    print(f"{'Channel':>8} | {'Events/10ms':>15} | Notes")
    print("-" * 55)
    
    for ch in range(1, 5):  # Time Tagger X has 4 input channels
        # Create counter for this channel
        counter = TimeTagger.Counter(tagger, channels=[ch])
        counter.start()
        time.sleep(0.010)  # 10ms
        counts = counter.getData()
        counter.stop()
        
        total = np.sum(counts)
        
        # Mark likely signals
        notes = ""
        if total > 100:
            notes = "*** STRONG SIGNAL ***"
        elif total > 10:
            notes = "** signal detected **"
        
        print(f"{ch:>8} | {total:>15,} | {notes}")
    
    print("\n" + "=" * 70)
    print("INTERPRETATION:")
    print("  - Find channels with > 100 events/10ms (likely your signals)")
    print("  - Report these channel numbers to update your configuration")
    print("=" * 70)

if __name__ == "__main__":
    scan_all_channels()
