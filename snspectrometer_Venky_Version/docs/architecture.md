# Architecture

## Layers

```
app/gui            Qt widgets (qtpy). No hardware calls; everything through the controller.
app/controller     AppController (QObject): signals, worker pool, poll thread, sequences.
app/measurements   BaseMeasurement + registry, MeasurementGroup (SynchronizedMeasurements),
                   MeasurementManager.
app/analysis       ReplaySession (TimeTaggerVirtual), plugins, statistics, fits, peaks, raw stream.
app/hardware       HardwareManager: discovery, connection lifecycle, channel config, health.
app/synchronization SyncManager: sync state derivation, reference clock.
app/storage        RunDirectory (+ crash checkpoints), presets, settings/UI prefs, experiment browser.
app/plotting       pyqtgraph widgets (live), matplotlib export (publication), decimation.
app/utilities      logging, delay calibration, TTbin combiner, sequence runner, system monitor.
app/models         DetectorChannel/DetectorMap, ExperimentConfig (+ frozen copy), Preset,
                   MeasurementResult/Provenance, units, state enums.
swabian_backend    the only package importing TimeTagger: api, devices, configuration,
                   measurements, virtual_channels, synchronization, recorder, replay, mock.
simulator          synthetic datasets via Experimental generators; SimulatedDevice (loop replay).
```

## Key decisions

* **One tagger abstraction.** `TaggerHandle` wraps a `TimeTagger`, `TimeTaggerNetwork` or
  `TimeTaggerVirtual`. Measurements never know which one they run on; the simulator and
  offline replay are `TimeTaggerVirtual` objects. Capabilities (`trigger_level`,
  `impedance`, `event_divider`, ...) are probed per handle and reported as unsupported
  rather than emulated.
* **Every run is a SynchronizedMeasurements group.** Even a single measurement is a
  one-member group, so raw `FileWriter` recording and all measurement objects always
  start on the same time tags. Lifecycle: preflight → prepare (SyncGroup) → arm (create
  library objects on the proxy tagger) → start / startFor → poll → finalize (results,
  metadata, release).
* **Explicit numerics.** Results keep raw integer counts. g² normalisation
  (`counts·T/(Δt·N1·N2)`), accidental estimates, background subtraction and JSI
  normalisation are computed at application level with the equation stored in the
  scalars; the library's `getDataNormalized()` is stored alongside for comparison.
* **Provenance.** Each result JSON carries configuration + hash, library version, raw
  file list, NPZ checksum; replay/plugin outputs carry `Provenance` (source files,
  parameters, versions). Raw files are never modified; analysis lives in `analysis/`.
* **Threading.** The Swabian library processes tags in its own threads. The GUI polls
  snapshots from a dedicated `PollThread` at the configured rate (default 5 Hz) and
  device health once per second; blocking calls (discovery, connect, reference clock
  lock, dataset generation, delay calibration, merges, file scans) run in the Qt thread
  pool. Plots receive min/max-decimated copies; stored arrays are untouched.
* **Version differences.** `GatedCounter` (2.22+) vs `CountBetweenMarkers` (2.21),
  `GatedChannels` vs `GatedChannel`: the adapter picks what exists and records the
  implementation in the result. Unsupported library versions produce a diagnostic, not a crash.
* **Pause of a replay** is not an API primitive: `ReplaySource.pause()` stops the
  replay and remembers the stream position (via a `TimeTagStream` probe's `tGetData`);
  `resume()` re-queues the file with `appendFile(begin=position)`. A small overlap of
  already-buffered tags can be delivered twice; this is documented in the Analysis tab.
