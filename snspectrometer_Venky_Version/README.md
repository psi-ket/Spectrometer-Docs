# SNSPD Spectrometer — Swabian Time Tagger application

Scientific desktop application for an SNSPD-based spectrometer: *N* detector channels
(each mapped to a wavelength), one or more Swabian Instruments Time Taggers (USB,
Synchronizer groups, network servers) and full offline re-analysis of recorded
time-tag streams. All time tagging goes through the **official Swabian
`TimeTagger` Python API**; nothing is re-implemented or approximated.

```
GUI (qtpy: PySide6 / PyQt6 / PyQt5)
 |
Application Controller (app/controller)        Raw data (.ttbin)
 |                                                |
Measurement Manager / Groups (app/measurements)   TimeTaggerVirtual / FileReader
 |                                                |
swabian_backend (the ONLY place that imports TimeTagger)
 |
Swabian TimeTagger API  ->  hardware / network / virtual (replay, simulator)
```

## Requirements

* Python 3.11+ (developed on 3.13), NumPy, SciPy, pyqtgraph, matplotlib, psutil, qtpy
* A Qt binding: PySide6 (preferred), PyQt6 or PyQt5. The app probes them in that order
  and uses the first one whose DLLs load (see *Known environment issue* below).
* Swabian Instruments Time Tagger software with the `TimeTagger` Python package
  (verified with 2.21.2; adapter written against the 2.22.6 manual). Without the
  library the app starts in offline mode and reports it.
* For offline replay and the simulator: the free *Virtual Time Tagger* license, which
  the library acquires automatically once when hardware is attached.

## Run

```
cd snspectrometer
python -m app.main                # GUI
python -m app.main --simulator    # GUI + simulated device at startup
python -m app.main --ttbin path/to/raw.ttbin
python -m simulator --all --duration 10 --out demo_datasets   # generate synthetic datasets
python examples/headless_acquisition.py --simulator
python examples/replay_reanalysis.py demo_datasets/two_detector_pairs.ttbin
python -m pytest                  # tests (mock backend + real-library integration tests)
```

## What the application does (definition of done, mapped to code)

| Requirement | Where |
|---|---|
| Discover USB devices / network servers, serials, models, channels | `swabian_backend/devices.py`, Hardware tab |
| Trigger level (+ read-back), 50 Ω / High-Z, input delay, dead time, event divider, HW delay compensation, test signal | `swabian_backend/configuration.py`, Channels tab |
| Presets: create/save/load/duplicate/rename/delete/export/import/apply/compare with explicit device+channel mapping | `app/models/presets.py`, `app/storage/preset_store.py` |
| Detector → wavelength → role mapping, sorted wavelength axes | `app/models/channels.py` |
| Count Rate, Count Trace, Count Between Markers (`GatedCounter` on 2.22+, `CountBetweenMarkers` fallback), Histogram, Full Coincidence (n-fold, `Coincidences` virtual channels), Coincidence Matrix (`CorrelationPairs`), g²(τ) (`Correlation` + explicit normalisation), JSI (wavelength-space from pair coincidences), Histogram2D/ND, TimeDifferences(ND), raw TimeTagStream | `app/measurements/*.py` |
| Synchronized measurement groups (`SynchronizedMeasurements`), prepare/arm/start/pause/resume/stop/clear | `app/measurements/group.py`, Measurements tab |
| Raw TTbin recording (`FileWriter`, max file size, splitting, markers) | `swabian_backend/recorder.py` |
| measurement.json + result.npz per run, frozen experiment config, `getConfiguration()` snapshot, checksums | `app/measurements/group.py`, `app/models/results.py`, `app/storage/run_store.py` |
| Offline replay (`TimeTaggerVirtual`), speed presets, pause/resume/restart, evolving plots, re-analysis with new parameters, provenance | `app/analysis/replay_session.py`, Analysis tab |
| Raw stream access (`FileReader`: timestamps, channels, event types, missed events) | `app/analysis/raw_stream.py`, Raw Stream tab |
| TTbin combiner (`mergeStreamFiles`, channel/time offsets, overlap-only, warning about time bases) | `app/utilities/ttbin_combiner.py` |
| Delay calibration (Correlation peaks → accept → `setInputDelay` → verify) | `app/utilities/delay_calibration.py` |
| Multi-Time-Tagger: Synchronizer groups (channel = TT*100+input), `TimeTaggerNetwork` multi-server (client channel map via `getClientChannel`), sync states, cross-device validation | `swabian_backend/devices.py`, `app/synchronization/sync_manager.py` |
| Reference clock (`setReferenceClock`, live `getReferenceClockState`) with warning | Synchronization tab |
| Sequences (apply preset / verify / groups / delay calibration / wait / save) | `app/utilities/sequence_runner.py` |
| Analysis plugins: G2, Coincidence (CAR), JSI, Histogram, Count Statistics, Background, Peaks, Fits, Delay | `app/analysis/plugins.py` |
| Simulator (synthetic data generated with the library's `Experimental` generators, replayed as a live device) | `simulator/` |
| Crash recovery (status.json heartbeat, INTERRUPTED marking) | `app/storage/run_store.py` |
| Logging incl. `setLogger` integration, export with run | `app/utilities/logging_setup.py` |
| Tab show/hide/reorder/reset, Basic/Advanced/Developer modes, UI prefs separate from experiment metadata | `app/gui/tab_manager.py`, `app/storage/settings_store.py` |

See `docs/` for the architecture, the API mapping table and the user guide.

## Data layout

```
<data_dir>/<experiment_id>/<timestamp_run>/
    measurement.json       group metadata, frozen experiment config, device + hardware states,
                           tagger getConfiguration(), raw files, status
    status.json            checkpoint (RUNNING / COMPLETED / INTERRUPTED ...)
    results/<type>_<id>.json + .npz   one per measurement (raw counts + explicit normalisations)
    raw/<name>.ttbin, <name>.1.ttbin ...   FileWriter output
    analysis/              re-analysis outputs with provenance (raw never modified)
```

## Known environment issue (Windows / Anaconda)

On this machine the Qt6 bindings (PySide6 6.10, PyQt6) fail to import with
`DLL load failed while importing QtCore: The specified procedure could not be found`
because the Anaconda base environment provides an older `msvcp140.dll` next to
`python.exe` (loaded before the one Qt 6.10 needs). The application therefore
falls back to PyQt5 automatically (`app/qtbootstrap.py`; Settings → Diagnostics shows
the probe results). To use PySide6, update the MSVC runtime in the environment
(`conda update vc14_runtime` / install the latest *Microsoft Visual C++ Redistributable*
and remove the stale copy) or run the app from a clean virtual environment.

## License

MIT (see `LICENSE`).
