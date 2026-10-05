"""Qt-free core: PDF markup, scale calibration and takeoff measurements."""
from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass

import fitz  # PyMuPDF

TAG = "OpenRevu"
UNITS = {"mm": 1 / 25.4, "cm": 1 / 2.54, "m": 1 / 0.0254, "in": 1.0, "ft": 1 / 12.0}
PT_PER_IN = 72.0


@dataclass
class Scale:
    """Maps PDF points to real-world units. unit_per_pt * points = real length."""
    unit: str = "ft"
    unit_per_pt: float = 1 / PT_PER_IN / 12  # default: 1 pt = 1/72 in paper == 1:1

    @classmethod
    def from_calibration(cls, p1, p2, real_length: float, unit: str) -> "Scale":
        d = math.dist(p1, p2)
        if d <= 0 or real_length <= 0:
            raise ValueError("calibration needs two distinct points and a positive length")
        if unit not in UNITS:
            raise ValueError(f"unknown unit {unit!r}")
        return cls(unit, real_length / d)

    def length(self, pts) -> float:
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) * self.unit_per_pt

    def area(self, pts) -> float:
        return polygon_area(pts) * self.unit_per_pt ** 2

    def to_json(self) -> str:
        return json.dumps({"unit": self.unit, "unit_per_pt": self.unit_per_pt})

    @classmethod
    def from_json(cls, s: str) -> "Scale":
        d = json.loads(s)
        return cls(d["unit"], float(d["unit_per_pt"]))


def polygon_area(pts) -> float:
    """Shoelace formula, absolute value."""
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def cloud_points(rect: fitz.Rect, arc: float = 14.0, steps: int = 6):
    """Closed polyline of outward scallops around rect (revision cloud)."""
    r = fitz.Rect(rect).normalize()
    corners = [r.tl, r.tr, r.br, r.bl, r.tl]
    pts = []
    for a, b in zip(corners, corners[1:]):
        length = math.dist(a, b)
        n = max(1, round(length / arc))
        dx, dy = (b.x - a.x) / n, (b.y - a.y) / n
        nx, ny = dy, -dx  # outward for clockwise tl->tr->br->bl
        for i in range(n):
            sx, sy = a.x + dx * i, a.y + dy * i
            for k in range(steps):
                t = k / steps
                bulge = math.sin(math.pi * t) * 0.35
                pts.append((sx + dx * t + nx * bulge, sy + dy * t + ny * bulge))
    pts.append(pts[0])
    return pts


class Markup:
    """Wraps a PDF annotation with an OpenRevu measurement/label payload."""

    def __init__(self, annot: fitz.Annot, page_no: int, page: fitz.Page):
        self.annot, self.page_no, self._page = annot, page_no, page  # keep page alive

    @property
    def kind(self) -> str:
        return self.annot.type[1]

    @property
    def subject(self) -> str:
        return self.annot.info.get("subject", "")

    @property
    def comment(self) -> str:
        return self.annot.info.get("content", "")

    def measurement(self):
        """Return (kind, value, unit) if this is a measurement markup, else None."""
        c = self.comment
        if c.startswith(TAG + ":"):
            _, kind, val, unit = c.split(":", 3)
            return kind, float(val), unit
        return None


