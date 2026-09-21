# Limitations and assumptions

* **Library version.** Verified with the installed package 2.21.2 against the 2.22.6 manual.
  `GatedCounter` and `GatedChannels` exist only from 2.22; the adapter falls back to
  `CountBetweenMarkers` / `GatedChannel` and records which implementation ran.
* **Hardware not tested here.** No physical Time Tagger, Synchronizer or network server was
  available while building. All hardware paths are exercised through the virtual tagger and
  the mock backend; trigger level / impedance / event divider / reference clock / server
  mapping code follows the manual signatures but has not been run on hardware.
  `tests/test_integration_real.py` can be extended with hardware checks.
* **Synchronizer detection** is inferred from the channel numbering scheme
  (`TT_NUMBER*100 + INPUT`, manual 7.5); individual member serials are not exposed by the API.
* **Multi-server networks.** The library refuses unsynchronized servers; the app derives
  `SYNC_REQUIRED / REFERENCE_CLOCK_LOCKED / SYNC_ERROR` from each server's reference clock
  state and blocks cross-device measurements unless the state allows it.
* **Replay pause** re-queues the file from the paused position; a small overlap of tags
  already buffered by the library can be delivered twice (documented in the Analysis tab).
* **Replay speed** below 0.1x is not supported by the library and is clamped.
* **Simulated device** loops a finite dataset; an overflow marker is emitted at every loop
  boundary and a periodic pattern repeats every dataset duration. Per-channel delays are baked
  into the data via `mergeStreamFiles` time offsets.
* **Experimental generators** (`TimeTagger.Experimental.*`) are used only by the simulator; they
  are undocumented in the manual and may change.
* **Qt binding.** PySide6 6.10 and PyQt6 could not be loaded in this Anaconda environment
  (stale `msvcp140.dll`); the app runs on PyQt5 through qtpy. All widgets use qtpy-compatible
  APIs; PySide6 is selected automatically once it loads.
* **SVG export** uses matplotlib (re-plotting stored arrays) because `QtSvg` is unavailable in
  this environment; screen PNG export uses the on-screen widget.
* **JSI** is an application-level reconstruction from pair coincidences on a discrete
  detector grid; resolution equals detector spacing. Accidental subtraction models
  (Poisson estimate, sideband mean) are explicit and stored.
