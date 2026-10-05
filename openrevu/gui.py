"""PyQt5 front end: viewer, markup tools, scale calibration, takeoff panel."""
from __future__ import annotations

import sys

import fitz
from PyQt5 import QtCore, QtGui, QtWidgets as W

from .core import UNITS, Document, Scale

TOOLS = ["Select", "Rectangle", "Ellipse", "Line", "Arrow", "Cloud", "Pen", "Highlight",
         "Text", "Note", "Calibrate", "Length", "Area", "Count"]
DRAG_TOOLS = {"Rectangle", "Ellipse", "Line", "Arrow", "Cloud", "Highlight", "Calibrate", "Text"}
POLY_TOOLS = {"Length", "Area"}


class Canvas(W.QGraphicsView):
    status = QtCore.pyqtSignal(str)
    changed = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setScene(W.QGraphicsScene(self))
        self.setBackgroundBrush(QtGui.QColor("#555"))
        self.doc: Document | None = None
        self.page, self.zoom, self.tool = 0, 1.5, "Select"
        self.color = (1.0, 0.0, 0.0)
        self._start = None
        self._pts: list = []
        self._rubber = None

    # --- rendering ---
    def refresh(self):
        self.scene().clear()
        self._rubber = None
        if not self.doc:
            return
        pix = self.doc.render(self.page, self.zoom)
        img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888)
        self.scene().addPixmap(QtGui.QPixmap.fromImage(img.copy()))
        self.scene().setSceneRect(0, 0, pix.width, pix.height)

    def _pdf(self, ev) -> tuple:
        p = self.mapToScene(ev.pos())
        return (p.x() / self.zoom, p.y() / self.zoom)

    # --- interaction ---
    def mousePressEvent(self, ev):
        if not self.doc or ev.button() != QtCore.Qt.LeftButton or self.tool == "Select":
            return super().mousePressEvent(ev)
        p = self._pdf(ev)
        if self.tool in DRAG_TOOLS or self.tool == "Pen":
            self._start, self._pts = p, [p]
        elif self.tool in POLY_TOOLS:
            self._pts.append(p)
            self._draw_preview()
        elif self.tool == "Count":
            self.doc.add_count(self.page, p)
            self._done()
        elif self.tool == "Note":
            text, ok = W.QInputDialog.getText(self, "Note", "Text:")
            if ok:
                self.doc.add_note(self.page, p, text)
                self._done()

    def mouseMoveEvent(self, ev):
        if self._start and self.doc:
            self._pts = self._pts + [self._pdf(ev)] if self.tool == "Pen" else [self._start, self._pdf(ev)]
            self._draw_preview()
        elif self.tool in POLY_TOOLS and self._pts:
            self._draw_preview(self._pdf(ev))
        else:
            super().mouseMoveEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if self.tool in POLY_TOOLS and self.doc:
            pts = self._pts  # first click of the double-click already added the last vertex
            need = 3 if self.tool == "Area" else 2
            if len(pts) >= need:
                if self.tool == "Length":
                    _, v = self.doc.add_length(self.page, pts)
                    self.status.emit(f"Length: {v:.3f} {self.doc.scale.unit}")
                else:
                    _, v = self.doc.add_area(self.page, pts)
                    self.status.emit(f"Area: {v:.3f} {self.doc.scale.unit}²")
                self._done()
            self._pts = []

    def mouseReleaseEvent(self, ev):
        if not (self._start and self.doc):
            return super().mouseReleaseEvent(ev)
        a, b = self._start, self._pdf(ev)
        pts, self._start, self._pts = self._pts, None, []
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 3:
            return self.refresh()
        r, t, pg, c = fitz.Rect(a, b).normalize(), self.tool, self.page, self.color
        d = self.doc
        if t == "Rectangle": d.add_rect(pg, r, c)
        elif t == "Ellipse": d.add_ellipse(pg, r, c)
        elif t == "Line": d.add_line(pg, a, b, c)
        elif t == "Arrow": d.add_line(pg, a, b, c, arrow=True)
        elif t == "Cloud": d.add_cloud(pg, r, c)
        elif t == "Highlight": d.add_highlight(pg, r)
        elif t == "Pen": d.add_freehand(pg, pts, c)
        elif t == "Text":
            text, ok = W.QInputDialog.getText(self, "Text", "Text:")
            if ok and text:
                d.add_text(pg, r, text, c)
        elif t == "Calibrate":
            self._calibrate(a, b)
        self._done()

    def _calibrate(self, a, b):
        ln, ok = W.QInputDialog.getDouble(self, "Calibrate", "Real length of this segment:", 10, 1e-6, 1e9, 3)
        if not ok:
            return
        unit, ok = W.QInputDialog.getItem(self, "Calibrate", "Unit:", list(UNITS), 4, False)
        if ok:
            self.doc.set_scale(Scale.from_calibration(a, b, ln, unit))
            self.status.emit(f"Scale set: {ln} {unit} = {fitz.Point(a).distance_to(b):.1f} pt")

    def _draw_preview(self, extra=None):
        if self._rubber:
            self.scene().removeItem(self._rubber)
        pts = self._pts + ([extra] if extra else [])
        path = QtGui.QPainterPath()
        if self.tool in ("Rectangle", "Cloud", "Highlight", "Text") and len(pts) == 2:
            path.addRect(QtCore.QRectF(QtCore.QPointF(*[v * self.zoom for v in pts[0]]),
                                       QtCore.QPointF(*[v * self.zoom for v in pts[1]])).normalized())
        elif self.tool == "Ellipse" and len(pts) == 2:
            path.addEllipse(QtCore.QRectF(QtCore.QPointF(*[v * self.zoom for v in pts[0]]),
                                          QtCore.QPointF(*[v * self.zoom for v in pts[1]])).normalized())
        elif pts:
            path.moveTo(pts[0][0] * self.zoom, pts[0][1] * self.zoom)
            for p in pts[1:]:
                path.lineTo(p[0] * self.zoom, p[1] * self.zoom)
        self._rubber = self.scene().addPath(path, QtGui.QPen(QtGui.QColor(0, 120, 255), 1.5, QtCore.Qt.DashLine))

    def _done(self):
        self.refresh()
        self.changed.emit()

    def wheelEvent(self, ev):
        if ev.modifiers() & QtCore.Qt.ControlModifier:
            self.set_zoom(self.zoom * (1.15 if ev.angleDelta().y() > 0 else 1 / 1.15))
        else:
            super().wheelEvent(ev)

    def set_zoom(self, z):
        self.zoom = max(0.25, min(8.0, z))
        self.refresh()


