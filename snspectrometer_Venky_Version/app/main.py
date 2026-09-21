"""Entry point: ``python -m app.main`` or the ``snspectrometer`` console script."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SNSPD Spectrometer (Swabian Time Tagger)")
    parser.add_argument("--simulator", action="store_true", help="connect the simulator at startup")
    parser.add_argument("--scenario", default="spectrometer_jsi", help="simulator scenario name")
    parser.add_argument("--mode", choices=["BASIC", "ADVANCED", "DEVELOPER"], help="application mode override")
    parser.add_argument("--ttbin", help="open this TTbin in the Analysis tab")
    args = parser.parse_args(argv)

    from . import qtbootstrap
    from qtpy.QtWidgets import QApplication
    import pyqtgraph as pg

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("SNSPD Spectrometer")
    app.setOrganizationName("snspectrometer")
    from .controller.app_controller import AppController
    from .gui.main_window import MainWindow

    controller = AppController()
    if args.mode:
        controller.settings.app_mode = args.mode
    win = MainWindow(controller)
    win.show()
    if args.simulator:
        controller.connect_simulator_async(None, args.scenario)
    if args.ttbin:
        win.open_in_analysis(args.ttbin)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
