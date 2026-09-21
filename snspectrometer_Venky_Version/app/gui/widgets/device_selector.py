"""Device combo box kept in sync with the hardware manager."""
from __future__ import annotations

from typing import Callable, Optional

from qtpy.QtWidgets import QComboBox


class DeviceSelector(QComboBox):
    def __init__(self, controller, parent=None, include_virtual: bool = True):
        super().__init__(parent)
        self.controller = controller
        self.include_virtual = include_virtual
        controller.hardware_event.connect(lambda ev, p: self.refresh() if ev in ("connected", "disconnected") else None)
        self.refresh()

    def refresh(self) -> None:
        cur = self.currentData()
        self.blockSignals(True)
        self.clear()
        for dev_id in self.controller.device_ids():
            rec = self.controller.device_record(dev_id)
            if rec is None:
                continue
            h = rec.handle
            if not self.include_virtual and h.is_virtual:
                continue
            self.addItem(f"{dev_id}  ({h.model})", dev_id)
        self.blockSignals(False)
        if cur is not None:
            idx = self.findData(cur)
            if idx >= 0:
                self.setCurrentIndex(idx)
                return
        if self.count():
            self.setCurrentIndex(0)
            self.currentIndexChanged.emit(0)

    def device_id(self) -> Optional[str]:
        return self.currentData()

    def channel_provider(self) -> Callable[[], list[tuple[int, str]]]:
        def provider() -> list[tuple[int, str]]:
            dev = self.device_id()
            if dev is None:
                return []
            dm = self.controller.detector_map()
            entries = [(c.physical_channel, c.label) for c in dm.by_device(dev)]
            if not entries:
                try:
                    entries = [(ch, f"Ch {ch}") for ch in self.controller.handle(dev).channels_rising]
                except Exception:
                    entries = []
            for vc in self.controller.virtual_channels.get(dev, []):
                for ch, lab in vc.entries():
                    entries.append((ch, lab))
            return entries

        return provider