class Main(W.QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setWindowTitle("OpenRevu")
        self.resize(1300, 850)
        self.canvas = Canvas()
        self.setCentralWidget(self.canvas)
        self.canvas.status.connect(lambda s: self.statusBar().showMessage(s))
        self.canvas.changed.connect(self.update_panel)
        self.list = W.QListWidget()
        self.totals = W.QLabel("Takeoff totals")
        self.totals.setWordWrap(True)
        side = W.QWidget()
        lay = W.QVBoxLayout(side)
        lay.addWidget(W.QLabel("Markups"))
        lay.addWidget(self.list)
        btn = W.QPushButton("Delete selected")
        btn.clicked.connect(self.delete_selected)
        lay.addWidget(btn)
        lay.addWidget(self.totals)
        dock = W.QDockWidget("Markups List")
        dock.setWidget(side)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        self.list.itemDoubleClicked.connect(self.goto_markup)
        self._build_menus()
        self.markups = []
        if path:
            self.open(path)

    def _act(self, menu, text, fn, key=None):
        a = menu.addAction(text, fn)
        if key:
            a.setShortcut(key)
        return a

    def _build_menus(self):
        f = self.menuBar().addMenu("&File")
        self._act(f, "Open…", self.open_dialog, "Ctrl+O")
        self._act(f, "Save", self.save, "Ctrl+S")
        self._act(f, "Save As…", self.save_as, "Ctrl+Shift+S")
        self._act(f, "Export markups CSV…", self.export_csv)
        v = self.menuBar().addMenu("&View")
        self._act(v, "Zoom in", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25), "Ctrl+=")
        self._act(v, "Zoom out", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.25), "Ctrl+-")
        self._act(v, "Next page", lambda: self.go(1), "PgDown")
        self._act(v, "Previous page", lambda: self.go(-1), "PgUp")
        tb = self.addToolBar("Tools")
        grp = W.QActionGroup(self)
        for name in TOOLS:
            a = tb.addAction(name)
            a.setCheckable(True)
            a.setChecked(name == "Select")
            a.triggered.connect(lambda _, n=name: self.set_tool(n))
            grp.addAction(a)
        tb.addSeparator()
        ca = tb.addAction("Color…")
        ca.triggered.connect(self.pick_color)

    def set_tool(self, name):
        self.canvas.tool = name
        self.canvas._pts, self.canvas._start = [], None
        self.canvas.setDragMode(W.QGraphicsView.ScrollHandDrag if name == "Select" else W.QGraphicsView.NoDrag)
        hint = {"Length": "click points, double-click to finish", "Area": "click points, double-click to finish",
                "Calibrate": "drag across a known dimension"}.get(name, "")
        self.statusBar().showMessage(f"{name} {hint}")

    def pick_color(self):
        c = W.QColorDialog.getColor(QtGui.QColor(*[int(v * 255) for v in self.canvas.color]), self)
        if c.isValid():
            self.canvas.color = (c.redF(), c.greenF(), c.blueF())

    def open_dialog(self):
        p, _ = W.QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF (*.pdf)")
        if p:
            self.open(p)

    def open(self, path):
        try:
            self.canvas.doc = Document(path)
        except Exception as e:  # corrupt/unreadable file
            return W.QMessageBox.critical(self, "Open failed", str(e))
        self.canvas.page = 0
        self.canvas.refresh()
        self.update_panel()
        self.setWindowTitle(f"OpenRevu — {path}")

    def save(self):
        d = self.canvas.doc
        if d and d.path:
            d.save()
            self.canvas.refresh()
            self.statusBar().showMessage("Saved")

    def save_as(self):
        d = self.canvas.doc
        if d:
            p, _ = W.QFileDialog.getSaveFileName(self, "Save As", "", "PDF (*.pdf)")
            if p:
                d.save(p)
                self.setWindowTitle(f"OpenRevu — {p}")

    def export_csv(self):
        if self.canvas.doc:
            p, _ = W.QFileDialog.getSaveFileName(self, "Export CSV", "markups.csv", "CSV (*.csv)")
            if p:
                self.canvas.doc.export_csv(p)

    def go(self, delta):
        d = self.canvas.doc
        if d:
            self.canvas.page = max(0, min(d.page_count - 1, self.canvas.page + delta))
            self.canvas.refresh()
            self.statusBar().showMessage(f"Page {self.canvas.page + 1}/{d.page_count}")

    def update_panel(self):
        d = self.canvas.doc
        self.list.clear()
        self.markups = d.markups() if d else []
        for m in self.markups:
            r = m.measurement()
            txt = f"p{m.page_no + 1}  {m.subject or m.kind}" + (f"  {r[1]:.2f} {r[2]}" if r else f"  {m.comment}")
            self.list.addItem(txt)
        if d:
            lines = [f"{k.title()}: {v:.2f} {u}" for (k, u), v in sorted(d.takeoff().items())]
            self.totals.setText("Takeoff totals\n" + "\n".join(lines) + f"\nScale unit: {d.scale.unit}")

    def goto_markup(self, item):
        m = self.markups[self.list.row(item)]
        self.canvas.page = m.page_no
        self.canvas.refresh()

    def delete_selected(self):
        d, row = self.canvas.doc, self.list.currentRow()
        if d and row >= 0:
            d.delete(self.markups[row])
            self.canvas.refresh()
            self.update_panel()


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = W.QApplication(argv)
    win = Main(argv[1] if len(argv) > 1 else None)
    win.show()
    return app.exec_()