class Document:
    def __init__(self, path: str | None = None):
        self.doc = fitz.open(path) if path else fitz.open()
        self.path = path
        self.scale = self._load_scale()

    # --- scale persistence (stored in PDF keywords) ---
    def _load_scale(self) -> Scale:
        kw = (self.doc.metadata or {}).get("keywords") or ""
        for part in kw.split(";"):
            if part.startswith(TAG + "-scale="):
                try:
                    return Scale.from_json(part.split("=", 1)[1])
                except (ValueError, KeyError):
                    pass
        return Scale()

    def set_scale(self, scale: Scale):
        self.scale = scale

    def _store_scale(self):
        md = dict(self.doc.metadata or {})
        kw = [p for p in (md.get("keywords") or "").split(";") if p and not p.startswith(TAG + "-scale=")]
        kw.append(f"{TAG}-scale={self.scale.to_json()}")
        md["keywords"] = ";".join(kw)
        self.doc.set_metadata(md)

    @property
    def page_count(self) -> int:
        return len(self.doc)

    # --- markup creation ---
    def _finish(self, a: fitz.Annot, color, width, subject, content="", fill=None, opacity=1.0):
        a.set_colors(stroke=color, fill=fill)
        a.set_border(width=width)
        a.set_opacity(opacity)
        a.set_info(title=TAG, subject=subject, content=content)
        a.update()
        return a

    def add_rect(self, pno, rect, color=(1, 0, 0), width=1.5, fill=None):
        return self._finish(self.doc[pno].add_rect_annot(rect), color, width, "Rectangle", fill=fill)

    def add_ellipse(self, pno, rect, color=(1, 0, 0), width=1.5):
        return self._finish(self.doc[pno].add_circle_annot(rect), color, width, "Ellipse")

    def add_line(self, pno, p1, p2, color=(1, 0, 0), width=1.5, arrow=False):
        a = self.doc[pno].add_line_annot(p1, p2)
        if arrow:
            a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
        return self._finish(a, color, width, "Arrow" if arrow else "Line")

    def add_freehand(self, pno, pts, color=(1, 0, 0), width=2):
        a = self.doc[pno].add_ink_annot([list(pts)])
        return self._finish(a, color, width, "Pen")

    def add_cloud(self, pno, rect, color=(1, 0, 0), width=1.5):
        a = self.doc[pno].add_polyline_annot(cloud_points(rect))
        return self._finish(a, color, width, "Cloud")

    def add_highlight(self, pno, rect, color=(1, 1, 0)):
        page = self.doc[pno]
        words = [w for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(rect)]
        a = page.add_highlight_annot(quads=[fitz.Rect(w[:4]).quad for w in words]) if words \
            else page.add_highlight_annot(rect)
        a.set_colors(stroke=color)
        a.set_info(title=TAG, subject="Highlight")
        a.update()
        return a

    def add_text(self, pno, rect, text, color=(1, 0, 0), fontsize=12):
        a = self.doc[pno].add_freetext_annot(rect, text, fontsize=fontsize, text_color=color,
                                              fill_color=(1, 1, 1))
        a.set_info(title=TAG, subject="Text")
        a.update()
        return a

    def add_note(self, pno, point, text):
        a = self.doc[pno].add_text_annot(point, text)
        a.set_info(title=TAG, subject="Note", content=text)
        a.update()
        return a

    # --- measurements (takeoff) ---
    def add_length(self, pno, pts, color=(0, 0.4, 1), label=True):
        v = self.scale.length(pts)
        a = self.doc[pno].add_polyline_annot(pts) if len(pts) > 2 else self.doc[pno].add_line_annot(*pts)
        self._finish(a, color, 1.5, "Length", f"{TAG}:length:{v:.6f}:{self.scale.unit}")
        if label:
            self._label(pno, pts[len(pts) // 2], f"{v:.2f} {self.scale.unit}", color)
        return a, v

    def add_area(self, pno, pts, color=(0, 0.6, 0.2), label=True):
        v = self.scale.area(pts)
        a = self.doc[pno].add_polygon_annot(pts)
        self._finish(a, color, 1.5, "Area", f"{TAG}:area:{v:.6f}:{self.scale.unit}", fill=color, opacity=0.25)
        if label:
            c = fitz.Point(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            self._label(pno, c, f"{v:.2f} {self.scale.unit}²", color)
        return a, v

    def add_count(self, pno, point, color=(0.8, 0, 0.8)):
        r = fitz.Rect(point[0] - 5, point[1] - 5, point[0] + 5, point[1] + 5)
        a = self.doc[pno].add_circle_annot(r)
        self._finish(a, color, 1.5, "Count", f"{TAG}:count:1:ea", fill=color)
        return a

    def _label(self, pno, center, text, color):
        w = 7 * len(text) + 6
        r = fitz.Rect(center[0] - w / 2, center[1] - 8, center[0] + w / 2, center[1] + 8)
        a = self.doc[pno].add_freetext_annot(r, text, fontsize=10, text_color=color, fill_color=(1, 1, 1))
        a.set_info(title=TAG, subject="Label")
        a.update()

    # --- querying / editing ---
    def markups(self):
        out = []
        for pno, page in enumerate(self.doc):
            for a in page.annots() or []:
                out.append(Markup(a, pno, page))
        return out

    def delete(self, markup: Markup):
        self.doc[markup.page_no].delete_annot(markup.annot)

    def takeoff(self):
        """Aggregate measurements: {(kind, unit): total}."""
        totals: dict = {}
        for m in self.markups():
            r = m.measurement()
            if r:
                kind, val, unit = r
                totals[(kind, unit)] = totals.get((kind, unit), 0.0) + val
        return totals

    def export_csv(self, path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["page", "type", "subject", "comment", "value", "unit"])
            for m in self.markups():
                r = m.measurement()
                w.writerow([m.page_no + 1, m.kind, m.subject, "" if r else m.comment,
                            f"{r[1]:.4f}" if r else "", r[2] if r else ""])

    def render(self, pno, zoom=1.0) -> fitz.Pixmap:
        return self.doc[pno].get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True)

    def save(self, path=None):
        self._store_scale()
        path = path or self.path
        if path is None:
            raise ValueError("no path")
        if path == self.path:
            # PyMuPDF cannot overwrite the open file with a full save; write aside and swap.
            tmp = path + ".tmp"
            self.doc.save(tmp, garbage=3, deflate=True)
            self.doc.close()
            os.replace(tmp, path)
            self.doc = fitz.open(path)
        else:
            self.doc.save(path, garbage=3, deflate=True)
            self.path = path
