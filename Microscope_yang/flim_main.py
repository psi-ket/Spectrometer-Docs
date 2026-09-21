"""
flim_main.py — Application entry point for the FLIM Scanner GUI.

Usage
-----
    python flim_main.py
"""

import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPalette, QColor, QFont
from PyQt5.QtCore import Qt
from flim_window import FLIMWindow, STYLESHEET, BG_DARK, TEXT_PRIMARY, TEXT_SECONDARY, ACCENT


def build_dark_palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(BG_DARK))
    pal.setColor(QPalette.WindowText,      QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.Base,            QColor("#1a1d28"))
    pal.setColor(QPalette.AlternateBase,   QColor("#1e2230"))
    pal.setColor(QPalette.ToolTipBase,     QColor("#1e2230"))
    pal.setColor(QPalette.ToolTipText,     QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.Text,            QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.Button,          QColor("#262a38"))
    pal.setColor(QPalette.ButtonText,      QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.BrightText,      QColor("#ffffff"))
    pal.setColor(QPalette.Link,            QColor(ACCENT))
    pal.setColor(QPalette.Highlight,       QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Disabled, QPalette.Text,       QColor(TEXT_SECONDARY))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_SECONDARY))
    return pal


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("FLIM Scanner")
    app.setStyle("Fusion")
    app.setPalette(build_dark_palette())
    app.setStyleSheet(STYLESHEET)

    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    win = FLIMWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
