"""In-memory mock of the subset of the Swabian TimeTagger module used by the app.

Used by unit tests when the real library is not installed (or to force
deterministic values). It mimics signatures and return shapes only; it does not
process time tags. Install with ``swabian_backend.set_api_module(build_mock())``.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import numpy as np

CHANNEL_UNUSED = -134217728
LOGGER_INFO, LOGGER_WARNING, LOGGER_ERROR = 0, 1, 2


class ChannelEdge:
    All, Rising, Falling = 0, 1, 2


class CoincidenceTimestamp:
    Last, Average, First, ListedFirst = 0, 1, 2, 3


class GatedChannelInitial:
    Closed, Open = 0, 1


class TagType:
    TimeTag, Error, OverflowBegin, OverflowEnd, MissedEvents = 0, 1, 2, 3, 4


class Resolution:
    Standard, HighResA, HighResB, HighResC = 0, 1, 2, 3


class _State:
    devices: dict[str, str] = {"MOCK-0001": "Time Tagger Ultra (mock)"}
    servers: list[str] = []
    logger = None
    virtual_channel_counter = 1000
    created: list[Any] = []
    fail_next_create = False


def getVersion():
    return "2.22.6-mock"


def getCompilerVersion():
    return "mock"


def getCompilationTimestamp():
    return "mock"


def hasTimeTaggerVirtualLicense():
    return True


def scanTimeTagger(include_model_name=False):
    if include_model_name:
        return [f"{s},{m}" for s, m in _State.devices.items()]
    return list(_State.devices)


def scanTimeTaggerServers():
    return list(_State.servers)


def getTimeTaggerServerInfo(address="localhost:41101"):
    return json.dumps({"address": address, "channels": [1, 2, 3, 4]})


def setLogger(cb):
    _State.logger = cb


def freeTimeTagger(tagger):
    tagger._freed = True


def mergeStreamFiles(output, inputs, channel_offsets, time_offsets, overlap_only):
    with open(output, "w") as fh:
        fh.write(json.dumps({"merged": list(inputs), "channel_offsets": list(channel_offsets), "time_offsets": list(time_offsets)}))


# --------------------------------------------------------------------------- taggers
class _Base:
    def __init__(self, channels):
        self._channels = list(channels)
        self._delays = {c: 0 for c in self._channels}
        self._deadtime = {c: 0 for c in self._channels}
        self._divider = {c: 1 for c in self._channels}
        self._overflows = 0
        self._freed = False
        self._ref = SimpleNamespace(enabled=False, is_locked=False, is_synchronized=False, clock_period=100000, clock_channel=CHANNEL_UNUSED, synchronization_channel=CHANNEL_UNUSED, ideal_clock_channel=CHANNEL_UNUSED, averaging_periods=0.0, synchronization_offset=0, event_divider=1, error_counter=0, last_ideal_clock_event=0, period_error=0.0, phase_error_estimation=0.0)

    def getInputDelay(self, ch):
        return self._delays.get(ch, 0)

    def setInputDelay(self, ch, d):
        self._delays[ch] = int(d)

    def getDeadtime(self, ch):
        return self._deadtime.get(ch, 0)

    def setDeadtime(self, ch, d):
        if d < 0:
            raise ValueError("negative deadtime")
        self._deadtime[ch] = int(d)

    def getDeadtimeRange(self, ch):
        return (0, 2**40)

    def getEventDivider(self, ch):
        return self._divider.get(ch, 1)

    def setEventDivider(self, ch, d):
        self._divider[ch] = int(d)

    def getOverflows(self):
        return self._overflows

    def getOverflowsAndClear(self):
        v = self._overflows
        self._overflows = 0
        return v

    def clearOverflows(self):
        self._overflows = 0

    def getInvertedChannel(self, ch):
        return -ch if ch > 0 else CHANNEL_UNUSED

    def isUnusedChannel(self, ch):
        return ch == CHANNEL_UNUSED

    def getConfiguration(self):
        return {"mock": True, "channels": self._channels, "delays": self._delays}

    def setReferenceClock(self, clock_channel, clock_frequency=10e6, time_constant=1e-3, synchronization_channel=CHANNEL_UNUSED, synchronization_offset=0, wait_until_locked=True):
        self._ref.enabled = True
        self._ref.is_locked = True
        self._ref.clock_channel = clock_channel
        self._ref.clock_period = int(1e12 / clock_frequency)

    def disableReferenceClock(self):
        self._ref.enabled = False
        self._ref.is_locked = False

    def getReferenceClockState(self):
        return self._ref

    def setConditionalFilter(self, trigger, filtered):
        pass

    def sync(self, timeout=-1):
        return True


class TimeTagger(_Base):
    def __init__(self, serial="", channels=range(1, 9)):
        super().__init__(channels)
        self.serial = serial or next(iter(_State.devices))
        self._trigger = {c: 0.5 for c in self._channels}
        self._hz = {c: True for c in self._channels}
        self._test = {c: False for c in self._channels}
        self._hwcomp = True

    def getSerial(self):
        return self.serial

    def getModel(self):
        return _State.devices.get(self.serial, "mock")

    def getChannelList(self, edge=ChannelEdge.All):
        if edge == ChannelEdge.Rising:
            return list(self._channels)
        if edge == ChannelEdge.Falling:
            return [-c for c in self._channels]
        return list(self._channels) + [-c for c in self._channels]

    def setTriggerLevel(self, ch, v):
        rng = self.getTriggerLevelRange(ch)
        if not rng[0] <= v <= rng[1]:
            raise ValueError("out of range")
        self._trigger[ch] = round(v / 0.005) * 0.005

    def getTriggerLevel(self, ch):
        return self._trigger[ch]

    def getTriggerLevelRange(self, ch):
        return (-2.5, 2.5)

    def getDACRange(self):
        return (-2.5, 2.5)

    def setInputImpedanceHigh(self, ch, hz):
        self._hz[ch] = bool(hz)

    def getInputImpedanceHigh(self, ch):
        return self._hz[ch]

    def getInputHysteresis(self, ch):
        return 20

    def getHardwareDelayCompensation(self, ch):
        return 123 if self._hwcomp else 0

    def setHardwareDelayCompensationActive(self, active):
        self._hwcomp = bool(active)

    def setTestSignal(self, ch, en):
        self._test[ch] = bool(en)

    def getTestSignal(self, ch):
        return self._test[ch]

    def getPcbVersion(self):
        return "1.0"

    def getFirmwareVersion(self):
        return "mock-fw"

    def getDeviceLicense(self):
        return "{}"

    def startServer(self, *a, **k):
        pass

    def stopServer(self):
        pass

    def reset(self):
        pass


class TimeTaggerVirtual(_Base):
    def __init__(self, filename="", begin=0, duration=-1):
        chans = [1, 2, 3, 4] if filename else []
        super().__init__(chans)
        self.filename = filename
        self._running = False
        self._speed = 1.0
        self._queue: list[int] = []
        self._t0 = None
        self._finished_after = 0.5

    def getChannelList(self):
        return list(self._channels)

    def run(self, speed=-1.0):
        self._speed = speed
        self._running = True
        self._t0 = time.time()
        self._queue = [1]
        return [1]

    def stop(self):
        self._running = False
        self._queue = []

    def appendFile(self, filename, begin=0, duration=-1, clear=False):
        if clear:
            self._queue = []
        self._queue.append(len(self._queue) + 2)
        return self._queue[-1]

    def waitUntilFinished(self, ID=0, timeout=-1):
        if not self._running or self._t0 is None:
            return False
        return (time.time() - self._t0) >= self._finished_after

    def getReplaySpeed(self):
        return self._speed

    def setEventDivider(self, ch, d):
        raise RuntimeError("The Event Divider is not supported on the Time Tagger Virtual.")


class TimeTaggerNetwork(TimeTagger):
    def __init__(self, addresses):
        super().__init__("NET", range(1, 5))
        self.addresses = addresses if isinstance(addresses, list) else [addresses]

    def isConnected(self):
        return True

    def getServers(self):
        return [SimpleNamespace(getAddress=lambda a=a: a, getSerial=lambda: "S", getModel=lambda: "M", getChannelList=lambda e=None: [1, 2, 3, 4], getClientChannel=lambda c, i=i: c + i * 1000) for i, a in enumerate(self.addresses)]

    def getOverflowsClient(self):
        return 0


def createTimeTagger(serial="", resolution=None):
    if _State.fail_next_create:
        _State.fail_next_create = False
        raise RuntimeError("mock: no device")
    if serial and serial not in _State.devices:
        raise RuntimeError(f"mock: unknown serial {serial}")
    if not _State.devices:
        raise RuntimeError("mock: no Time Tagger devices available")
    t = TimeTagger(serial)
    _State.created.append(t)
    return t


def createTimeTaggerVirtual(filename="", begin=0, duration=-1):
    return TimeTaggerVirtual(filename, begin, duration)


def createTimeTaggerNetwork(addresses):
    return TimeTaggerNetwork(addresses)


# --------------------------------------------------------------------------- measurements
class _Iterator:
    def __init__(self, tagger, *a, **k):
        self.tagger = tagger
        self._running = not isinstance(tagger, _Proxy)
        self._t0 = time.time()
        self._cap = 0
        self._deadline = None
        if isinstance(tagger, _Proxy):
            tagger.sm._register(self)

    def start(self):
        self._running = True
        self._t0 = time.time()
        self._deadline = None

    def startFor(self, duration, clear=True):
        self.start()
        self._deadline = self._t0 + duration / 1e12
        if clear:
            self.clear()

    def stop(self):
        if self._running:
            self._cap += int((time.time() - self._t0) * 1e12)
        self._running = False

    def clear(self):
        self._cap = 0
        self._t0 = time.time()

    def isRunning(self):
        if self._running and self._deadline is not None and time.time() >= self._deadline:
            self.stop()
        return self._running

    def getCaptureDuration(self):
        extra = int((time.time() - self._t0) * 1e12) if self._running else 0
        return self._cap + extra

    def getConfiguration(self):
        return {"name": type(self).__name__}

    def waitUntilFinished(self, timeout=-1):
        return True


class Countrate(_Iterator):
    def __init__(self, tagger, channels):
        super().__init__(tagger)
        self.channels = list(channels)

    def getData(self):
        return np.full(len(self.channels), 1000.0)

    def getCountsTotal(self):
        return np.full(len(self.channels), int(1000 * self.getCaptureDuration() / 1e12), dtype=np.int64)


class Counter(_Iterator):
    def __init__(self, tagger, channels, binwidth=1000000000, n_values=1):
        super().__init__(tagger)
        self.channels, self.binwidth, self.n = list(channels), binwidth, n_values

    def getData(self, rolling=True):
        return np.ones((len(self.channels), self.n), dtype=np.int64)

    def getDataNormalized(self, rolling=True):
        return np.full((len(self.channels), self.n), 1e12 / self.binwidth)

    def getIndex(self):
        return np.arange(self.n, dtype=np.int64) * self.binwidth

    def getDataTotalCounts(self):
        return np.full(len(self.channels), self.n, dtype=np.int64)


class CountBetweenMarkers(_Iterator):
    def __init__(self, tagger, click_channel, begin_channel, end_channel=CHANNEL_UNUSED, n_values=1000):
        super().__init__(tagger)
        self.n = n_values

    def getData(self):
        return np.ones(self.n, dtype=np.int64)

    def getIndex(self):
        return np.arange(self.n, dtype=np.int64) * 1000

    def getBinWidths(self):
        return np.full(self.n, 1000, dtype=np.int64)

    def ready(self):
        return True


class Histogram(_Iterator):
    def __init__(self, tagger, click_channel, start_channel=CHANNEL_UNUSED, binwidth=1000, n_bins=1000):
        super().__init__(tagger)
        self.binwidth, self.n = binwidth, n_bins

    def getData(self):
        d = np.zeros(self.n, dtype=np.int64)
        d[self.n // 4] = 100
        return d

    def getIndex(self):
        return np.arange(self.n, dtype=np.int64) * self.binwidth


class Correlation(_Iterator):
    def __init__(self, tagger, channel_1, channel_2=CHANNEL_UNUSED, binwidth=1000, n_bins=1000):
        super().__init__(tagger)
        self.binwidth, self.n = binwidth, n_bins

    def getIndex(self):
        return (np.arange(self.n, dtype=np.int64) - self.n // 2) * self.binwidth

    def getData(self):
        idx = self.getIndex()
        return (10 + 100 * np.exp(-0.5 * (idx / (3 * self.binwidth)) ** 2)).astype(np.int64)

    def getDataNormalized(self):
        return self.getData().astype(float) / 10.0


class _PairsData:
    def __init__(self, n_ch, n_bins, binwidth):
        self.n_ch, self.n_bins, self.binwidth = n_ch, n_bins, binwidth

    def getCounts(self, exclude_self_coincidences=True):
        idx = (np.arange(self.n_bins) - self.n_bins // 2) * self.binwidth
        base = 5 + 50 * np.exp(-0.5 * (idx / (3 * self.binwidth)) ** 2)
        return np.tile(base, (self.n_ch, self.n_ch, 1)).astype(np.int64)

    def getG2(self, exclude_self_coincidences=True):
        return self.getCounts().astype(float) / 5.0


class CorrelationPairs(_Iterator):
    def __init__(self, tagger, channels, binwidth=1000, n_bins=1000):
        super().__init__(tagger)
        self.channels, self.binwidth, self.n = list(channels), binwidth, n_bins

    def getIndex(self):
        return (np.arange(self.n, dtype=np.int64) - self.n // 2) * self.binwidth

    def getDataObject(self):
        return _PairsData(len(self.channels), self.n, self.binwidth)


class Histogram2D(_Iterator):
    def __init__(self, tagger, s, s1, s2, bw1, bw2, n1, n2):
        super().__init__(tagger)
        self.bw1, self.bw2, self.n1, self.n2 = bw1, bw2, n1, n2

    def getData(self):
        return np.zeros((self.n1, self.n2), dtype=np.int64)

    def getIndex_1(self):
        return np.arange(self.n1, dtype=np.int64) * self.bw1

    def getIndex_2(self):
        return np.arange(self.n2, dtype=np.int64) * self.bw2


class HistogramND(_Iterator):
    def __init__(self, tagger, start, stops, binwidths, n_bins):
        super().__init__(tagger)
        self.bws, self.ns = list(binwidths), list(n_bins)

    def getData(self):
        return np.zeros(int(np.prod(self.ns)), dtype=np.int64)

    def getIndex(self, dim=0):
        return np.arange(self.ns[dim], dtype=np.int64) * self.bws[dim]


class TimeDifferences(_Iterator):
    def __init__(self, tagger, click, start=CHANNEL_UNUSED, nxt=CHANNEL_UNUSED, sync=CHANNEL_UNUSED, binwidth=1000, n_bins=1000, n_histograms=1):
        super().__init__(tagger)
        self.bw, self.n, self.nh = binwidth, n_bins, n_histograms

    def getData(self):
        return np.zeros((self.nh, self.n), dtype=np.int64)

    def getIndex(self):
        return np.arange(self.n, dtype=np.int64) * self.bw

    def setMaxRollovers(self, n):
        pass

    def getHistogramIndex(self):
        return 0

    def getCounts(self):
        return 0

    def ready(self):
        return False


class TimeDifferencesND(_Iterator):
    def __init__(self, tagger, click, start, nexts, syncs, n_hist, binwidth, n_bins):
        super().__init__(tagger)
        self.bw, self.n, self.nh = binwidth, n_bins, list(n_hist)

    def getData(self):
        return np.zeros((int(np.prod(self.nh)), self.n), dtype=np.int64)

    def getIndex(self):
        return np.arange(self.n, dtype=np.int64) * self.bw

    def getHistogramIndex(self):
        return [0] * len(self.nh)

    def getRollovers(self):
        return [0] * len(self.nh)


class _Buffer:
    def __init__(self, n=0):
        self.size = n
        self.hasOverflows = False
        self.tStart = 0
        self.tGetData = int(time.time() * 1e6) % 10**12

    def getTimestamps(self):
        return np.arange(self.size, dtype=np.int64) * 1000

    def getChannels(self):
        return np.ones(self.size, dtype=np.int32)

    def getEventTypes(self):
        return np.zeros(self.size, dtype=np.int32)

    def getMissedEvents(self):
        return np.zeros(self.size, dtype=np.int32)

    def getOverflows(self):
        return self.getEventTypes()


class TimeTagStream(_Iterator):
    def __init__(self, tagger, n_max_events, channels):
        if not list(channels):
            raise ValueError("No channel was provided.")
        super().__init__(tagger)

    def getData(self):
        return _Buffer(1)

    def getCounts(self):
        return 1


class FileWriter(_Iterator):
    def __init__(self, tagger, filename, channels):
        super().__init__(tagger)
        self.filename = filename
        with open(filename, "wb") as fh:
            fh.write(b"MOCKTTBIN")
        self._max = 1 << 30

    def setMaxFileSize(self, n):
        self._max = n

    def getMaxFileSize(self):
        return self._max

    def getTotalEvents(self):
        return 42

    def getTotalSize(self):
        return 9

    def split(self, new_filename=""):
        pass

    def setMarker(self, m):
        pass


class FileReader:
    def __init__(self, filenames):
        self.files = [filenames] if isinstance(filenames, str) else list(filenames)
        self._left = 2

    def hasData(self):
        return self._left > 0

    def getData(self, n):
        self._left -= 1
        return _Buffer(min(n, 100))

    def getConfiguration(self):
        return {"mock": True}

    def getChannelList(self):
        return [1, 2]

    def getLastMarker(self):
        return ""


# --------------------------------------------------------------------------- virtual channels
class _VChannel(_Iterator):
    def __init__(self, tagger, n_out):
        super().__init__(tagger)
        self._out = []
        for _ in range(n_out):
            _State.virtual_channel_counter += 1
            self._out.append(_State.virtual_channel_counter)


class Coincidences(_VChannel):
    def __init__(self, tagger, groups, coincidenceWindow, timestamp=CoincidenceTimestamp.Last):
        super().__init__(tagger, len(groups))

    def getChannels(self):
        return list(self._out)

    def setCoincidenceWindow(self, w):
        pass


class Coincidence(_VChannel):
    def __init__(self, tagger, channels, coincidenceWindow=1000, timestamp=CoincidenceTimestamp.Last):
        super().__init__(tagger, 1)

    def getChannel(self):
        return self._out[0]


class Combinations(_VChannel):
    def __init__(self, tagger, channels, window_size):
        super().__init__(tagger, 0)
        self.channels = list(channels)

    def getChannel(self, input_channels):
        _State.virtual_channel_counter += 1
        return _State.virtual_channel_counter

    def getSumChannel(self, n):
        _State.virtual_channel_counter += 1
        return _State.virtual_channel_counter


class DelayedChannels(_VChannel):
    def __init__(self, tagger, input_channels, delay):
        super().__init__(tagger, len(input_channels))

    def getChannels(self):
        return list(self._out)

    def setDelay(self, d):
        pass


class GatedChannels(_VChannel):
    def __init__(self, tagger, input_channels, gate_start, gate_stop, initial=GatedChannelInitial.Closed):
        super().__init__(tagger, len(input_channels))

    def getChannels(self):
        return list(self._out)


class Combiner(_VChannel):
    def __init__(self, tagger, channels):
        super().__init__(tagger, 1)

    def getChannel(self):
        return self._out[0]


# --------------------------------------------------------------------------- synchronized measurements
class _Proxy:
    def __init__(self, sm):
        self.sm = sm


class SynchronizedMeasurements:
    def __init__(self, tagger):
        self.tagger = tagger
        self._meas: list[_Iterator] = []
        self._proxy = _Proxy(self)

    def getTagger(self):
        return self._proxy

    def _register(self, m):
        self._meas.append(m)

    def registerMeasurement(self, m):
        self._register(m)

    def unregisterMeasurement(self, m):
        if m in self._meas:
            self._meas.remove(m)

    def start(self):
        for m in self._meas:
            m.start()

    def startFor(self, duration, clear=True):
        for m in self._meas:
            m.startFor(duration, clear)

    def stop(self):
        for m in self._meas:
            m.stop()

    def clear(self):
        for m in self._meas:
            m.clear()

    def isRunning(self):
        return any(m.isRunning() for m in self._meas)

    def waitUntilFinished(self, timeout=-1):
        return True


def build_mock() -> Any:
    """Return a module-like namespace with all mock symbols."""
    import sys
    return sys.modules[__name__]


def set_mock_devices(devices: dict[str, str]) -> None:
    _State.devices = dict(devices)
