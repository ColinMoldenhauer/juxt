"""Render the value picker for the docs: docs/assets/screenshots/value-picker.png.

Runs offscreen at 2x (like the other screenshots), in the dark theme, on the
example data in examples/sample_plots. The picker shows a session started
with some values pinned, so the values found on disk but not loaded appear
unticked in the accent colour, and one of them is ticked to show the
"+N  -M" summary.

    python scripts/screenshot_value_picker.py [OUT.png]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from juxt import theme  # noqa: E402
from juxt.detect import _axes_from_local_template  # noqa: E402
from juxt.dialogs import ValuesDialog  # noqa: E402

TEMPLATE = (ROOT / "examples" / "sample_plots").as_posix() + "/{sensor}_{date}_{overpass}_{source}.png"
# What the session was started with (as with `--sensor ASCAT SMAP --source L2`).
LOADED = {"sensor": ["ASCAT", "SMAP"], "source": ["L2"]}
# Ticked in the picker before the screenshot: one value added, one removed.
TICK = {"sensor": ["SMOS"]}
UNTICK = {"date": ["2024-03-16"]}


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "docs" / "assets" / "screenshots" / "value-picker.png")
    app = QApplication([])
    theme.apply("dark")

    _template, found = _axes_from_local_template(TEMPLATE)
    current = {name: LOADED.get(name, vals) for name, vals in found.items()}
    dlg = ValuesDialog(current, found)
    for name, values in TICK.items():
        _set(dlg, name, values, Qt.CheckState.Checked)
    for name, values in UNTICK.items():
        _set(dlg, name, values, Qt.CheckState.Unchecked)

    dlg.show()
    _settle(app)
    # The offscreen screen is small, and the dialogue caps itself at 90% of
    # it: widen it until every column fits without the sideways scrollbar.
    from PySide6.QtWidgets import QScrollArea
    bar = dlg.findChild(QScrollArea).horizontalScrollBar()
    while bar.maximum() > 0:
        dlg.resize(dlg.width() + 10, dlg.height())
        _settle(app)
    dlg.findChild(QScrollArea).setFocus()   # no text cursor in a filter field
    _settle(app)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not dlg.grab().save(str(out)):
        sys.exit(f"could not write {out}")
    print(out)


def _settle(app: QApplication) -> None:
    for _ in range(5):
        app.processEvents()


def _set(dlg: ValuesDialog, axis: str, values: list[str], state: Qt.CheckState) -> None:
    lst = dlg.panel(axis).value_list
    for i in range(lst.count()):
        item = lst.item(i)
        if item.data(Qt.ItemDataRole.UserRole) in values:
            item.setCheckState(state)


if __name__ == "__main__":
    main()
