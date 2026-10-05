"""Icons for tools and actions.

Almost all icons come from the Lucide set (ISC licence, bundled in icons_svg/ with its licence text).
Squiggly and Redact have no Lucide match, so they are drawn here with the same stroke style.
Icons are drawn from SVG text, so they stay sharp at any size and any colour."""
from __future__ import annotations

import os
import re

from PyQt5 import QtCore, QtGui, QtSvg

SVG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons_svg")
INK = "#2f3640"
BLUE = "#1f6feb"
STROKE = 1.8  # a little lighter than Lucide's default of 2, so small icons look clean

# icon name -> Lucide file (without .svg). Tool names match the tool names used by the canvas.
FILES = {
    # tools
    "Select": "mouse-pointer-2", "Pan": "hand", "Rectangle": "rectangle-horizontal", "Ellipse": "circle",
    "Line": "slash", "Arrow": "move-up-right", "Polyline": "waypoints", "Cloud": "cloud", "Pen": "pen",
    "Highlight": "highlighter", "Underline": "underline", "Strikeout": "strikethrough", "Text": "type",
    "Callout": "message-square-text", "Note": "sticky-note", "Stamp": "stamp", "Signature": "signature",
    "Calibrate": "ruler", "Length": "ruler-dimension-line", "Perimeter": "hexagon", "Area": "pentagon",
    "Fill": "paint-bucket", "RectArea": "square-dashed", "EllipseArea": "circle-dashed", "Volume": "box",
    "Diameter": "diameter", "Angle": "triangle-right", "Count": "map-pin", "Viewport": "fullscreen",
    # actions and panels
    "open": "folder-open", "save": "save", "undo": "undo-2", "redo": "redo-2", "zoom-in": "zoom-in",
    "zoom-out": "zoom-out", "fit-width": "move-horizontal", "fit-page": "maximize", "page-up": "chevron-up",
    "page-down": "chevron-down", "print": "printer", "search": "search", "delete": "trash", "copy": "copy",
    "duplicate": "copy-plus", "bookmark": "bookmark", "thumbnails": "gallery-thumbnails", "layers": "layers",
    "properties": "sliders-horizontal", "toolchest": "wrench", "measure": "ruler", "images": "images",
    "add": "plus", "close": "x", "keep": "pin", "reply": "reply", "auto": "wand-sparkles", "line-colour": "pen-line",
    "fill-colour": "paint-bucket", "sheets": "file-stack",
}
MEASURE_TOOLS = {"Calibrate", "Length", "Perimeter", "Area", "Fill", "RectArea", "EllipseArea", "Volume",
                 "Diameter", "Angle", "Count", "Viewport"}
_cache: dict = {}


def _svg_for(name: str, color: str) -> str | None:
    """SVG text for an icon, recoloured and with a uniform stroke width. None if the name is unknown."""
    file = FILES.get(name)
    if file is None:
        return None
    path = os.path.join(SVG_DIR, file + ".svg")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    text = text.replace("currentColor", color)
    return re.sub(r'stroke-width="[^"]*"', f'stroke-width="{STROKE}"', text, count=1)


def _custom_svg(name: str, color: str) -> str | None:
    """Icons that Lucide does not have, in the same style."""
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">')
    if name == "Squiggly":  # the letter A with a wavy line below it
        return head + ('<path d="M7 15 L12 4 L17 15 M8.8 11 H15.2"/>'
                       '<path d="M3 20 q1.5 -2 3 0 t3 0 t3 0 t3 0 t3 0 t3 0"/></svg>')
    if name == "Redact":  # a black bar over two text lines
        return head + ('<path d="M4 6 H20 M4 18 H14"/><rect x="3" y="9.5" width="18" height="5" rx="1" '
                       f'fill="{color}"/></svg>')
    return None


def _render(svg: str, px: int) -> QtGui.QPixmap:
    pm = QtGui.QPixmap(px, px)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8"))).render(p, QtCore.QRectF(0, 0, px, px))
    p.end()
    return pm


def tool_icon(name: str, size: int = 24, color: str | None = None) -> QtGui.QIcon:
    """Icon for a tool or action name. Measure tools are blue, everything else dark grey."""
    color = color or (BLUE if name in MEASURE_TOOLS else INK)
    key = (name, size, color)
    if key not in _cache:
        svg = _svg_for(name, color) or _custom_svg(name, color)
        icon = QtGui.QIcon()
        if svg is None:  # unknown name: a neutral dot, never an empty button
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3" '
                   f'fill="{color}"/></svg>')
        for scale in (1, 2):
            icon.addPixmap(_render(svg, size * scale))
        _cache[key] = icon
    return _cache[key]


def has_icon(name: str) -> bool:
    return _svg_for(name, INK) is not None or _custom_svg(name, INK) is not None
