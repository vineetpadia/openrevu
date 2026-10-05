"""Qt-free core: PDF markup, scale calibration, takeoff, undo/redo."""
from __future__ import annotations

import csv
import getpass
import json
import math
import os
from dataclasses import dataclass

import fitz  # PyMuPDF

from ._util import mutates as _mutates
from .pages import PageOps

TAG = "OpenRevu"
UNITS = {"mm": 1 / 25.4, "cm": 1 / 2.54, "m": 1 / 0.0254, "in": 1.0, "ft": 1 / 12.0, "yd": 1 / 36.0}
STATUSES = ["", "Accepted", "Rejected", "Cancelled", "Completed", "Reviewed"]
MEASURE_KEY, STATUS_KEY, PARENT_KEY = "OR_Measure", "OR_Status", "OR_Parent"
VERTEX_TYPES = {"Line", "PolyLine", "Polygon", "Ink"}
RECT_TYPES = {"Square", "Circle", "FreeText", "Text", "Stamp"}
TEXT_MARKUP_TYPES = {"Highlight", "Underline", "StrikeOut", "Squiggly"}  # move-only (quad points)
DEPTH_KEY = "OR_Depth"
RESIZABLE_MEASURES = {"length", "perimeter", "area", "volume", "diameter"}


@dataclass
class Scale:
    """Maps PDF points to real-world units: real = unit_per_pt * points."""
    unit: str = "ft"
    unit_per_pt: float = 1 / 72 / 12  # 1 pt == 1/72 in, i.e. 1:1 paper

    @classmethod
    def from_calibration(cls, p1, p2, real_length: float, unit: str) -> "Scale":
        d = math.dist(p1, p2)
        if d <= 0 or real_length <= 0:
            raise ValueError("calibration needs two distinct points and a positive length")
        if unit not in UNITS:
            raise ValueError(f"unknown unit {unit!r}")
        return cls(unit, real_length / d)

    @classmethod
    def from_ratio(cls, paper_in: float, real: float, unit: str) -> "Scale":
        """e.g. 1/4 in on paper = 1 ft real: from_ratio(0.25, 1, 'ft')."""
        if paper_in <= 0 or real <= 0 or unit not in UNITS:
            raise ValueError("invalid ratio")
        return cls(unit, real / (paper_in * 72))

    def length(self, pts) -> float:
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) * self.unit_per_pt

    def area(self, pts) -> float:
        return polygon_area(pts) * self.unit_per_pt ** 2

    def to_dict(self):
        return {"unit": self.unit, "unit_per_pt": self.unit_per_pt}

    @classmethod
    def from_dict(cls, d) -> "Scale":
        return cls(d["unit"], float(d["unit_per_pt"]))

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, s: str) -> "Scale":
        return cls.from_dict(json.loads(s))


def polygon_area(pts) -> float:
    """Shoelace formula, absolute value."""
    pts = list(pts)
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def angle_deg(a, b, c) -> float:
    """Angle at vertex b between rays b->a and b->c, degrees in [0, 180]."""
    v1, v2 = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    n = math.hypot(*v1) * math.hypot(*v2)
    if n == 0:
        raise ValueError("degenerate angle")
    return math.degrees(math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / n))))


def cloud_points(rect: fitz.Rect, arc: float = 14.0, steps: int = 6):
    """Closed polyline of outward scallops around rect (revision cloud)."""
    r = fitz.Rect(rect).normalize()
    corners = [r.tl, r.tr, r.br, r.bl, r.tl]
    pts = []
    for a, b in zip(corners, corners[1:]):
        n = max(1, round(math.dist(a, b) / arc))
        dx, dy = (b.x - a.x) / n, (b.y - a.y) / n
        nx, ny = dy, -dx
        for i in range(n):
            sx, sy = a.x + dx * i, a.y + dy * i
            for k in range(steps):
                t = k / steps
                bulge = math.sin(math.pi * t) * 0.35
                pts.append((sx + dx * t + nx * bulge, sy + dy * t + ny * bulge))
    pts.append(pts[0])
    return pts


def format_value(kind: str, val: float, unit: str) -> str:
    """Human-readable measurement, e.g. 'area' 12.5 'ft' -> '12.50 ft²'."""
    suffix = {"area": "²", "volume": "³"}.get(kind, "")
    if kind == "angle":
        return f"{val:.1f}°"
    if kind == "count":
        return f"{val:g} ea"
    return f"{val:.2f} {unit}{suffix}"


def _xget(doc, xref, key):
    t, v = doc.xref_get_key(xref, key)
    return None if t == "null" else v


