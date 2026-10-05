"""Continuous-scroll PDF canvas with markup tools, selection, move and resize."""
from __future__ import annotations

import fitz
from PyQt5 import QtCore, QtGui, QtWidgets as W

from .core import UNITS, Document, Markup, Scale

GAP = 14
DRAG_TOOLS = {"Rectangle", "Ellipse", "Line", "Arrow", "Cloud", "Highlight", "Underline", "Strikeout",
              "Squiggly", "Calibrate", "Text", "Callout", "Redact", "RectArea", "EllipseArea", "Diameter",
              "Stamp", "Signature", "Viewport", "Snapshot", "Image", "Link"}
POLY_TOOLS = {"Length", "Area", "Perimeter", "Volume", "Polyline"}
ANGLE_TOOLS = {"Angle"}
ALL_TOOLS = ["Select", "Rectangle", "Ellipse", "Line", "Arrow", "Polyline", "Cloud", "Pen", "Highlight",
             "Underline", "Strikeout", "Squiggly", "Text", "Callout", "Note", "Stamp", "Signature", "Redact",
             "Calibrate", "Length", "Polyline", "Perimeter", "Area", "RectArea", "EllipseArea", "Volume",
             "Diameter", "Angle", "Count"]
HANDLE = 8


class Canvas(W.QGraphicsView):
    status = QtCore.pyqtSignal(str)
    changed = QtCore.pyqtSignal()          # document content changed
    selection_changed = QtCore.pyqtSignal()
    page_changed = QtCore.pyqtSignal(int)
    zoom_changed = QtCore.pyqtSignal(float)
    tool_done = QtCore.pyqtSignal()  # a markup was placed with the current tool
    context_requested = QtCore.pyqtSignal(object, QtCore.QPoint, int, tuple)  # markup|None, global pos, page, pdf point

    def __init__(self, doc: Document):
        super().__init__()
        self.setScene(W.QGraphicsScene(self))
        self.setBackgroundBrush(QtGui.QColor("#595959"))
        self.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        self.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        self.doc = doc
        self.zoom, self.tool = 1.25, "Select"
        self.color = (1.0, 0.0, 0.0)
        self.width, self.opacity = 1.5, 1.0
        self.fill = None  # fill colour for new rectangles/ellipses; None = no fill
        self.stamp_text, self.count_group, self.depth = "APPROVED", "Count", 1.0
        self.selected: Markup | None = None
        self.tool_chest_item = None  # callable(pno, pt) when placing a tool chest item
        self._rects: list[QtCore.QRectF] = []
        self._pix: dict[int, W.QGraphicsPixmapItem] = {}
        self._overlay: list = []
        self._hits: list = []
        self._hit_items: list = []
        self._pg = None
        self._start = None
        self._pts: list = []
        self._mode = None   # 'move' | 'resize' | 'pan' | None
        self._last = None
        self._cur_page = 0
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.relayout()
        self.set_tool("Select")

    def set_tool(self, name: str):
        self.tool = name
        self._reset()
        self.tool_chest_item = None
        shapes = {"Select": QtCore.Qt.ArrowCursor, "Pan": QtCore.Qt.OpenHandCursor, "Note": QtCore.Qt.PointingHandCursor}
        self.viewport().setCursor(shapes.get(name, QtCore.Qt.CrossCursor))

    # ---------- layout & rendering ----------
    def relayout(self):
        self.scene().clear()
        self._pix.clear()
        self._overlay.clear()
        self._hit_items.clear()
        self._rects = []
        y, maxw = GAP, 0
        for p in self.doc.doc:
            w, h = p.rect.width * self.zoom, p.rect.height * self.zoom
            self._rects.append(QtCore.QRectF(0, y, w, h))
            y += h + GAP
            maxw = max(maxw, w)
        for i, r in enumerate(self._rects):
            r.moveLeft((maxw - r.width()) / 2)
            self.scene().addRect(r, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtCore.Qt.white)).setZValue(0)
        self.scene().setSceneRect(-GAP, 0, maxw + 2 * GAP, y)
        self.render_visible()
        self._draw_selection()
        self._draw_hits()

    def _visible_pages(self, margin=1.0):
        vr = self.mapToScene(self.viewport().rect()).boundingRect()
        pad = vr.height() * margin
        top, bot = vr.top() - pad, vr.bottom() + pad
        return [i for i, r in enumerate(self._rects) if r.bottom() >= top and r.top() <= bot]

    def render_visible(self):
        want = set(self._visible_pages())
        for i in list(self._pix):
            if i not in want:
                self.scene().removeItem(self._pix.pop(i))
        for i in want:
            if i not in self._pix and i < len(self._rects):
                self._render_page(i)

    def _render_page(self, i):
        pix = self.doc.render(i, self.zoom)
        img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888)
        item = self.scene().addPixmap(QtGui.QPixmap.fromImage(img.copy()))
        item.setPos(self._rects[i].topLeft())
        item.setZValue(1)
        self._pix[i] = item

    def invalidate(self, pages=None):
        """Re-render pages (all if None) after an edit."""
        for i in list(self._pix) if pages is None else pages:
            if i in self._pix:
                self.scene().removeItem(self._pix.pop(i))
        self.render_visible()
        self._draw_selection()

    def reload(self):
        """Layout changed (pages added/removed/rotated/zoomed)."""
        if self.selected is not None:
            old = self.selected
            self.selected = None
            try:
                self.selected = self._refind(old)
            except RuntimeError:  # stale object: fall back to xref identity captured earlier
                pass
            self.selection_changed.emit()
        self.relayout()

    def _on_scroll(self):
        self.render_visible()
        c = self.mapToScene(self.viewport().rect().center())
        for i, r in enumerate(self._rects):
            if r.top() - GAP / 2 <= c.y() <= r.bottom() + GAP / 2:
                if i != self._cur_page:
                    self._cur_page = i
                    self.page_changed.emit(i)
                break

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.render_visible()

    def goto_page(self, i, y_offset=0.0):
        i = max(0, min(len(self._rects) - 1, i))
        self.verticalScrollBar().setValue(int(self._rects[i].top() - GAP + y_offset * self.zoom))
        self._cur_page = i
        self.render_visible()
        self.page_changed.emit(i)

    def current_page(self) -> int:
        return self._cur_page

    def set_zoom(self, z):
        z = max(0.2, min(8.0, z))
        keep = self._cur_page
        self.zoom = z
        self.relayout()
        self.goto_page(keep)
        self.zoom_changed.emit(z)

    def fit_page(self):
        if self.doc.page_count:
            pg = self.doc.doc[self._cur_page].rect
            vp = self.viewport()
            self.set_zoom(min((vp.width() - 2 * GAP) / pg.width, (vp.height() - 2 * GAP) / pg.height))

    def fit_width(self):
        if self.doc.page_count:
            w = max(p.rect.width for p in self.doc.doc)
            self.set_zoom((self.viewport().width() - 2 * GAP) / w)

    # ---------- coordinates ----------
    def page_at(self, scene_pt):
        for i, r in enumerate(self._rects):
            if r.adjusted(0, -GAP / 2, 0, GAP / 2).contains(scene_pt):
                return i
        return None

    def _to_pdf(self, pno, scene_pt):
        r = self._rects[pno]
        return ((scene_pt.x() - r.left()) / self.zoom, (scene_pt.y() - r.top()) / self.zoom)

    def _to_scene(self, pno, pt):
        r = self._rects[pno]
        return QtCore.QPointF(r.left() + pt[0] * self.zoom, r.top() + pt[1] * self.zoom)

    def _srect(self, pno, rect):
        return QtCore.QRectF(self._to_scene(pno, (rect[0], rect[1])), self._to_scene(pno, (rect[2], rect[3]))).normalized()

    # ---------- overlays ----------
    def _clear_overlay(self):
        for it in self._overlay:
            if it.scene():
                self.scene().removeItem(it)
        self._overlay.clear()

    def _draw_selection(self):
        self._clear_overlay()
        m = self.selected
        if not m:
            return
        try:
            r = self._srect(m.page_no, m.rect).adjusted(-3, -3, 3, 3)
        except Exception:
            self.selected = None
            return
        pen = QtGui.QPen(QtGui.QColor(0, 120, 255), 1, QtCore.Qt.DashLine)
        box = self.scene().addRect(r, pen)
        h = self.scene().addRect(QtCore.QRectF(r.right() - HANDLE / 2, r.bottom() - HANDLE / 2, HANDLE, HANDLE),
                                 QtGui.QPen(QtGui.QColor(0, 120, 255)), QtGui.QBrush(QtCore.Qt.white))
        for it in (box, h):
            it.setZValue(10)
            self._overlay.append(it)

    def set_hits(self, hits):
        self._hits = hits
        self._draw_hits()

    def _draw_hits(self):
        for it in self._hit_items:
            if it.scene():
                self.scene().removeItem(it)
        self._hit_items = []
        for pno, rect in self._hits:
            if pno < len(self._rects):
                it = self.scene().addRect(self._srect(pno, rect), QtGui.QPen(QtCore.Qt.NoPen),
                                          QtGui.QBrush(QtGui.QColor(255, 200, 0, 110)))
                it.setZValue(5)
                self._hit_items.append(it)

    def select(self, m: Markup | None):
        self.selected = m
        self._draw_selection()
        self.selection_changed.emit()

    # ---------- mouse ----------
    def mousePressEvent(self, ev):
        sp = self.mapToScene(ev.pos())
        if ev.button() == QtCore.Qt.MiddleButton:
            self._mode, self._last = "pan", ev.pos()
            return
        if ev.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(ev)
        pno = self.page_at(sp)
        if self.tool == "Pan":
            self._mode, self._last = "pan", ev.pos()
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            return
        if pno is None:
            return
        pt = self._to_pdf(pno, sp)
        if self.tool_chest_item:
            self.tool_chest_item(pno, pt)
            return
        t = self.tool
        if t == "Pan":
            self._mode, self._last = "pan", ev.pos()
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            return
        if t == "Select":
            return self._press_select(ev, pno, pt, sp)
        if self._pg is not None and self._pg != pno and (self._pts or self._start):
            return  # multi-click tools stay on one page
        self._pg = pno
        if t in DRAG_TOOLS or t == "Pen":
            self._start, self._pts = pt, [pt]
        elif t in POLY_TOOLS or t in ANGLE_TOOLS:
            self._pts.append(pt)
            if t in ANGLE_TOOLS and len(self._pts) == 3:
                self._finish_angle()
            else:
                self._preview()
        elif t == "Count":
            self.doc.add_count(pno, pt, group=self.count_group or "Count")
            self._done([pno])
        elif t == "Fill":
            try:
                _, v = self.doc.add_fill_area(pno, pt)
                self.status.emit(f"Fill area: {v:.3f} {self.doc.scale_at(pno, [pt]).unit}²")
            except (ValueError, RuntimeError) as e:
                self.status.emit(str(e))
                return  # a missed click does not end the tool
            self._done([pno])
        elif t == "Note":
            text, ok = W.QInputDialog.getText(self, "Note", "Text:")
            if ok:
                self.doc.add_note(pno, pt, text)
                self._done([pno])

    def _press_select(self, ev, pno, pt, sp):
        if self.selected and self.selected.page_no == pno:
            hr = self._srect(pno, self.selected.rect).adjusted(-3, -3, 3, 3)
            if QtCore.QRectF(hr.right() - HANDLE, hr.bottom() - HANDLE, 2 * HANDLE, 2 * HANDLE).contains(sp):
                self._mode, self._start, self._pg = "resize", pt, pno
                return
        hit = self.doc.markup_at(pno, pt, tol=4 / self.zoom)
        self.select(hit)
        if hit:
            self._mode, self._start, self._pg = "move", pt, pno
        else:
            self._mode, self._last = "pan", ev.pos()

    def mouseMoveEvent(self, ev):
        if self._mode == "pan":
            d = ev.pos() - self._last
            self._last = ev.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            return
        sp = self.mapToScene(ev.pos())
        if self._mode in ("move", "resize") and self.selected is not None:
            cur = self._to_pdf(self._pg, sp)
            r = fitz.Rect(self.selected.rect)
            if self._mode == "move":
                r = r + (cur[0] - self._start[0], cur[1] - self._start[1]) * 2
            else:
                r = fitz.Rect(r.x0, r.y0, max(r.x1 + cur[0] - self._start[0], r.x0 + 2),
                              max(r.y1 + cur[1] - self._start[1], r.y0 + 2))
            self._clear_overlay()
            it = self.scene().addRect(self._srect(self._pg, r), QtGui.QPen(QtGui.QColor(0, 120, 255), 1.5))
            it.setZValue(10)
            self._overlay.append(it)
            return
        if self._pg is None:
            return super().mouseMoveEvent(ev)
        cur = self._to_pdf(self._pg, sp)
        if self._start:
            self._pts = self._pts + [cur] if self.tool == "Pen" else [self._start, cur]
            self._preview()
        elif self._pts:
            self._preview(cur)

    def mouseDoubleClickEvent(self, ev):
        if self.tool == "Select":
            if self.selected and (self.selected.kind == "FreeText" or self.selected.kind == "Text"):
                text, ok = W.QInputDialog.getMultiLineText(self, "Edit text", "Text:", self.selected.comment)
                if ok:
                    self.selected.set_comment(text)
                    self._done([self.selected.page_no])
            return
        if self.tool in POLY_TOOLS and self._pts and self._pg is not None:
            pts, pno, t, d = list(self._pts), self._pg, self.tool, self.doc
            self._reset()
            need = 3 if t in ("Area", "Perimeter", "Volume") else 2
            if len(pts) < need:
                return self.invalidate([pno])
            c = self.color
            if t == "Length":
                _, v = d.add_length(pno, pts); msg = f"Length: {v:.3f} {d.scale_at(pno, pts).unit}"
            elif t == "Polyline":
                d.add_polyline(pno, pts, c, self.width); msg = "Polyline added"
            elif t == "Perimeter":
                _, v = d.add_perimeter(pno, pts); msg = f"Perimeter: {v:.3f} {d.scale_at(pno, pts).unit}"
            elif t == "Area":
                _, v = d.add_area(pno, pts); msg = f"Area: {v:.3f} {d.scale_at(pno, pts).unit}²"
            else:
                _, v = d.add_area(pno, pts, depth=self.depth); msg = f"Volume: {v:.3f} {d.scale_at(pno, pts).unit}³"
            self.status.emit(msg)
            self._done([pno])

    def mouseReleaseEvent(self, ev):
        mode, self._mode = self._mode, None
        if mode == "pan":
            if self.tool == "Pan":
                self.viewport().setCursor(QtCore.Qt.OpenHandCursor)
            return
        sp = self.mapToScene(ev.pos())
        if mode in ("move", "resize") and self.selected is not None:
            cur = self._to_pdf(self._pg, sp)
            m, pno = self.selected, self._pg
            try:
                if mode == "move":
                    dx, dy = cur[0] - self._start[0], cur[1] - self._start[1]
                    if abs(dx) + abs(dy) > 0.5:
                        m.move(dx, dy)
                else:
                    r = m.rect
                    m.resize(fitz.Rect(r.x0, r.y0, max(r.x1 + cur[0] - self._start[0], r.x0 + 2),
                                       max(r.y1 + cur[1] - self._start[1], r.y0 + 2)))
            except ValueError as e:
                self.status.emit(str(e))
            self._start = None
            self.selected = self._refind(m)
            self._done([pno])
            self.selection_changed.emit()
            return
        if not (self._start and self._pg is not None):
            return
        a, b = self._start, self._to_pdf(self._pg, sp)
        pts, pno, self._start, self._pts = self._pts, self._pg, None, []
        self._clear_overlay()
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 3 and self.tool not in ("Stamp", "Signature", "Image"):
            return self.invalidate([pno])
        self._commit_drag(pno, a, b, pts)
        self._pg = None

    def _refind(self, m: Markup):
        xref, pno = m._annot.xref if m._gen == self.doc.generation else m._xref, m.page_no
        for x in self.doc.markups():
            if x.xref == xref and x.page_no == pno:
                return x
        return None

    def _commit_drag(self, pno, a, b, pts):
        r, t, d, c = fitz.Rect(a, b).normalize(), self.tool, self.doc, self.color
        w, op = self.width, self.opacity
        if t == "Rectangle": d.add_rect(pno, r, c, w, fill=self.fill, opacity=op)
        elif t == "Ellipse": d.add_ellipse(pno, r, c, w, fill=self.fill, opacity=op)
        elif t == "Line": d.add_line(pno, a, b, c, w)
        elif t == "Arrow": d.add_line(pno, a, b, c, w, arrow=True)
        elif t == "Cloud": d.add_cloud(pno, r, c, w)
        elif t == "Highlight": d.add_highlight(pno, r, c if c != (1.0, 0.0, 0.0) else (1, 1, 0))
        elif t == "Underline": d.add_underline(pno, r)
        elif t == "Strikeout": d.add_strikeout(pno, r)
        elif t == "Squiggly": d.add_squiggly(pno, r)
        elif t == "Pen": d.add_freehand(pno, pts, c, w)
        elif t == "Redact":
            d.mark_redaction(pno, r)
            self.status.emit("Redaction marked — use Document ▸ Apply redactions to remove content permanently")
        elif t in ("Text", "Callout"):
            text, ok = W.QInputDialog.getMultiLineText(self, t, "Text:")
            if not (ok and text):
                return self.invalidate([pno])
            if t == "Text":
                d.add_text(pno, r, text, c)
            else:
                d.add_callout(pno, r, text, (r.x0 - 30, r.y1 + 40), c)
        elif t == "Stamp":
            if r.width < 20:
                r = fitz.Rect(a[0], a[1], a[0] + 160, a[1] + 48)
            d.add_stamp(pno, r, self.stamp_text, c if c != (1.0, 0.0, 0.0) else (0, 0.5, 0))
        elif t == "Signature":
            if r.width < 20:
                r = fitz.Rect(a[0], a[1], a[0] + 180, a[1] + 54)
            d.add_signature_text(pno, r)
        elif t == "RectArea": _, v = d.add_rect_area(pno, r); self.status.emit(f"Area: {v:.3f}")
        elif t == "EllipseArea": _, v = d.add_ellipse_area(pno, r); self.status.emit(f"Area: {v:.3f}")
        elif t == "Diameter": _, v = d.add_diameter(pno, a, b); self.status.emit(f"Diameter: {v:.3f}")
        elif t == "Snapshot":
            pix = d.render(pno, 2.0, clip=r)
            img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888).copy()
            W.QApplication.clipboard().setImage(img)
            self.status.emit(f"Snapshot copied to the clipboard ({img.width()} x {img.height()} px). Paste it into another program.")
            return self.invalidate([pno])
        elif t == "Image":
            path, _ = W.QFileDialog.getOpenFileName(self, "Choose an image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)")
            if not path:
                return self.invalidate([pno])
            try:
                if r.width < 20:   # a click: keep the picture's own proportions
                    pm = fitz.Pixmap(path)
                    w0 = min(200.0, pm.width)
                    r = fitz.Rect(a[0], a[1], a[0] + w0, a[1] + w0 * pm.height / pm.width)
                d.add_image_stamp(pno, r, path)
            except Exception as e:  # the image libraries raise many different types for a bad file
                self.status.emit(f"Cannot use that image: {e}")
                return self.invalidate([pno])
        elif t == "Link":
            self._add_link(pno, r)
            return self._done([pno])
        elif t == "Calibrate":
            self._calibrate(pno, a, b)
            return self._done(None)
        elif t == "Viewport":
            txt, ok = W.QInputDialog.getText(self, "Viewport scale", "paper inches : real length : unit  (e.g. 0.5:1:ft)", text="0.25:1:ft")
            if ok:
                try:
                    pi, rl, un = txt.split(":")
                    d.add_viewport(pno, r, Scale.from_ratio(float(pi), float(rl), un.strip()))
                    self.status.emit("Viewport added: measurements inside it use its own scale")
                except ValueError as e:
                    self.status.emit(f"Invalid viewport: {e}")
            return self._done(None)
        self._done([pno])

    def _finish_angle(self):
        pts, pno = list(self._pts), self._pg
        self._reset()
        try:
            _, deg = self.doc.add_angle(pno, *pts)
            self.status.emit(f"Angle: {deg:.2f}°")
        except ValueError as e:
            self.status.emit(str(e))
        self._done([pno])

    def _add_link(self, pno, r):
        """Ask for a web address or a page number, and make the dragged region a link."""
        text, ok = W.QInputDialog.getText(self, "Link", "Web address (https://…) or page number:")
        text = text.strip()
        if not ok or not text:
            return
        if text.isdigit():
            n = int(text)
            if not 1 <= n <= self.doc.page_count:
                return self.status.emit(f"There is no page {n}. The document has {self.doc.page_count} page(s).")
            self.doc.add_link_goto(pno, r, n - 1)
            return self.status.emit(f"Link to page {n} added. Click it in a PDF viewer to follow it.")
        if text.lower().startswith("www."):
            text = "https://" + text
        if not text.lower().startswith(("http://", "https://", "mailto:")):
            return self.status.emit("A link needs a page number or an address that starts with http://, https://, or mailto:")
        self.doc.add_link_uri(pno, r, text)
        self.status.emit(f"Link to {text} added.")

    def _calibrate(self, pno, a, b):
        ln, ok = W.QInputDialog.getDouble(self, "Calibrate", "Real length of the dragged segment:", 10, 1e-6, 1e9, 3)
        if not ok:
            return
        unit, ok = W.QInputDialog.getItem(self, "Calibrate", "Unit:", list(UNITS), 4, False)
        if not ok:
            return
        scope, ok = W.QInputDialog.getItem(self, "Calibrate", "Apply to:", ["This page", "All pages"], 0, False)
        if not ok:
            return
        try:
            sc = Scale.from_calibration(a, b, ln, unit)
        except ValueError as e:
            return self.status.emit(str(e))
        if scope == "All pages":
            self.doc.set_scale(sc, reset_pages=True)
        else:
            self.doc.set_scale(sc, page=pno)
        self.status.emit(f"Scale set: {ln} {unit} = {fitz.Point(a).distance_to(b):.1f} pt ({scope.lower()})")

    def _reset(self):
        self._pts, self._start, self._pg = [], None, None
        self._clear_overlay()

    def cancel(self):
        self._reset()
        self.tool_chest_item = None

    def _preview(self, extra=None):
        self._clear_overlay()
        if self._pg is None:
            return
        pts = self._pts + ([extra] if extra else [])
        if not pts:
            return
        sp = [self._to_scene(self._pg, p) for p in pts]
        path = QtGui.QPainterPath()
        if self.tool in ("Rectangle", "Cloud", "Highlight", "Underline", "Strikeout", "Squiggly", "Text", "Callout",
                         "Redact", "RectArea", "Stamp", "Signature") and len(sp) == 2:
            path.addRect(QtCore.QRectF(sp[0], sp[1]).normalized())
        elif self.tool in ("Ellipse", "EllipseArea") and len(sp) == 2:
            path.addEllipse(QtCore.QRectF(sp[0], sp[1]).normalized())
        else:
            path.moveTo(sp[0])
            for p in sp[1:]:
                path.lineTo(p)
        it = self.scene().addPath(path, QtGui.QPen(QtGui.QColor(0, 120, 255), 1.5, QtCore.Qt.DashLine))
        it.setZValue(10)
        self._overlay.append(it)

    def _done(self, pages):
        self.invalidate(pages)
        self.changed.emit()
        if self.tool not in ("Select", "Pan"):
            self.tool_done.emit()

    def contextMenuEvent(self, ev):
        sp = self.mapToScene(ev.pos())
        pno = self.page_at(sp)
        if pno is None:
            return
        pt = self._to_pdf(pno, sp)
        hit = self.doc.markup_at(pno, pt, tol=4 / self.zoom)
        if hit is not None:
            self.select(hit)
        self.context_requested.emit(hit, ev.globalPos(), pno, pt)

    def wheelEvent(self, ev):
        if ev.modifiers() & QtCore.Qt.ControlModifier:
            self.set_zoom(self.zoom * (1.15 if ev.angleDelta().y() > 0 else 1 / 1.15))
        else:
            super().wheelEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() == QtCore.Qt.Key_Escape:
            self.cancel()
            self.invalidate()
        elif ev.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace) and self.selected is not None:
            pno = self.selected.page_no
            self.doc.delete(self.selected)
            self.select(None)
            self._done([pno])
        else:
            super().keyPressEvent(ev)
