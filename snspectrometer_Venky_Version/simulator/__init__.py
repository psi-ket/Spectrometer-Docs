"""Simulator: synthetic TTbin datasets and a hardware-free simulated device.

The simulator feeds the *same* Swabian measurement classes as real hardware. It
uses ``createTimeTaggerVirtual()`` (a simulated tagger) together with the
library's ``Experimental`` signal generators to write genuine TTbin files, which
are then replayed as a live device or analysed offline.
"""
