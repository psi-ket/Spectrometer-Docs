# Swabian API mapping

Every library symbol used, with the manual section it was checked against
(Time Tagger User Manual 2.22.6) and where it is wrapped.

| Feature | Library call | Manual | Adapter |
|---|---|---|---|
| Discovery | `scanTimeTagger(True)`, `scanTimeTaggerServers()`, `getTimeTaggerServerInfo()` | 5.2.6 | `devices.py` |
| Open / close | `createTimeTagger(serial, resolution)`, `createTimeTaggerNetwork(addr | [addrs])`, `createTimeTaggerVirtual(file, begin, duration)`, `freeTimeTagger()` | 5.2.6 | `devices.py` |
| Channel list | `getChannelList(ChannelEdge.Rising/Falling/All)`, `getInvertedChannel()` | 5.3.2 | `devices.py` |
| Trigger level | `setTriggerLevel()`, `getTriggerLevel()`, `getTriggerLevelRange()` / `getDACRange()` | 5.3.2 | `configuration.py` |
| Impedance (TTX only) | `setInputImpedanceHigh()`, `getInputImpedanceHigh()` | 5.3.2 | `configuration.py` |
| Input delay | `setInputDelay()`, `getInputDelay()` | 5.3.1 | `configuration.py` |
| Dead time | `setDeadtime()`, `getDeadtime()`, `getDeadtimeRange()` | 5.3.1 | `configuration.py` |
| Event divider | `setEventDivider()`, `getEventDivider()` (not on virtual) | 5.3.1 | `configuration.py` |
| HW delay compensation | `setHardwareDelayCompensationActive()`, `getHardwareDelayCompensation()` | 5.3.2 | `configuration.py` |
| Test signal | `setTestSignal()`, `getTestSignal()` | 5.3.2 | `configuration.py` |
| Overflows | `getOverflows()`, `clearOverflows()`, `getOverflowsClient()` | 5.3.1 / 5.3.4 | `devices.py`, `hardware/manager.py` |
| Configuration snapshot | `getConfiguration()` | 5.3.1 | `devices.py`, stored in `measurement.json` |
| Multi-server channels | `getServers()`, `getServer(addr)`, `getClientChannel()` | 5.3.4 | `devices.py` |
| Synchronizer | channel numbering `TT*100 + input` | 7.5 | `devices.py` |
| Reference clock | `setReferenceClock(...)`, `disableReferenceClock()`, `getReferenceClockState()` | 5.3.1 / 5.3.5 | `synchronization.py` |
| Synchronized start | `SynchronizedMeasurements`, `getTagger()`, `start/startFor/stop/clear/isRunning/waitUntilFinished` | 5.5.7 | `synchronization.py` |
| Count rate | `Countrate.getData()/getCountsTotal()` | 5.5.2 | `measurements/counting.py` |
| Count trace | `Counter.getData(rolling)/getDataNormalized/getIndex/getDataTotalCounts` | 5.5.2 | `measurements/counting.py` |
| Count between markers | `GatedCounter` (2.22+) else `CountBetweenMarkers` | 5.5.2 | `swabian_backend/measurements.py` |
| Histogram | `Histogram`, `Correlation` for ± ranges | 5.5.3 | `measurements/histograms.py` |
| g² | `Correlation.getData()/getDataNormalized()/getIndex()` + `Countrate` totals | 5.5.3 | `measurements/correlations.py` |
| Pair matrix / JSI | `CorrelationPairs.getDataObject().getCounts(exclude_self)` (N×N×bins) | 5.5.3 | `measurements/correlations.py` |
| Coincidences | `Coincidences(groups, window, timestamp)`, `getChannels()` | 5.4.4 | `virtual_channels.py` |
| Other virtual channels | `Coincidence`, `Combinations` (+ `getSumChannel`), `DelayedChannels`, `GatedChannels`/`GatedChannel`, `Combiner` | 5.4 | `virtual_channels.py` |
| Histogram2D / ND | `Histogram2D`, `HistogramND` (reshape row-major) | 5.5.3 | `measurements/histograms.py` |
| TimeDifferences | `TimeDifferences`, `TimeDifferencesND`, `setMaxRollovers`, `getHistogramIndex` | 5.5.3 | `measurements/histograms.py` |
| Raw stream | `TimeTagStream.getData()` → `TimeTagStreamBuffer` (timestamps, channels, event types, missed events, `tStart/tGetData`) | 5.5.6 | `measurements/raw_stream.py`, `replay.py` |
| Recording | `FileWriter(tagger, file, channels)`, `setMaxFileSize`, `split`, `setMarker`, `getTotalEvents/Size` | 5.5.6 | `recorder.py` |
| Reading | `FileReader(files)`, `getData(n)`, `hasData`, `getConfiguration`, `getChannelList`, `getLastMarker` | 5.5.6 | `replay.py` |
| Replay | `TimeTaggerVirtual.run(speed)`, `stop()`, `appendFile(file, begin, duration, clear)`, `waitUntilFinished(ID, timeout)`, `getChannelList()` | 5.3.3 | `replay.py` |
| Merging | `mergeStreamFiles(out, ins, channel_offsets, time_offsets, overlap_only)` | 5.2.6 | `replay.py` |
| Logging | `setLogger(callback)` | 5.2.6 | `utilities/logging_setup.py` |
| Simulator (Experimental) | `Experimental.ExponentialSignalGenerator/PatternSignalGenerator/TransformEfficiency/TransformGaussianBroadening/TransformDeadtime` | (not in manual; installed package docstrings) | `simulator/demo_dataset.py` only |

## Verified behaviours (library 2.21.2, virtual tagger)

* `Correlation(ch1, ch2)` histograms `t_ch1 − t_ch2`; `setInputDelay(ch2, +d)` moves the peak
  by `−d`. Delay calibration therefore applies `setInputDelay(ch, +weighted_centre)` of
  `Correlation(reference, ch)`, matching the manual's 3.1.3 example.
* `run(speed)`: speeds < 0.1 rejected by the library → clamped to 0.1 with a note; `speed ≤ 0`
  → −1 (as fast as possible).
* `appendFile` twice yields a monotonic, gap-free stream; an overflow marker appears at
  the file boundary (visible on the simulated device).
* `CorrelationPairs.getG2()` equals the application's `counts·T/(Δt·N1·N2)` to 1e-11.
