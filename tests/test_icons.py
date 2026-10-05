import os

import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtWidgets

from openrevu import icons
from openrevu.gui import TOOL_GROUPS


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_every_mapped_icon_file_is_bundled():
    missing = [f for f in icons.FILES.values() if not os.path.exists(os.path.join(icons.SVG_DIR, f + ".svg"))]
    assert missing == []


def test_licence_text_ships_with_the_icons():
    text = open(os.path.join(icons.SVG_DIR, "LICENSE-lucide.txt"), encoding="utf-8").read()
    assert "ISC License" in text and "Lucide" in text


def test_no_unused_svg_files_are_bundled():
    on_disk = {f[:-4] for f in os.listdir(icons.SVG_DIR) if f.endswith(".svg")}
    assert on_disk == set(icons.FILES.values())


def test_every_tool_has_a_real_icon():
    tools = ["Select", "Pan"] + [t for names in TOOL_GROUPS.values() for t in names]
    for name in tools:
        assert icons.has_icon(name), f"no icon for tool {name}"
        pm = icons.tool_icon(name).pixmap(24, 24)
        assert not pm.isNull()
        img = pm.toImage()
        opaque = sum(1 for x in range(0, img.width()) for y in range(img.height()) if img.pixelColor(x, y).alpha() > 0)
        assert opaque > 20, f"icon {name} drew nothing"


def test_unknown_icon_name_gives_a_visible_fallback_not_an_empty_button():
    img = icons.tool_icon("no-such-icon").pixmap(24, 24).toImage()
    assert any(img.pixelColor(x, y).alpha() > 0 for x in range(24) for y in range(24))


def test_measure_tools_are_tinted_blue_and_others_dark():
    def dominant(name):
        img = icons.tool_icon(name).pixmap(24, 24).toImage()
        px = [img.pixelColor(x, y) for x in range(24) for y in range(24) if img.pixelColor(x, y).alpha() > 200]
        return max(set((c.red() // 40, c.green() // 40, c.blue() // 40) for c in px), key=lambda k: sum(1 for c in px if (c.red() // 40, c.green() // 40, c.blue() // 40) == k))
    assert dominant("Length")[2] > dominant("Length")[0]      # blue channel dominates
    assert dominant("Rectangle")[0] <= 1 and dominant("Rectangle")[2] <= 1  # dark grey


def test_stroke_width_is_normalised_and_colour_replaced():
    svg = icons._svg_for("Stamp", "#ff0000")
    assert 'stroke="#ff0000"' in svg and "currentColor" not in svg and f'stroke-width="{icons.STROKE}"' in svg


def test_icons_are_declared_as_package_data():
    tomllib = pytest.importorskip("tomllib")
    import pathlib
    meta = tomllib.loads((pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())
    assert "icons_svg/*.svg" in meta["tool"]["setuptools"]["package-data"]["openrevu"]
