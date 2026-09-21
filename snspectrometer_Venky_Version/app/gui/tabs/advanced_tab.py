"""Advanced measurements: Histogram2D/ND, TimeDifferences(ND), raw stream, virtual channel editor."""
from __future__ import annotations

from qtpy.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .measurement_tab import MeasurementTab
from .virtual_channels import VirtualChannelsEditor


class AdvancedTab(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        tabs = QTabWidget()
        tabs.addTab(MeasurementTab(controller, "histogram_2d"), "Histogram 2D")
        tabs.addTab(MeasurementTab(controller, "histogram_nd"), "Histogram ND")
        tabs.addTab(MeasurementTab(controller, "time_differences"), "Time Differences")
        tabs.addTab(MeasurementTab(controller, "time_differences_nd"), "Time Differences ND")
        tabs.addTab(VirtualChannelsEditor(controller), "Virtual Channels")
        if controller.settings.app_mode == "DEVELOPER":
            tabs.addTab(MeasurementTab(controller, "raw_stream"), "Live TimeTagStream")
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(tabs)