class Markup:
    """A PDF annotation plus OpenRevu metadata (measurement, status, replies)."""

    def __init__(self, annot: fitz.Annot, page_no: int, page: fitz.Page, d: "Document"):
        self._annot, self.page_no, self._page, self.d = annot, page_no, page, d  # keep page alive
        self._gen = d.generation
        self._xref = annot.xref

    @property
    def annot(self) -> fitz.Annot:
        if self._gen != self.d.generation:  # underlying document was reopened (undo/redo): avoid a native crash
            raise RuntimeError("stale markup: re-fetch it from Document.markups()")
        return self._annot

    @property
    def xref(self) -> int:
        return self._xref

    @property
    def kind(self) -> str:
        return self.annot.type[1]

    @property
    def subject(self) -> str:
        return self.annot.info.get("subject", "")

    @property
    def comment(self) -> str:
        return self.annot.info.get("content", "")

    @property
    def author(self) -> str:
        return self.annot.info.get("title", "")

    @property
    def date(self) -> str:
        return self.annot.info.get("modDate", "") or self.annot.info.get("creationDate", "")

    @property
    def rect(self) -> fitz.Rect:
        """Bounding box in visual page coordinates."""
        return self.d._rv(self.page_no, self.annot.rect)

    @property
    def status(self) -> str:
        v = _xget(self.d.doc, self.xref, STATUS_KEY)
        return v or ""

    def measurement(self):
        """(kind, value, unit) if this is a measurement markup, else None."""
        v = _xget(self.d.doc, self.xref, MEASURE_KEY)
        if not v:
            return None
        kind, val, unit = v.split(":", 2)
        return kind, float(val), unit

    def replies(self):
        out = []
        for a in self._page.annots() or []:
            if _xget(self.d.doc, a.xref, "IRT") == f"{self.xref} 0 R":
                out.append((a.info.get("title", ""), a.info.get("content", "")))
        return out

    # --- editing (each checkpoints for undo) ---
    def _edit(self):
        self.d.checkpoint()

    def set_colors(self, stroke=None, fill=None):
        self._edit()
        kw = {}
        if stroke is not None:
            kw["stroke"] = stroke
        if fill is not None:
            kw["fill"] = fill
        self.annot.set_colors(**kw)
        self.annot.update()

    def set_width(self, w):
        self._edit()
        self.annot.set_border(width=w)
        self.annot.update()

    def set_opacity(self, o):
        self._edit()
        self.annot.set_opacity(o)
        self.annot.update()

    def set_subject(self, s):
        self._edit()
        self.annot.set_info(subject=s)
        self.annot.update()

    def set_comment(self, c):
        self._edit()
        self.annot.set_info(content=c)
        self.annot.update()

    def set_status(self, s):
        if s not in STATUSES:
            raise ValueError(f"unknown status {s!r}")
        self._edit()
        if s:
            self.d.doc.xref_set_key(self.xref, STATUS_KEY, f"({s})")
        else:
            self.d.doc.xref_set_key(self.xref, STATUS_KEY, "null")

    def _children(self):
        return [a for a in self._page.annots() or []
                if _xget(self.d.doc, a.xref, PARENT_KEY) == str(self.xref)]

    def points(self):
        """Geometry in visual coordinates: list of points; for Ink, list of strokes."""
        v, pn = self.annot.vertices, self.page_no
        if self.kind == "Ink":
            return [[tuple(self.d._tv(pn, p)) for p in st] for st in v]
        return [tuple(self.d._tv(pn, p)) for p in v or []]

    def _check_transformable(self, resize=False):
        k = self.kind
        if k in TEXT_MARKUP_TYPES:
            if resize:
                raise ValueError("text markups follow their text; move them instead of resizing")
        elif k not in VERTEX_TYPES and k not in RECT_TYPES:
            raise ValueError(f"cannot move or resize {k} markups")

    def move(self, dx, dy):
        """Move by (dx, dy) in visual coordinates."""
        self._check_transformable()
        new = self.rect + (dx, dy, dx, dy)
        self._edit()
        self._apply(self.d._ru(self.page_no, new))

    def resize(self, new_rect):
        """Resize to new_rect (visual coordinates). Measurements are re-measured."""
        self._check_transformable(resize=True)
        m = self.measurement()
        if m and (m[0] not in RESIZABLE_MEASURES or any(c.subject == "Cutout" for c in self._children_markups())):
            raise ValueError("this measurement cannot be resized; delete and re-measure it")
        old = self.annot.rect
        if old.width == 0 or old.height == 0:
            raise ValueError("degenerate markup")
        self._edit()
        self._apply(self.d._ru(self.page_no, new_rect))
        if m:
            self._remeasure(m[0])

    def _children_markups(self):
        return [Markup(c, self.page_no, self._page, self.d) for c in self._children()]

    def _rd(self):
        rd = _xget(self.d.doc, self.xref, "RD")
        return tuple(float(v) for v in rd.strip("[]").split()) if rd else (0.0, 0.0, 0.0, 0.0)

    def _remeasure(self, kind):
        """Recompute the stored value (and relabel) after the geometry changed."""
        d, pno = self.d, self.page_no
        sc = d.scale_at(pno, [self.rect.tl, self.rect.br])
        if self.kind == "Circle":
            l, t, r_, b_ = self._rd()
            vr = self.rect
            w, h = vr.width - l - r_, vr.height - t - b_
            val = sc.unit_per_pt * w if kind == "diameter" else 3.141592653589793 / 4 * w * h * sc.unit_per_pt ** 2
            center = ((vr.x0 + vr.x1) / 2, (vr.y0 + vr.y1) / 2)
        else:
            pts = self.points()
            center = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            if kind == "length":
                val = sc.length(pts)
                center = pts[len(pts) // 2] if len(pts) > 2 else center
            elif kind == "perimeter":
                val = sc.length(pts + pts[:1])
            else:
                val = sc.area(pts)
                if kind == "volume":
                    val *= float(_xget(d.doc, self.xref, DEPTH_KEY) or 1)
        unit = self.measurement()[2]
        d.doc.xref_set_key(self.xref, MEASURE_KEY, f"({kind}:{val:.10g}:{unit})")
        had_label = bool(self._children())
        for c in self._children():
            self._page.delete_annot(c)
        if had_label:
            d._label(pno, center, format_value(kind, val, unit) if kind != "diameter" else f"Ø {val:.2f} {unit}", (0, 0.4, 1), self)

    def _apply(self, new_u: fitz.Rect, children=True):
        """Map the annotation's current (API-space) bbox onto new_u, scaling vertices accordingly."""
        a, page, doc = self.annot, self._page, self.d.doc
        old, outer_old = a.rect, a.rect
        if self.kind in TEXT_MARKUP_TYPES:
            rawq = _xget(doc, self.xref, "QuadPoints")
            nums = [float(v) for v in rawq.strip("[]").split()]
            ddx, ddy = new_u.x0 - old.x0, -(new_u.y0 - old.y0)  # PDF y axis points up
            moved = [v + (ddx if i % 2 == 0 else ddy) for i, v in enumerate(nums)]
            doc.xref_set_key(self.xref, "QuadPoints", "[" + " ".join(f"{v:g}" for v in moved) + "]")
            a.update()
            return
        if self.kind in VERTEX_TYPES:
            # a.rect is the vertex bbox plus stroke padding: scale the vertices' own bbox, keep the padding
            pts = [p for st in a.vertices for p in st] if self.kind == "Ink" else a.vertices
            vb = fitz.Rect(min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
            pad = (vb.x0 - old.x0, vb.y0 - old.y0, old.x1 - vb.x1, old.y1 - vb.y1)
            new_vb = fitz.Rect(new_u.x0 + pad[0], new_u.y0 + pad[1], new_u.x1 - pad[2], new_u.y1 - pad[3])
            old, new_u = vb, new_vb
        sx = new_u.width / old.width if old.width else 1.0
        sy = new_u.height / old.height if old.height else 1.0
        f = lambda p: (new_u.x0 + (p[0] - old.x0) * sx, new_u.y0 + (p[1] - old.y0) * sy)  # noqa: E731
        raw = lambda pts: " ".join(f"{x + page.cropbox.x0:g} {page.mediabox.height - (y + page.cropbox.y0):g}"  # noqa: E731
                                   for x, y in pts)
        if self.kind in VERTEX_TYPES:
            v = a.vertices
            if self.kind == "Ink":
                doc.xref_set_key(self.xref, "InkList", "[" + "".join("[" + raw([f(p) for p in st]) + "]" for st in v) + "]")
            elif self.kind == "Line":
                doc.xref_set_key(self.xref, "L", "[" + raw([f(p) for p in v]) + "]")
            else:
                doc.xref_set_key(self.xref, "Vertices", "[" + raw([f(p) for p in v]) + "]")
        else:
            if self.kind in ("Square", "Circle"):  # annot.rect includes the /RD border padding; set_rect wants the inner rect
                l, t, r_, b_ = self._rd()
                new_u = fitz.Rect(new_u.x0 + l, new_u.y0 + t, new_u.x1 - r_, new_u.y1 - b_)
            a.set_rect(new_u)
        a.update()
        if children and (sx, sy) == (1.0, 1.0):
            dxu, dyu = new_u.x0 - old.x0, new_u.y0 - old.y0  # translation is identical for padded and inner boxes
            for c in self._children():
                m = Markup(c, self.page_no, page, self.d)
                m._apply(fitz.Rect(c.rect.x0 + dxu, c.rect.y0 + dyu, c.rect.x1 + dxu, c.rect.y1 + dyu), children=False)

    def to_dict(self):
        """Serialisable description (for tool chest / copy-paste)."""
        a = self.annot
        c = a.colors
        pts = self.points()
        return {
            "kind": self.kind, "subject": self.subject, "comment": self.comment,
            "stroke": c.get("stroke"), "fill": c.get("fill"), "width": a.border.get("width", 1),
            "opacity": a.opacity, "rect": list(self.rect),
            "vertices": [[list(p) for p in st] for st in pts] if self.kind == "Ink"
            else ([list(p) for p in pts] if pts else None),
            "arrow": bool(a.line_ends and a.line_ends[1] == fitz.PDF_ANNOT_LE_CLOSED_ARROW)
            if self.kind == "Line" else False,
        }


class Document(PageOps):

    def __init__(self, path: str | None = None, password: str | None = None):
        self.doc = fitz.open(path) if path else fitz.open()
        encrypted = bool(self.doc.needs_pass)
        if encrypted and not (password and self.doc.authenticate(password)):
            raise PermissionError("password required or incorrect")
        self.path = path
        self._password = password if encrypted else None  # re-applied on save so protection is never silently dropped
        self.author = getpass.getuser()
        self._depth = 0
        self.generation = 0
        self._pcache: dict[int, fitz.Page] = {}
        self.viewports: list = []
        self._undo: list[bytes] = []
        self._redo: list[bytes] = []
        self.modified = False
        self.default_scale, self.page_scales = self._load_scales()

    def _page(self, pno: int) -> fitz.Page:
        """Page object kept alive (annotations returned by add_* are only valid while their page is)."""
        pg = self._pcache.get(pno)
        if pg is None:
            pg = self._pcache[pno] = self.doc[pno]
        return pg

    def _invalidate_pages(self):
        """Call after anything that renumbers/replaces pages or reopens the document."""
        self._pcache = {}
        self.generation += 1

    # --- coordinate spaces ---
    # PyMuPDF annotation/text APIs use unrotated, cropbox-origin, y-down coordinates; the UI and the public
    # OpenRevu API use *visual* coordinates (what is drawn). These convert at the boundary.
    def _tu(self, pno, p):
        return fitz.Point(p) * self._page(pno).derotation_matrix

    def _tv(self, pno, p):
        return fitz.Point(p) * self._page(pno).rotation_matrix

    def _tus(self, pno, pts):
        return [tuple(self._tu(pno, p)) for p in pts]

    def _ru(self, pno, r):
        return fitz.Rect(r).normalize() * self._page(pno).derotation_matrix

    def _rv(self, pno, r):
        return fitz.Rect(r) * self._page(pno).rotation_matrix

    # --- undo / redo ---
    MAX_UNDO = 30

    def checkpoint(self):
        self._store_scales()
        self._undo.append(self._dump())
        del self._undo[:-self.MAX_UNDO]
        self._redo = []
        self.modified = True

    def _reopen(self, data: bytes):
        self._invalidate_pages()
        self.doc.close()
        self.doc = fitz.open("pdf", data)
        if self.doc.needs_pass:
            self.doc.authenticate(self._password)
        self.default_scale, self.page_scales = self._load_scales()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._reopen(self._undo.pop())
        self.modified = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._reopen(self._redo.pop())
        self.modified = True
        return True

    def _snapshot(self) -> bytes:
        self._store_scales()
        return self._dump()

    def _dump(self) -> bytes:
        """Serialize for the undo history; protected documents never leave plaintext copies in memory."""
        if self._password:
            return self.doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=self._password,
                                    owner_pw=self._password + "-owner", permissions=-1)
        return self.doc.tobytes()

    # --- scales (default + per page), persisted in PDF keywords ---
    def _load_scales(self):
        self.viewports = []
        kw = (self.doc.metadata or {}).get("keywords") or ""
        for part in kw.split(";"):
            if part.startswith(TAG + "-scales="):
                try:
                    d = json.loads(part.split("=", 1)[1])
                    self.viewports = [(int(v["page"]), fitz.Rect(v["rect"]), Scale.from_dict(v["scale"]))
                                      for v in d.get("viewports", [])]
                    return Scale.from_dict(d["default"]), {int(k): Scale.from_dict(v) for k, v in d["pages"].items()}
                except (ValueError, KeyError, TypeError):
                    pass
        return Scale(), {}

    def _store_scales(self):
        md = dict(self.doc.metadata or {})
        kw = [p for p in (md.get("keywords") or "").split(";") if p and not p.startswith(TAG + "-scale")]
        blob = {"default": self.default_scale.to_dict(), "pages": {str(k): v.to_dict() for k, v in self.page_scales.items()},
                "viewports": [{"page": p, "rect": list(r), "scale": sc.to_dict()} for p, r, sc in self.viewports]}
        kw.append(f"{TAG}-scales={json.dumps(blob)}")
        md["keywords"] = ";".join(kw)
        self.doc.set_metadata(md)

    @property
    def scale(self) -> Scale:
        return self.default_scale

    def scale_for(self, pno: int) -> Scale:
        return self.page_scales.get(pno, self.default_scale)

    def scale_at(self, pno: int, pts) -> Scale:
        """Scale that applies at the centroid of pts: the innermost-last viewport containing it, else the page scale."""
        pts = list(pts)
        c = fitz.Point(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        for p, r, sc in reversed(self.viewports):
            if p == pno and r.contains(c):
                return sc
        return self.scale_for(pno)

    def add_viewport(self, pno: int, rect, scale: Scale):
        """Give a region of a page its own scale (e.g. a detail drawn at a different scale)."""
        self._check([pno])
        r = fitz.Rect(rect).normalize()
        if r.is_empty:
            raise ValueError("viewport must have an area")
        self.checkpoint()
        self.viewports.append((pno, r, scale))

    def remove_viewport(self, index: int):
        self.checkpoint()
        del self.viewports[index]

    def set_scale(self, scale: Scale, page: int | None = None, pages=None, reset_pages: bool = False):
        """Set the default scale, or the scale of one page / a list of pages.
        reset_pages=True also clears every per-page override (apply to all pages)."""
        self.checkpoint()
        if reset_pages:
            self.page_scales = {}
        if pages is not None or page is not None:
            for p in ([page] if pages is None else pages):
                self.page_scales[p] = scale
        else:
            self.default_scale = scale

    @property
    def page_count(self) -> int:
        return len(self.doc)

    # --- markup creation ---
    def _finish(self, a, color, width, subject, fill=None, opacity=1.0, measure=None, content=""):
        a.set_colors(stroke=color, fill=fill)
        a.set_border(width=width)
        a.set_opacity(opacity)
        a.set_info(title=self.author, subject=subject, content=content)
        a.update()
        if measure:
            self.doc.xref_set_key(a.xref, MEASURE_KEY, f"({measure})")
        return a

    @_mutates
    def add_rect(self, pno, rect, color=(1, 0, 0), width=1.5, fill=None, opacity=1.0):
        return self._finish(self._page(pno).add_rect_annot(self._ru(pno, rect)), color, width, "Rectangle", fill=fill, opacity=opacity)

    @_mutates
    def add_ellipse(self, pno, rect, color=(1, 0, 0), width=1.5, fill=None, opacity=1.0):
        return self._finish(self._page(pno).add_circle_annot(self._ru(pno, rect)), color, width, "Ellipse", fill=fill, opacity=opacity)

    @_mutates
    def add_line(self, pno, p1, p2, color=(1, 0, 0), width=1.5, arrow=False):
        a = self._page(pno).add_line_annot(self._tu(pno, p1), self._tu(pno, p2))
        if arrow:
            a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
        return self._finish(a, color, width, "Arrow" if arrow else "Line")

    @_mutates
    def add_polyline(self, pno, pts, color=(1, 0, 0), width=1.5):
        return self._finish(self._page(pno).add_polyline_annot(self._tus(pno, pts)), color, width, "Polyline")

    @_mutates
    def add_polygon(self, pno, pts, color=(1, 0, 0), width=1.5, fill=None, opacity=1.0):
        return self._finish(self._page(pno).add_polygon_annot(self._tus(pno, pts)), color, width, "Polygon", fill=fill, opacity=opacity)

    @_mutates
    def add_freehand(self, pno, pts, color=(1, 0, 0), width=2):
        return self._finish(self._page(pno).add_ink_annot([self._tus(pno, pts)]), color, width, "Pen")

    @_mutates
    def add_cloud(self, pno, rect, color=(1, 0, 0), width=1.5):
        return self._finish(self._page(pno).add_polyline_annot(self._tus(pno, cloud_points(rect))), color, width, "Cloud")

    def _markup_text(self, pno, rect, kind, color):
        page, ru = self._page(pno), self._ru(pno, rect)
        words = [w for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(ru)]
        quads = [fitz.Rect(w[:4]).quad for w in words] or [ru.quad]
        a = getattr(page, f"add_{kind}_annot")(quads=quads)
        a.set_colors(stroke=color)
        a.set_info(title=self.author, subject=kind.title())
        a.update()
        return a

    @_mutates
    def add_highlight(self, pno, rect, color=(1, 1, 0)):
        return self._markup_text(pno, rect, "highlight", color)

    @_mutates
    def add_underline(self, pno, rect, color=(0, 0.6, 0)):
        return self._markup_text(pno, rect, "underline", color)

    @_mutates
    def add_strikeout(self, pno, rect, color=(1, 0, 0)):
        return self._markup_text(pno, rect, "strikeout", color)

    @_mutates
    def add_squiggly(self, pno, rect, color=(1, 0, 0)):
        return self._markup_text(pno, rect, "squiggly", color)

    @_mutates
    def add_text(self, pno, rect, text, color=(1, 0, 0), fontsize=12, fill=(1, 1, 1)):
        a = self._page(pno).add_freetext_annot(self._ru(pno, rect), text, fontsize=fontsize, text_color=color, fill_color=fill)
        a.set_info(title=self.author, subject="Text")
        a.update()
        return a

    @_mutates
    def add_callout(self, pno, rect, text, tip, color=(1, 0, 0), fontsize=11):
        """Text box with a leader line to `tip` (a callout)."""
        a = self._page(pno).add_freetext_annot(self._ru(pno, rect), text, fontsize=fontsize, text_color=color, fill_color=(1, 1, 1))
        a.set_info(title=self.author, subject="Callout")
        a.update()
        r = fitz.Rect(rect)
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        self.add_line(pno, c, tip, color, 1.0, arrow=True)
        return a

    @_mutates
    def add_note(self, pno, point, text):
        a = self._page(pno).add_text_annot(self._tu(pno, point), text)
        a.set_info(title=self.author, subject="Note", content=text)
        a.update()
        return a

    @_mutates
    def add_reply(self, markup: Markup, text):
        pg = markup._page
        r = markup.annot.rect
        a = pg.add_text_annot((r.x1, r.y0), text)
        a.set_info(title=self.author, subject="Reply", content=text)
        a.update()
        self.doc.xref_set_key(a.xref, "IRT", f"{markup.xref} 0 R")
        return a

    @_mutates
    def add_stamp(self, pno, rect, text="APPROVED", color=(0, 0.5, 0), date=True):
        """Custom text stamp rendered to an image (name + date line like Revu dynamic stamps)."""
        r = fitz.Rect(rect)
        lines = [text] + ([f"{self.author}  {_today()}"] if date else [])
        src = fitz.open()
        w, h = 400, 120 if date else 80
        sp = src.new_page(width=w, height=h)
        sp.draw_rect(fitz.Rect(3, 3, w - 3, h - 3), color=color, width=4)
        sp.insert_textbox(fitz.Rect(8, 6, w - 8, h * (0.62 if date else 1)), lines[0],
                          fontsize=38 if date else 40, fontname="hebo", color=color, align=1)
        if date:
            sp.insert_textbox(fitz.Rect(8, h * 0.66, w - 8, h - 4), lines[1], fontsize=18, color=color, align=1)
        sp.set_rotation((360 - self._page(pno).rotation) % 360)  # pre-rotate so it displays upright on rotated pages
        a = self._page(pno).add_stamp_annot(self._ru(pno, r), stamp=sp.get_pixmap(dpi=144))
        a.set_info(title=self.author, subject="Stamp", content=text)
        a.update()
        return a

    @_mutates
    def add_image_stamp(self, pno, rect, image_path, subject="Image"):
        a = self._page(pno).add_stamp_annot(self._ru(pno, rect), stamp=image_path)
        a.set_info(title=self.author, subject=subject)
        a.update()
        return a

    @_mutates
    def add_from_dict(self, pno, d, offset=(0, 0)):
        """Recreate a markup from Markup.to_dict() at an offset (paste / tool chest)."""
        dx, dy = offset
        k, page = d["kind"], self._page(pno)
        col = tuple(d["stroke"]) if d.get("stroke") else None
        fill = tuple(d["fill"]) if d.get("fill") else None
        r = self._ru(pno, fitz.Rect(d["rect"]) + (dx, dy, dx, dy))
        sh = lambda pts: self._tus(pno, [(p[0] + dx, p[1] + dy) for p in pts])  # noqa: E731
        if k == "Square":
            a = page.add_rect_annot(r)
        elif k == "Circle":
            a = page.add_circle_annot(r)
        elif k == "Line":
            a = page.add_line_annot(*sh(d["vertices"]))
            if d.get("arrow"):
                a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
        elif k == "PolyLine":
            a = page.add_polyline_annot(sh(d["vertices"]))
        elif k == "Polygon":
            a = page.add_polygon_annot(sh(d["vertices"]))
        elif k == "Ink":
            a = page.add_ink_annot([sh(st) for st in d["vertices"]])
        elif k == "FreeText":
            a = page.add_freetext_annot(r, d["comment"], fontsize=12, text_color=col or (1, 0, 0), fill_color=fill or (1, 1, 1))
        elif k == "Text":
            a = page.add_text_annot(r.tl, d["comment"])
        else:
            raise ValueError(f"cannot recreate {k} markups")
        if k not in ("FreeText", "Text"):
            a.set_colors(stroke=col, fill=fill)
            a.set_border(width=d.get("width", 1))
            a.set_opacity(d.get("opacity", 1))
        a.set_info(title=self.author, subject=d.get("subject", ""), content=d.get("comment", ""))
        a.update()
        return a

    # --- measurements (takeoff) ---
    def _label(self, pno, center, text, color, parent):
        w = 6.2 * len(text) + 8
        r = fitz.Rect(center[0] - w / 2, center[1] - 8, center[0] + w / 2, center[1] + 8)
        a = self._page(pno).add_freetext_annot(self._ru(pno, r), text, fontsize=10, text_color=color, fill_color=(1, 1, 1))
        a.set_info(title=self.author, subject="Label")
        a.update()
        self.doc.xref_set_key(a.xref, PARENT_KEY, str(parent.xref))

    @staticmethod
    def _centroid(pts):
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    @_mutates
    def add_length(self, pno, pts, color=(0, 0.4, 1), label=True, subject="Length"):
        pts = [tuple(p) for p in pts]
        sc = self.scale_at(pno, pts)
        v = sc.length(pts)
        pg = self._page(pno)
        up = self._tus(pno, pts)
        a = pg.add_polyline_annot(up) if len(pts) > 2 else pg.add_line_annot(*up)
        self._finish(a, color, 1.5, subject, measure=f"length:{v:.10g}:{sc.unit}")
        if label:
            mid = pts[len(pts) // 2] if len(pts) > 2 else self._centroid(pts)
            self._label(pno, mid, f"{v:.2f} {sc.unit}", color, a)
        return a, v

    @_mutates
    def add_perimeter(self, pno, pts, color=(0, 0.4, 1), label=True, subject="Perimeter"):
        pts = [tuple(p) for p in pts]
        sc = self.scale_at(pno, pts)
        v = sc.length(pts + pts[:1])
        a = self._page(pno).add_polygon_annot(self._tus(pno, pts))
        self._finish(a, color, 1.5, subject, measure=f"perimeter:{v:.10g}:{sc.unit}")
        if label:
            self._label(pno, self._centroid(pts), f"{v:.2f} {sc.unit}", color, a)
        return a, v

    @_mutates
    def add_area(self, pno, pts, color=(0, 0.6, 0.2), label=True, subject="Area", cutouts=(), depth=None):
        """Polygon area minus cutouts. If depth is given (real units) a volume is recorded instead."""
        pts = [tuple(p) for p in pts]
        sc = self.scale_at(pno, pts)
        v = sc.area(pts) - sum(sc.area(c) for c in cutouts)
        if v < 0:
            raise ValueError("cutouts exceed the area")
        kind, unit, txt = "area", sc.unit, f"{v:.2f} {sc.unit}²"
        if depth is not None:
            if depth <= 0:
                raise ValueError("depth must be positive")
            v, kind, txt = v * depth, "volume", f"{v * depth:.2f} {sc.unit}³"
        a = self._page(pno).add_polygon_annot(self._tus(pno, pts))
        self._finish(a, color, 1.5, subject, fill=color, opacity=0.25, measure=f"{kind}:{v:.10g}:{unit}")
        if depth is not None:
            self.doc.xref_set_key(a.xref, DEPTH_KEY, repr(float(depth)))
        for c in cutouts:
            ca = self._page(pno).add_polygon_annot(self._tus(pno, c))
            self._finish(ca, (0.5, 0.5, 0.5), 1, "Cutout", fill=(1, 1, 1), opacity=0.8)
            self.doc.xref_set_key(ca.xref, PARENT_KEY, str(a.xref))
        if label:
            self._label(pno, self._centroid(pts), txt, color, a)
        return a, v

    def region_polygon(self, pno, point, max_px=4000, threshold=200):
        """Dynamic-Fill-style: boundary (and obstacles) of the enclosed open area around `point`.
        Returns (outer_polygon, [hole_polygons]) in visual page coordinates. Needs numpy + scipy."""
        try:
            import numpy as np
            import scipy.ndimage  # noqa: F401  (checked here so a missing scipy becomes a clear error)
            from .fill import find_region
        except ImportError as e:
            raise RuntimeError("region fill needs numpy and scipy: pip install openrevu[fill]") from e
        pg = self._page(pno)
        z = min(4.0, max_px / max(pg.rect.width, pg.rect.height))
        pix = pg.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csGRAY, annots=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        outer, holes = find_region(arr > threshold, (int(point[0] * z), int(point[1] * z)), min_hole=max(30, int(z * z * 4)),
                                   tol=max(1.0, z * 0.5))
        conv = lambda ring: [((x + 0.5) / z, (y + 0.5) / z) for x, y in ring]  # noqa: E731
        return conv(outer), [conv(h) for h in holes]

    @_mutates
    def add_fill_area(self, pno, point, **kw):
        """Measure the room around `point`: area of the enclosed region minus obstacles (columns etc.)."""
        outer, holes = self.region_polygon(pno, point)
        return self.add_area(pno, outer, cutouts=holes, subject=kw.pop("subject", "Fill Area"), **kw)

    @_mutates
    def add_rect_area(self, pno, rect, **kw):
        r = fitz.Rect(rect).normalize()
        return self.add_area(pno, [tuple(r.tl), tuple(r.tr), tuple(r.br), tuple(r.bl)], **kw)

    @_mutates
    def add_ellipse_area(self, pno, rect, color=(0, 0.6, 0.2), label=True, subject="Area"):
        r = fitz.Rect(rect).normalize()
        sc = self.scale_at(pno, [tuple(r.tl), tuple(r.br)])
        v = math.pi * (r.width / 2) * (r.height / 2) * sc.unit_per_pt ** 2
        a = self._page(pno).add_circle_annot(self._ru(pno, r))
        self._finish(a, color, 1.5, subject, fill=color, opacity=0.25, measure=f"area:{v:.10g}:{sc.unit}")
        if label:
            self._label(pno, self._centroid([tuple(r.tl), tuple(r.br)]), f"{v:.2f} {sc.unit}²", color, a)
        return a, v

    @_mutates
    def add_diameter(self, pno, p1, p2, color=(0, 0.4, 1), label=True, subject="Diameter"):
        """Circle through the diameter p1-p2; records the diameter length."""
        c = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        rad = math.dist(p1, p2) / 2
        sc = self.scale_at(pno, [p1, p2])
        v = math.dist(p1, p2) * sc.unit_per_pt
        a = self._page(pno).add_circle_annot(self._ru(pno, fitz.Rect(c[0] - rad, c[1] - rad, c[0] + rad, c[1] + rad)))
        self._finish(a, color, 1.5, subject, measure=f"diameter:{v:.10g}:{sc.unit}")
        if label:
            self._label(pno, c, f"Ø {v:.2f} {sc.unit}", color, a)
        return a, v

    @_mutates
    def add_angle(self, pno, a_, b_, c_, color=(0, 0.4, 1), label=True, subject="Angle"):
        deg = angle_deg(a_, b_, c_)
        a = self._page(pno).add_polyline_annot(self._tus(pno, [a_, b_, c_]))
        self._finish(a, color, 1.5, subject, measure=f"angle:{deg:.10g}:deg")
        if label:
            self._label(pno, b_, f"{deg:.1f}°", color, a)
        return a, deg

    @_mutates
    def add_count(self, pno, point, color=(0.8, 0, 0.8), group="Count"):
        r = fitz.Rect(point[0] - 5, point[1] - 5, point[0] + 5, point[1] + 5)
        a = self._page(pno).add_circle_annot(self._ru(pno, r))
        self._finish(a, color, 1.5, group, fill=color, measure="count:1:ea")
        return a

    # --- querying / editing ---
    def markups(self, include_children=False):
        out = []
        for pno, page in enumerate(self.doc):
            for a in page.annots() or []:
                if a.type[1] in ("Popup", "Link", "Widget"):
                    continue
                if not include_children and (_xget(self.doc, a.xref, PARENT_KEY) or _xget(self.doc, a.xref, "IRT")):
                    continue
                out.append(Markup(a, pno, page, self))
        return out

    def markup_at(self, pno, point, tol=4):
        """Topmost markup on page whose bounds contain the point (for selection)."""
        pt = fitz.Point(point)
        hits = [m for m in self.markups() if m.page_no == pno and (m.rect + (-tol, -tol, tol, tol)).contains(pt)]
        return hits[-1] if hits else None

    @_mutates
    def delete(self, markup: Markup):
        for c in markup._children():
            markup._page.delete_annot(c)
        for a in markup._page.annots() or []:
            if _xget(self.doc, a.xref, "IRT") == f"{markup.xref} 0 R":
                markup._page.delete_annot(a)
        markup._page.delete_annot(markup.annot)

    def takeoff(self):
        """{(kind, unit): total} over all measurement markups."""
        totals: dict = {}
        for m in self.markups():
            r = m.measurement()
            if r:
                totals[(r[0], r[2])] = totals.get((r[0], r[2]), 0.0) + r[1]
        return totals

    def takeoff_by_subject(self):
        """{(subject, kind, unit): (count_of_items, total)} — a measurement summary."""
        out: dict = {}
        for m in self.markups():
            r = m.measurement()
            if r:
                n, t = out.get((m.subject, r[0], r[2]), (0, 0.0))
                out[(m.subject, r[0], r[2])] = (n + 1, t + r[1])
        return out

    def export_csv(self, path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["page", "type", "subject", "author", "status", "comment", "replies", "measure", "value", "unit"])
            for m in self.markups():
                r = m.measurement()
                w.writerow([m.page_no + 1, m.kind, m.subject, m.author, m.status, m.comment,
                            " | ".join(f"{a}: {t}" for a, t in m.replies()),
                            r[0] if r else "", f"{r[1]:.4f}" if r else "", r[2] if r else ""])

    def export_summary_csv(self, path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["subject", "measure", "items", "total", "unit"])
            for (s, k, u), (n, t) in sorted(self.takeoff_by_subject().items()):
                w.writerow([s, k, n, f"{t:.4f}", u])

    def render(self, pno, zoom=1.0, clip=None) -> fitz.Pixmap:
        return self._page(pno).get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True, clip=clip)

    def save(self, path=None, **opts):
        """Save (to `path`, or in place). A document opened with a password stays encrypted with it."""
        self._store_scales()
        path = path or self.path
        if path is None:
            raise ValueError("no path")
        kw = {"garbage": 3, "deflate": True, **opts}
        if self._password and "encryption" not in kw:
            kw.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=self._password, owner_pw=self._password + "-owner",
                      permissions=-1)
        if path == self.path:
            tmp = path + ".tmp"
            self.doc.save(tmp, **kw)
            self._invalidate_pages()
            self.doc.close()
            os.replace(tmp, path)
            self.doc = fitz.open(path)
            if self.doc.needs_pass and not self.doc.authenticate(self._password or kw.get("user_pw", "")):
                raise PermissionError("saved file is encrypted; reopen with its password")
        else:
            self.doc.save(path, **kw)
            self.path = path
        self.modified = False


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()

