# User guide

## Typical experiment

1. **Launch** `python -m app.main`. Discovery runs automatically (Hardware tab shows the
   startup diagnostics: devices, servers, library version/license, sync state).
2. **Connect** the Time Tagger (USB, network server(s), or the simulator when no hardware
   is present).
3. **Channels tab**: name detectors, enter wavelengths and roles (signal/idler/marker/
   sync), set trigger level (slider or table, read-back shown in *Applied*), impedance,
   delay, dead time, divider. *Apply* pushes to hardware; every change is logged. Save
   the mapping as a **preset**; presets are portable with an explicit device/channel
   mapping dialog and can be compared with the read-back hardware state.
4. **Synchronization tab** (Advanced): view sub-devices, reference clock live state,
   configure `setReferenceClock` (acquisition must be stopped; warning shown).
5. **Measurement tabs** (Count Rate, Count Trace, Count Between Markers, Histogram,
   Coincidences, Coincidence Matrix, G2, JSI, Advanced): configure, set duration,
   tick *Record raw TTbin*, run *Preflight* or *Arm & Start*. Live plots update at the
   configured rate; the run card shows status, progress, raw file size, overflows.
6. **Measurements tab**: build a *group* of several measurements that must start on the
   same tags (SynchronizedMeasurements): Prepare → Arm → Start; Pause/Resume/Stop/Clear.
   Templates can be saved/loaded.
7. Results are saved automatically (JSON + NPZ) with `measurement.json` and the raw
   TTbin in the run directory; see the **Experiments tab**.
8. **Analysis tab**: open a TTbin (or *Open run folder in Analysis*), add measurements
   with any parameters, Play at a chosen speed (0.1x … 100x, Max), Pause/Resume/Stop/
   Restart, watch the evolving plots (replace / accumulate / history / difference),
   save results with provenance, run analysis plugins (fits, CAR, JSI marginals, …).
9. **TTbin Utility**: merge files with channel/time offsets (note the time-base warning).
10. **Delay Calibration**: measure, accept, apply, verify.
11. **Sequences**: automate steps (preset → verify → groups → calibration → save).

## Modes and tabs

*View → Application mode*: Basic (hardware, counts, histogram, coincidences, g², analysis),
Advanced (+ JSI, matrix, advanced measurements, synchronization, utilities, sequences),
Developer (+ raw stream, live TimeTagStream). Any tab can be hidden/reordered
(Settings → Tab visibility, or *View → Hide current tab*); *Reset UI layout* restores.

## Simulator

Hardware → *Connect simulator* generates a synthetic dataset (SPDC-like pairs with
wavelength-mapped detectors, jitter, dark counts, per-channel delays, marker and sync
channels) through the library's own signal generators, records it with `FileWriter` and
replays it in a loop as a live `TimeTaggerVirtual` device. Hardware-only settings
(trigger, impedance, divider) are reported as unsupported, exactly as on a real virtual
tagger. `python -m simulator --all` generates all scenarios for offline use.

## Interpreting outputs

* Coincidence counts are **raw counts** unless a quantity is explicitly labelled *rate*
  or *normalised*; g² plots offer raw counts / g² (app) / g² (library) / background-subtracted.
* JSI axes come from the detector wavelength calibration; nothing about the pump or
  energy conservation is assumed. `schmidt_number_proxy` is an SVD estimate on the
  detector grid, not the true Schmidt number.
* Interrupted runs (application crash, device loss) are marked `INTERRUPTED`, never
  `COMPLETED`.
