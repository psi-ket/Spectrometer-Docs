import TimeTagger
import numpy as np
import numba
import numba.typed

class CountBetweenMarkersMultiChannels(TimeTagger.CustomMeasurement):
    def __init__(self, tagger, click_channels, begin_channel, end_channel=None, n_bins=1000):
        TimeTagger.CustomMeasurement.__init__(self, tagger)

        self.click_channels = numba.typed.List()
        for ch in click_channels:
            self.click_channels.append(np.int16(ch))
            self.register_channel(channel=np.int16(ch))

        self.begin_channel = np.int16(begin_channel)
        self.register_channel(channel=self.begin_channel)

        # Numba-safe end handling
        self.has_end = end_channel is not None
        self.end_channel = np.int16(end_channel if end_channel is not None else 0)
        if self.has_end:
            self.register_channel(channel=self.end_channel)

        self.max_bins = n_bins
        self.clear_impl()
        self.finalize_init()

    def __del__(self):
        # The measurement must be stopped before deconstruction to avoid
        # concurrent process() calls.
        self.stop()

    def getData(self):
        # Locking this instance to guarantee that process() is not running in parallel
        # This ensures to return a consistent data.
        with self.mutex:
            return self.data.copy()

    def getBinWidths(self):
        # Locking this instance to guarantee that process() is not running in parallel
        # This ensures to return a consistent data.
        with self.mutex:
            return self.binWidth.copy()

    def getIndex(self):
        # Locking this instance to guarantee that process() is not running in parallel
        # This ensures to return a consistent data.
        with self.mutex:
            return self.beginClick.copy()

    def on_start(self):
        # The lock is already acquired within the backend.
        pass

    def on_stop(self):
        # The lock is already acquired within the backend.
        pass
    def clear_impl(self):
        self.bin = -1
        self.inOverflow = False
        self.countsInOverflow = np.zeros(len(self.click_channels), dtype=np.uint32)
        self.stopped = True
        self.data = np.zeros((len(self.click_channels), self.max_bins), dtype=np.uint32)
        self.binWidth = np.zeros(self.max_bins, dtype=np.uint64)
        self.beginClick = np.zeros(self.max_bins, dtype=np.uint64)
        self.firstStart = 0
        self.lastStart = 0

    @staticmethod
    @numba.jit(nopython=True, nogil=True)
    def fast_process(tags, data, binWidth, beginClick,
                     click_channels, begin_channel,
                     has_end, end_channel,
                     n_bins, bin, inOverflow, countsInOverflow,
                     isStopped, first_start, last_start):

        for tag in tags:
            ttype = tag["type"]

            if ttype == TimeTagger.TagType["OverflowBegin"]:
                inOverflow = True

            elif ttype == TimeTagger.TagType["OverflowEnd"]:
                if bin >= 0 and bin < n_bins:
                    data[:, bin] += countsInOverflow
                inOverflow = False

            elif ttype == TimeTagger.TagType["MissedEvents"]:
                if inOverflow:
                    # If marker(s) miss events, data can be invalid
                    if tag["channel"] == begin_channel or (has_end and tag["channel"] == end_channel):
                        if not isStopped:
                            raise RuntimeWarning("Missed events on marker channel. Data likely invalid.")
                    else:
                        for i, ch in enumerate(click_channels):
                            if tag["channel"] == ch:
                                if bin >= 0 and bin < n_bins and not isStopped:
                                    countsInOverflow[i] += tag["missed_events"]

            elif ttype == TimeTagger.TagType["TimeTag"]:
                if inOverflow:
                    raise RuntimeError("In overflow state while getting good TimeTag.")

                ch = tag["channel"]
                tm = tag["time"]

                if ch == begin_channel:
                    # close previous bin width (time since last marker)
                    if bin >= 0 and bin < n_bins and not isStopped:
                        binWidth[bin] = tm - last_start

                    bin += 1
                    isStopped = False
                    last_start = tm

                    if bin == 0:
                        first_start = tm
                        beginClick[bin] = 0
                    elif bin > 0 and bin < n_bins:
                        beginClick[bin] = tm - first_start

                elif has_end and ch == end_channel:
                    if bin >= 0 and bin < n_bins and not isStopped:
                        binWidth[bin] = tm - last_start
                    isStopped = True

                else:
                    for i, cc in enumerate(click_channels):
                        if ch == cc:
                            if bin >= 0 and bin < n_bins and not isStopped:
                                data[i, bin] += 1

            elif ttype == TimeTagger.TagType["Error"]:
                if not isStopped:
                    raise RuntimeError("Error tag received! Measurement stops here!")

        return first_start, last_start, bin, isStopped

    def process(self, incoming_tags, begin_time, end_time):
        self.firstStart, self.lastStart, self.bin, self.stopped = self.fast_process(
            incoming_tags,
            self.data,
            self.binWidth,
            self.beginClick,
            self.click_channels,
            self.begin_channel,
            self.has_end,
            self.end_channel,
            self.max_bins,
            self.bin,
            self.inOverflow,
            self.countsInOverflow,
            self.stopped,
            self.firstStart,
            self.lastStart,
        )
