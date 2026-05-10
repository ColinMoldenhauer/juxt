from __future__ import annotations
import sys
from itertools import product

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog

from .config import load_config
from .loader import preload
from .viewer import MainWindow


def main():
    if len(sys.argv) < 2:
        print("Usage: juxt <config.yaml>")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("juxt")

    try:
        config = load_config(sys.argv[1])
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)

    total = 1
    for vs in config.axes.values():
        total *= len(vs)

    progress = QProgressDialog("Loading images…", None, 0, total)
    progress.setWindowTitle("juxt")
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)

    def on_progress(i: int, n: int):
        progress.setValue(i)
        app.processEvents()

    pixmaps = preload(config.template, config.axes, on_progress)
    progress.close()

    window = MainWindow(config, pixmaps)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
