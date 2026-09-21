import TimeTagger
import inspect

print("TimeTagger version:", TimeTagger.__version__ if hasattr(TimeTagger, "__version__") else "unknown")

# Let's inspect createTimeTaggerVirtual
print("\ncreateTimeTaggerVirtual signature:")
try:
    print(inspect.signature(TimeTagger.createTimeTaggerVirtual))
except Exception as e:
    print("Could not get signature:", e)

# Let's create virtual taggers and inspect methods
print("\nCreating virtual tagger...")
tagger = TimeTagger.createTimeTaggerVirtual()
print("Tagger type:", type(tagger))
print("Available channels by default:")
try:
    # Try calling common channel methods
    if hasattr(tagger, "getChannelList"):
        print("getChannelList:", tagger.getChannelList())
except Exception as e:
    print("Error getting channel list:", e)

# Check if we can create virtual channels or customize channel count
print("\nDoes it have createTimeTaggerVirtual with channel numbers?")
for name in dir(TimeTagger):
    if "Virtual" in name or "virtual" in name.lower():
        print(name)

try:
    # Let's see if we can add a channel or if tagger supports setting channels count
    print("Methods on tagger relating to channels:")
    for method in dir(tagger):
        if "channel" in method.lower():
            print(f" - {method}")
except Exception as e:
    print(e)

TimeTagger.freeTimeTagger(tagger)
