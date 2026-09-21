"""Tab registry with show / hide / disable / reorder / restore / reset and application modes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from qtpy.QtWidgets import QTabWidget, QWidget

MODE_TABS = {
    "BASIC": {"overview", "hardware", "channels", "measurements", "countrate", "counter", "gated_counter", "histogram", "coincidences", "g2", "analysis", "experiments", "logs", "settings"},
    "ADVANCED": {"overview", "hardware", "channels", "sync", "measurements", "countrate", "counter", "gated_counter", "histogram", "coincidences", "coincidence_matrix", "g2", "jsi", "advanced", "analysis", "combiner", "delay_cal", "sequences", "experiments", "logs", "settings"},
    "DEVELOPER": None,  # all
}


@dataclass
class TabDef:
    id: str
    title: str
    factory: Callable[[], QWidget]
    category: str = "basic"
    widget: Optional[QWidget] = None
    enabled: bool = True


class TabManager:
    def __init__(self, tabs: QTabWidget, ui_prefs, on_changed: Callable[[], None]):
        self.tabs = tabs
        self.prefs = ui_prefs
        self.on_changed = on_changed
        self.defs: list[TabDef] = []
        self.default_order: list[str] = []

    def register(self, tab_id: str, title: str, factory: Callable[[], QWidget], category: str = "basic") -> None:
        self.defs.append(TabDef(tab_id, title, factory, category))
        self.default_order.append(tab_id)

    def get(self, tab_id: str) -> Optional[TabDef]:
        for d in self.defs:
            if d.id == tab_id:
                return d
        return None

    def widget(self, tab_id: str) -> Optional[QWidget]:
        d = self.get(tab_id)
        if d is None:
            return None
        if d.widget is None:
            d.widget = d.factory()
        return d.widget

    def order(self) -> list[str]:
        saved = [t for t in self.prefs.tab_order if self.get(t)]
        return saved + [t for t in self.default_order if t not in saved]

    def visible_ids(self) -> list[str]:
        return [t for t in self.order() if t not in self.prefs.hidden_tabs]

    def layout(self) -> list[tuple[str, str, bool]]:
        return [(t, self.get(t).title, t not in self.prefs.hidden_tabs) for t in self.order()]

    def rebuild(self) -> None:
        current = self.tabs.tabText(self.tabs.currentIndex()) if self.tabs.count() else ""
        while self.tabs.count():
            self.tabs.removeTab(0)
        for tid in self.visible_ids():
            d = self.get(tid)
            w = self.widget(tid)
            idx = self.tabs.addTab(w, d.title)
            self.tabs.setTabEnabled(idx, d.enabled and tid not in self.prefs.disabled_tabs)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == current:
                self.tabs.setCurrentIndex(i)
        self.on_changed()

    def apply_layout(self, order: list[str], hidden: list[str]) -> None:
        self.prefs.tab_order = list(order)
        self.prefs.hidden_tabs = list(hidden)
        self.rebuild()

    def show(self, tab_id: str) -> None:
        if tab_id in self.prefs.hidden_tabs:
            self.prefs.hidden_tabs.remove(tab_id)
        self.rebuild()

    def hide(self, tab_id: str) -> None:
        if tab_id not in self.prefs.hidden_tabs:
            self.prefs.hidden_tabs.append(tab_id)
        self.rebuild()

    def set_disabled(self, tab_id: str, disabled: bool) -> None:
        if disabled and tab_id not in self.prefs.disabled_tabs:
            self.prefs.disabled_tabs.append(tab_id)
        elif not disabled and tab_id in self.prefs.disabled_tabs:
            self.prefs.disabled_tabs.remove(tab_id)
        self.rebuild()

    def restore_hidden(self) -> None:
        self.prefs.hidden_tabs = []
        self.rebuild()

    def reset_layout(self) -> None:
        self.prefs.tab_order = []
        self.prefs.hidden_tabs = []
        self.prefs.disabled_tabs = []
        self.rebuild()

    def apply_mode(self, mode: str) -> None:
        allowed = MODE_TABS.get(mode)
        if allowed is None:
            self.prefs.hidden_tabs = []
        else:
            self.prefs.hidden_tabs = [d.id for d in self.defs if d.id not in allowed]
        self.rebuild()

    def select(self, tab_id: str) -> None:
        d = self.get(tab_id)
        if d is None:
            return
        if tab_id in self.prefs.hidden_tabs:
            self.show(tab_id)
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) is d.widget:
                self.tabs.setCurrentIndex(i)
