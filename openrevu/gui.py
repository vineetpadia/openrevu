"""PyQt5 application window: tabs, panels, menus and dialogs."""
from __future__ import annotations

import os
import sys

import fitz
from PyQt5 import QtCore, QtGui, QtWidgets as W

from . import __version__
from .canvas import Canvas
from .core import STATUSES, Document, Scale, format_value
from .pages import compare_pdfs
from .toolchest import ToolChest

MARKUP_TOOLS = ["Select", "Rectangle", "Ellipse", "Line", "Arrow", "Polyline", "Cloud", "Pen", "Highlight",
                "Underline", "Strikeout", "Squiggly", "Text", "Callout", "Note", "Stamp", "Signature", "Redact"]
MEASURE_TOOLS = ["Calibrate", "Length", "Perimeter", "Area", "Fill", "RectArea", "EllipseArea", "Volume", "Diameter",
                 "Angle", "Count", "Viewport"]
STAMPS = ["APPROVED", "REVIEWED", "REJECTED", "DRAFT", "FOR CONSTRUCTION", "VOID", "AS BUILT", "CONFIDENTIAL"]
ERRORS = (ValueError, IndexError, RuntimeError, PermissionError, OSError, KeyError)


def parse_pages(text: str, n: int) -> list[int]:
    """'1-3,5' -> [0,1,2,4] (1-based input, validated against n)."""
    out: list[int] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(part)
        if not 1 <= lo <= hi <= n:
            raise ValueError(f"page range {part!r} outside 1-{n}")
        out += range(lo - 1, hi)
    if not out:
        raise ValueError("no pages given")
    return list(dict.fromkeys(out))


class Main(W.QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setWindowTitle("OpenRevu")
        self.resize(1450, 900)
        self.tabs = W.QTabWidget(documentMode=True, tabsClosable=True, movable=True)
        self.setCentralWidget(self.tabs)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.clipboard: dict | None = None
        self.chest = ToolChest()
        self.color = (1.0, 0.0, 0.0)
        self._build_docks()
        self._build_actions()
        self._build_options_bar()
        self.mk_table.verticalHeader().hide()
        self.d_thumbs.raise_()
        self.resizeDocks([self.d_thumbs, self.d_props], [230, 270], QtCore.Qt.Horizontal)
        self.resizeDocks([self.d_markups], [260], QtCore.Qt.Vertical)
        self.statusBar().showMessage("Open a PDF to begin (Ctrl+O)")
        self.setAcceptDrops(True)
        if path:
            self.open(path)

    # ---------- current-document helpers ----------
    @property
    def cv(self) -> Canvas | None:
        return self.tabs.currentWidget()

    @property
    def doc(self) -> Document | None:
        return self.cv.doc if self.cv else None

    def _need_doc(self) -> bool:
        if not self.cv:
            self.statusBar().showMessage("No document open")
        return self.cv is not None

    def _run(self, fn, *a, structural=False, msg=None, **kw):
        """Run a document operation with error reporting and UI refresh."""
        try:
            r = fn(*a, **kw)
        except ERRORS as e:
            W.QMessageBox.warning(self, "OpenRevu", str(e))
            return None
        if structural:
            self.cv.reload()
        else:
            self.cv.invalidate()
        self._after_change()
        if msg:
            self.statusBar().showMessage(msg)
        return True if r is None else r

    # ---------- UI construction ----------
    def _dock(self, title, widget, area=QtCore.Qt.LeftDockWidgetArea):
        d = W.QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _build_docks(self):
        self.thumbs = W.QListWidget(viewMode=W.QListView.IconMode, iconSize=QtCore.QSize(100, 100), gridSize=QtCore.QSize(105, 125),
                                    resizeMode=W.QListView.Adjust, movement=W.QListView.Static, wordWrap=True)
        self.thumbs.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.thumbs.itemClicked.connect(lambda it: self.cv and self.cv.goto_page(self.thumbs.row(it)))
        self.thumbs.customContextMenuRequested.connect(self._thumb_menu)
        self.thumbs.setSelectionMode(W.QAbstractItemView.ExtendedSelection)
        self.d_thumbs = self._dock("Thumbnails", self.thumbs)

        bw = W.QWidget()
        bl = W.QVBoxLayout(bw)
        self.toc = W.QTreeWidget(headerHidden=True)
        self.toc.itemClicked.connect(lambda it, _: self.cv and self.cv.goto_page(it.data(0, QtCore.Qt.UserRole) - 1))
        bl.addWidget(self.toc)
        row = W.QHBoxLayout()
        for text, fn in (("Add bookmark here", self.add_bookmark), ("Remove", self.remove_bookmark)):
            b = W.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        bl.addLayout(row)
        self.d_toc = self._dock("Bookmarks", bw)
        self.tabifyDockWidget(self.d_thumbs, self.d_toc)
        self.d_thumbs.raise_()

        mw = W.QWidget()
        ml = W.QVBoxLayout(mw)
        frow = W.QHBoxLayout()
        self.mk_filter = W.QLineEdit(placeholderText="Filter markups…")
        self.mk_status = W.QComboBox()
        self.mk_status.addItems(["Any status"] + [s or "(none)" for s in STATUSES])
        for w in (self.mk_filter, self.mk_status):
            frow.addWidget(w)
        ml.addLayout(frow)
        self.mk_table = W.QTableWidget(0, 7, selectionBehavior=W.QAbstractItemView.SelectRows,
                                       editTriggers=W.QAbstractItemView.NoEditTriggers, sortingEnabled=True)
        self.mk_table.setHorizontalHeaderLabels(["Page", "Subject", "Type", "Author", "Status", "Comment", "Value"])
        self.mk_table.horizontalHeader().setStretchLastSection(True)
        self.mk_table.itemSelectionChanged.connect(self._table_select)
        self.mk_filter.textChanged.connect(self.refresh_markups)
        self.mk_status.currentIndexChanged.connect(self.refresh_markups)
        ml.addWidget(self.mk_table)
        self.totals = W.QLabel()
        self.totals.setWordWrap(True)
        trow = W.QHBoxLayout()
        trow.addWidget(self.totals, 1)
        b = W.QPushButton("Measurement summary…")
        b.clicked.connect(self.show_summary)
        trow.addWidget(b)
        ml.addLayout(trow)
        self.d_markups = self._dock("Markups List", mw, QtCore.Qt.BottomDockWidgetArea)

        pw = W.QWidget()
        pl = W.QFormLayout(pw)
        self.p_subject = W.QLineEdit()
        self.p_comment = W.QPlainTextEdit(maximumHeight=70)
        self.p_status = W.QComboBox()
        self.p_status.addItems([s or "(none)" for s in STATUSES])
        self.p_stroke, self.p_fill = W.QPushButton("Line…"), W.QPushButton("Fill…")
        self.p_width = W.QDoubleSpinBox(minimum=0.25, maximum=40, singleStep=0.5, value=1.5)
        self.p_opacity = W.QDoubleSpinBox(minimum=0.05, maximum=1, singleStep=0.05, value=1)
        self.p_replies = W.QListWidget(maximumHeight=90)
        self.p_reply = W.QLineEdit(placeholderText="Reply…")
        for lbl, w in (("Subject", self.p_subject), ("Comment", self.p_comment), ("Status", self.p_status),
                       ("Line colour", self.p_stroke), ("Fill colour", self.p_fill), ("Width", self.p_width),
                       ("Opacity", self.p_opacity), ("Replies", self.p_replies), ("", self.p_reply)):
            pl.addRow(lbl, w)
        self.p_subject.editingFinished.connect(lambda: self._prop(lambda m: m.set_subject(self.p_subject.text())))
        self.p_comment.installEventFilter(self)
        self.p_status.activated.connect(lambda i: self._prop(lambda m: m.set_status(STATUSES[i])))
        self.p_width.editingFinished.connect(lambda: self._prop(lambda m: m.set_width(self.p_width.value())))
        self.p_opacity.editingFinished.connect(lambda: self._prop(lambda m: m.set_opacity(self.p_opacity.value())))
        self.p_stroke.clicked.connect(lambda: self._prop_color("stroke"))
        self.p_fill.clicked.connect(lambda: self._prop_color("fill"))
        self.p_reply.returnPressed.connect(self._add_reply)
        self.d_props = self._dock("Properties", pw, QtCore.Qt.RightDockWidgetArea)

        cw = W.QWidget()
        cl = W.QVBoxLayout(cw)
        self.chest_list = W.QListWidget()
        self.chest_list.itemClicked.connect(self._arm_chest)
        cl.addWidget(W.QLabel("Click an item, then click on the page to place it."))
        cl.addWidget(self.chest_list)
        row = W.QHBoxLayout()
        for text, fn in (("Add selected markup", self.chest_add), ("Remove", self.chest_remove)):
            b = W.QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        cl.addLayout(row)
        self.d_chest = self._dock("Tool Chest", cw, QtCore.Qt.RightDockWidgetArea)
        self.tabifyDockWidget(self.d_props, self.d_chest)
        self.d_props.raise_()
        self._refresh_chest()

        sw = W.QWidget()
        sl = W.QVBoxLayout(sw)
        self.search_edit = W.QLineEdit(placeholderText="Search text (Enter)…")
        self.search_edit.returnPressed.connect(self.do_search)
        self.search_list = W.QListWidget()
        self.search_list.itemClicked.connect(self._goto_hit)
        sl.addWidget(self.search_edit)
        sl.addWidget(self.search_list)
        self.d_search = self._dock("Search", sw, QtCore.Qt.LeftDockWidgetArea)
        self.tabifyDockWidget(self.d_toc, self.d_search)
        self._hits: list = []

    def eventFilter(self, obj, ev):
        if obj is self.p_comment and ev.type() == QtCore.QEvent.FocusOut:
            self._prop(lambda m: m.set_comment(self.p_comment.toPlainText()), only_if_changed=True)
        return super().eventFilter(obj, ev)

    def _act(self, menu, text, fn, key=None, checkable=False):
        a = menu.addAction(text)
        a.triggered.connect(lambda _=False, f=fn: f())
        if key:
            a.setShortcut(QtGui.QKeySequence(key))
        a.setCheckable(checkable)
        return a

    def _build_actions(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        self._act(f, "Open…", self.open_dialog, "Ctrl+O")
        self._act(f, "New from images…", self.new_from_images)
        self._act(f, "Save", self.save, "Ctrl+S")
        self._act(f, "Save As…", self.save_as, "Ctrl+Shift+S")
        self._act(f, "Save encrypted copy…", self.save_encrypted)
        self._act(f, "Optimize / compress copy…", self.optimize)
        f.addSeparator()
        self._act(f, "Export page as PNG…", self.export_png)
        self._act(f, "Export markups list (CSV)…", lambda: self._export("csv"))
        self._act(f, "Export measurement summary (CSV)…", lambda: self._export("summary"))
        self._act(f, "Print…", self.print_doc, "Ctrl+P")
        f.addSeparator()
        self._act(f, "Close tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        self._act(f, "Quit", self.close, "Ctrl+Q")

        e = mb.addMenu("&Edit")
        self._act(e, "Undo", self.undo, "Ctrl+Z")
        self._act(e, "Redo", self.redo, "Ctrl+Y")
        e.addSeparator()
        self._act(e, "Copy markup", self.copy_markup, "Ctrl+C")
        self._act(e, "Paste markup", self.paste_markup, "Ctrl+V")
        self._act(e, "Duplicate markup", lambda: (self.copy_markup(), self.paste_markup()), "Ctrl+D")
        self._act(e, "Find…", lambda: (self.d_search.show(), self.d_search.raise_(), self.search_edit.setFocus()), "Ctrl+F")
        self._act(e, "Next result", self.next_hit, "F3")

        v = mb.addMenu("&View")
        self._act(v, "Zoom in", lambda: self.cv and self.cv.set_zoom(self.cv.zoom * 1.25), "Ctrl+=")
        self._act(v, "Zoom out", lambda: self.cv and self.cv.set_zoom(self.cv.zoom / 1.25), "Ctrl+-")
        self._act(v, "Fit width", lambda: self.cv and self.cv.fit_width(), "Ctrl+0")
        self._act(v, "Next page", lambda: self.cv and self.cv.goto_page(self.cv.current_page() + 1), "PgDown")
        self._act(v, "Previous page", lambda: self.cv and self.cv.goto_page(self.cv.current_page() - 1), "PgUp")
        for d in (self.d_thumbs, self.d_toc, self.d_search, self.d_markups, self.d_props, self.d_chest):
            v.addAction(d.toggleViewAction())

        d = mb.addMenu("&Document")
        for text, fn in (("Insert blank page…", self.insert_blank), ("Insert pages from PDF…", self.insert_pdf),
                         ("Insert image as page…", self.insert_image), ("Delete pages…", self.delete_pages),
                         ("Rotate pages…", self.rotate_pages), ("Move page…", self.move_page),
                         ("Extract pages…", self.extract_pages), ("Split document…", self.split_doc),
                         ("Crop page…", self.crop_page), ("Set page labels…", self.page_labels)):
            self._act(d, text, fn)
        d.addSeparator()
        for text, fn in (("Header / footer…", self.header_footer), ("Watermark (text)…", self.watermark),
                         ("Watermark (image)…", self.watermark_image), ("Bates numbering…", self.bates),
                         ("Flatten markups", self.flatten), ("Apply redactions", self.apply_redactions),
                         ("Redact text matches…", self.redact_text), ("OCR (needs tesseract)…", self.ocr),
                         ("Auto-bookmark sheets", self.auto_bookmarks), ("Auto-link sheet references", self.auto_links),
                         ("Properties…", self.metadata), ("Compare with another PDF…", self.compare),
                         ("Fill form field…", self.fill_form), ("Layers…", self.layers_dialog)):
            self._act(d, text, fn)

        m = mb.addMenu("&Measure")
        self._act(m, "Calibrate scale (drag known length)", lambda: self.set_tool("Calibrate"))
        self._act(m, "Set scale by ratio…", self.scale_ratio)
        self._act(m, "Show scales", self.show_scales)

        s = mb.addMenu("&Sign")
        self._act(s, "Place visual signature", lambda: self.set_tool("Signature"))
        self._act(s, "Digitally sign (PKCS#12)…", self.digital_sign)
        self._act(s, "Verify signatures…", self.verify_signatures)

        h = mb.addMenu("&Help")
        self._act(h, "About", lambda: W.QMessageBox.about(
            self, "OpenRevu", f"OpenRevu {__version__}\nFree PDF markup, measurement and review tool.\nAGPL-3.0-or-later."))

        self.tool_group = W.QActionGroup(self)
        self.tool_actions: dict[str, QtGui.QAction] = {}
        for title, tools in (("Markup", MARKUP_TOOLS), ("Measure", MEASURE_TOOLS)):
            tb = self.addToolBar(title)
            tb.setObjectName(title)
            for name in tools:
                a = tb.addAction(name)
                a.setCheckable(True)
                a.setChecked(name == "Select")
                a.triggered.connect(lambda _, n=name: self.set_tool(n))
                self.tool_group.addAction(a)
                self.tool_actions[name] = a
            self.addToolBarBreak() if title == "Markup" else None

    def _build_options_bar(self):
        self.addToolBarBreak()
        tb = self.addToolBar("Options")
        tb.setObjectName("Options")
        self.color_btn = W.QPushButton("Colour")
        self._paint_btn(self.color_btn, self.color)
        self.color_btn.clicked.connect(self.pick_color)
        self.w_width = W.QDoubleSpinBox(minimum=0.25, maximum=40, singleStep=0.5, value=1.5, prefix="Width ")
        self.w_opacity = W.QDoubleSpinBox(minimum=0.05, maximum=1, singleStep=0.05, value=1, prefix="Opacity ")
        self.w_stamp = W.QComboBox(editable=True)
        self.w_stamp.addItems(STAMPS)
        self.w_group = W.QLineEdit("Count", maximumWidth=110)
        self.w_depth = W.QDoubleSpinBox(minimum=0.001, maximum=1e6, value=1, decimals=3, prefix="Depth ")
        self.page_spin = W.QSpinBox(minimum=1, maximum=1)
        self.page_lbl = W.QLabel(" / 0 ")
        for w in (self.color_btn, self.w_width, self.w_opacity):
            tb.addWidget(w)
        tb.addSeparator()
        for lbl, w in (("Stamp ", self.w_stamp), (" Count group ", self.w_group)):
            tb.addWidget(W.QLabel(lbl))
            tb.addWidget(w)
        tb.addWidget(self.w_depth)
        tb.addSeparator()
        tb.addWidget(W.QLabel("Page "))
        tb.addWidget(self.page_spin)
        tb.addWidget(self.page_lbl)
        self.page_spin.editingFinished.connect(lambda: self.cv and self.cv.goto_page(self.page_spin.value() - 1))
        for w, attr in ((self.w_width, "width"), (self.w_opacity, "opacity"), (self.w_depth, "depth")):
            w.valueChanged.connect(lambda v, a=attr: self.cv and setattr(self.cv, a, v))
        self.w_stamp.currentTextChanged.connect(lambda t: self.cv and setattr(self.cv, "stamp_text", t))
        self.w_group.textChanged.connect(lambda t: self.cv and setattr(self.cv, "count_group", t))

    @staticmethod
    def _paint_btn(btn, rgb):
        btn.setStyleSheet("background-color: rgb(%d,%d,%d)" % tuple(int(v * 255) for v in rgb))

    # ---------- tabs / files ----------
    def open_dialog(self):
        ps, _ = W.QFileDialog.getOpenFileNames(self, "Open PDF", "", "PDF (*.pdf)")
        for p in ps:
            self.open(p)

    def open(self, path, password=None):
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).doc.path == path:
                self.tabs.setCurrentIndex(i)
                return
        try:
            d = Document(path, password)
        except PermissionError:
            pw, ok = W.QInputDialog.getText(self, "Password", f"Password for {os.path.basename(path)}:", W.QLineEdit.Password)
            return self.open(path, pw) if ok else None
        except Exception as e:  # unreadable / corrupt
            return W.QMessageBox.critical(self, "Open failed", str(e))
        self._attach(d, os.path.basename(path))

    def _attach(self, d: Document, title):
        cv = Canvas(d)
        cv.status.connect(self.statusBar().showMessage)
        cv.changed.connect(self._after_change)
        cv.selection_changed.connect(self._load_props)
        cv.page_changed.connect(self._page_changed)
        cv.color, cv.width, cv.opacity = self.color, self.w_width.value(), self.w_opacity.value()
        cv.stamp_text, cv.count_group, cv.depth = self.w_stamp.currentText(), self.w_group.text(), self.w_depth.value()
        cv.tool = next((n for n, a in self.tool_actions.items() if a.isChecked()), "Select")
        i = self.tabs.addTab(cv, title)
        self.tabs.setCurrentIndex(i)
        self.statusBar().showMessage(f"{title}: {d.page_count} page(s) — pick a tool from the toolbar")

    def new_from_images(self):
        ps, _ = W.QFileDialog.getOpenFileNames(self, "Images", "", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")
        if ps:
            try:
                self._attach(Document.from_images(ps), "Untitled")
            except ERRORS as e:
                W.QMessageBox.warning(self, "OpenRevu", str(e))

    def close_tab(self, i):
        if i < 0:
            return
        cv = self.tabs.widget(i)
        if cv.doc.modified:
            r = W.QMessageBox.question(self, "Unsaved changes", "Save changes before closing?",
                                       W.QMessageBox.Save | W.QMessageBox.Discard | W.QMessageBox.Cancel)
            if r == W.QMessageBox.Cancel:
                return
            if r == W.QMessageBox.Save and not self._save(cv.doc):
                return
        self.tabs.removeTab(i)
        cv.deleteLater()
        if not self.tabs.count():
            self._tab_changed(-1)

    def closeEvent(self, ev):
        while self.tabs.count():
            n = self.tabs.count()
            self.close_tab(0)
            if self.tabs.count() == n:
                return ev.ignore()
        ev.accept()

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        for u in ev.mimeData().urls():
            if u.toLocalFile().lower().endswith(".pdf"):
                self.open(u.toLocalFile())

    def _save(self, d: Document) -> bool:
        try:
            if d.path:
                d.save()
            else:
                p, _ = W.QFileDialog.getSaveFileName(self, "Save", "", "PDF (*.pdf)")
                if not p:
                    return False
                d.save(p)
                self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(p))
        except ERRORS as e:
            W.QMessageBox.warning(self, "Save failed", str(e))
            return False
        self.statusBar().showMessage("Saved")
        return True

    def save(self):
        if self._need_doc():
            self.cv.select(None)  # save renumbers objects; drop the selection rather than risk a stale one
            self._save(self.doc)
            self.cv.reload()
            self._after_change()

    def save_as(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getSaveFileName(self, "Save As", "", "PDF (*.pdf)")
            if p:
                self._run(self.doc.save, p, msg=f"Saved {p}")
                self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(p))

    def save_encrypted(self):
        if not self._need_doc():
            return
        pw, ok = W.QInputDialog.getText(self, "Encrypt", "Password to open the copy:", W.QLineEdit.Password)
        if ok and pw:
            p, _ = W.QFileDialog.getSaveFileName(self, "Save encrypted copy", "", "PDF (*.pdf)")
            if p:
                self._run(self.doc.save_encrypted, p, pw, msg="Encrypted copy saved")

    def optimize(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getSaveFileName(self, "Optimized copy", "", "PDF (*.pdf)")
            if p:
                r = self._run(self.doc.optimize, p)
                if r:
                    self.statusBar().showMessage(f"{r[0]:,} → {r[1]:,} bytes")

    def _export(self, kind):
        if self._need_doc():
            p, _ = W.QFileDialog.getSaveFileName(self, "Export CSV", f"{kind}.csv", "CSV (*.csv)")
            if p:
                self._run(self.doc.export_csv if kind == "csv" else self.doc.export_summary_csv, p)

    def export_png(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getSaveFileName(self, "Export PNG", "page.png", "PNG (*.png)")
            if p:
                self._run(self.doc.export_png, self.cv.current_page(), p, 200)

    def print_doc(self):
        if not self._need_doc():
            return
        from PyQt5 import QtPrintSupport as P
        pr = P.QPrinter(P.QPrinter.HighResolution)
        if P.QPrintDialog(pr, self).exec_() != W.QDialog.Accepted:
            return
        painter = QtGui.QPainter(pr)
        for i in range(self.doc.page_count):
            if i:
                pr.newPage()
            pix = self.doc.render(i, pr.resolution() / 72)
            img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888)
            rect = painter.viewport()
            sz = img.size().scaled(rect.size(), QtCore.Qt.KeepAspectRatio)
            painter.setViewport(rect.x(), rect.y(), sz.width(), sz.height())
            painter.setWindow(img.rect())
            painter.drawImage(0, 0, img)
            painter.setViewport(rect)
            painter.setWindow(rect)
        painter.end()

    # ---------- tool / options ----------
    def set_tool(self, name):
        if name in self.tool_actions:
            self.tool_actions[name].setChecked(True)
        for i in range(self.tabs.count()):
            c = self.tabs.widget(i)
            c.tool = name
            c.cancel()
        hint = {"Length": "click points, double-click to finish", "Area": "click points, double-click to finish",
                "Perimeter": "click points, double-click to finish", "Volume": "click points, double-click; uses Depth",
                "Polyline": "click points, double-click to finish", "Angle": "click 3 points (vertex is the 2nd)",
                "Calibrate": "drag across a known dimension", "Fill": "click inside an enclosed room (Dynamic Fill)", "Stamp": "click or drag to place",
                "Redact": "drag area, then Document ▸ Apply redactions"}.get(name, "")
        self.statusBar().showMessage(f"{name}  {hint}")

    def pick_color(self):
        c = W.QColorDialog.getColor(QtGui.QColor(*[int(v * 255) for v in self.color]), self)
        if c.isValid():
            self.color = (c.redF(), c.greenF(), c.blueF())
            self._paint_btn(self.color_btn, self.color)
            for i in range(self.tabs.count()):
                self.tabs.widget(i).color = self.color

    # ---------- refresh ----------
    def _tab_changed(self, _i):
        self.refresh_all()

    def _page_changed(self, i):
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)

    def _after_change(self):
        self.refresh_markups()
        self._load_props()
        self.tabs.setTabText(self.tabs.currentIndex(), self._title())

    def _title(self):
        d = self.doc
        return ("*" if d.modified else "") + (os.path.basename(d.path) if d and d.path else "Untitled")

    def refresh_all(self):
        self.refresh_markups()
        self.refresh_thumbs()
        self.refresh_toc()
        self._load_props()
        d = self.doc
        n = d.page_count if d else 0
        self.page_spin.setMaximum(max(1, n))
        self.page_lbl.setText(f" / {n} ")
        self.setWindowTitle(f"OpenRevu — {d.path}" if d and d.path else "OpenRevu")

    def refresh_markups(self):
        self._rows = []
        t = self.mk_table
        t.setSortingEnabled(False)
        t.setRowCount(0)
        d = self.doc
        if d:
            q, st = self.mk_filter.text().lower(), self.mk_status.currentIndex()
            for m in d.markups():
                status = m.status
                if st and (STATUSES[st - 1] != status):
                    continue
                r = m.measurement()
                val = format_value(*r) if r else ""
                row = [str(m.page_no + 1), m.subject, m.kind, m.author, status, m.comment, val]
                if q and q not in " ".join(row).lower():
                    continue
                i = t.rowCount()
                t.insertRow(i)
                for c, txt in enumerate(row):
                    it = W.QTableWidgetItem(txt)
                    if c == 0:
                        it.setData(QtCore.Qt.UserRole, len(self._rows))
                        it.setData(QtCore.Qt.DisplayRole, int(txt))
                    t.setItem(i, c, it)
                self._rows.append(m)
        t.setSortingEnabled(True)
        if d:
            lines = [f"{k.title()}: {format_value(k, v, u)}" for (k, u), v in sorted(d.takeoff().items())]
            self.totals.setText("Totals — " + ("; ".join(lines) if lines else "no measurements"))
        else:
            self.totals.setText("")

    def _table_select(self):
        rows = self.mk_table.selectionModel().selectedRows()
        if rows and self.cv:
            idx = self.mk_table.item(rows[0].row(), 0).data(QtCore.Qt.UserRole)
            m = self._rows[idx]
            self.cv.select(m)
            r = m.rect
            self.cv.goto_page(m.page_no, max(0, r.y0 - 80))

    def refresh_thumbs(self):
        self.thumbs.clear()
        d = self.doc
        if not d:
            return
        for i in range(d.page_count):
            self.thumbs.addItem(W.QListWidgetItem(f"{d.page_label(i) or i + 1}"))
        self._thumb_next = 0
        self._thumb_doc = d
        QtCore.QTimer.singleShot(0, self._thumb_tick)

    def _thumb_tick(self):
        d = getattr(self, "_thumb_doc", None)
        if d is None or d is not self.doc or self.thumbs.count() != d.page_count:
            return
        for _ in range(4):
            i = self._thumb_next
            if i >= d.page_count:
                return
            try:
                pix = d.thumbnail(i, 110)
            except Exception:
                return
            img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888)
            self.thumbs.item(i).setIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(img.copy())))
            self._thumb_next += 1
        QtCore.QTimer.singleShot(5, self._thumb_tick)

    def refresh_toc(self):
        self.toc.clear()
        d = self.doc
        if not d:
            return
        stack: list = []
        for lvl, title, page in d.toc():
            item = W.QTreeWidgetItem([title])
            item.setData(0, QtCore.Qt.UserRole, max(1, page))
            while len(stack) >= lvl:
                stack.pop()
            (stack[-1] if stack else self.toc.invisibleRootItem()).addChild(item)
            stack.append(item)
        self.toc.expandAll()

    def _thumb_menu(self, pos):
        if not self.cv:
            return
        rows = sorted({self.thumbs.row(i) for i in self.thumbs.selectedItems()})
        if not rows:
            return
        m = W.QMenu(self)
        acts = {
            m.addAction("Rotate clockwise"): lambda: self._run(self.doc.rotate_pages, rows, 90, structural=True),
            m.addAction("Rotate counter-clockwise"): lambda: self._run(self.doc.rotate_pages, rows, -90, structural=True),
            m.addAction("Delete page(s)"): lambda: self._run(self.doc.delete_pages, rows, structural=True),
            m.addAction("Insert blank page after"): lambda: self._run(self.doc.insert_blank, rows[-1] + 1, structural=True),
            m.addAction("Move up"): lambda: self._run(self.doc.move_page, rows[0], max(0, rows[0] - 1), structural=True),
            m.addAction("Move down"): lambda: self._run(self.doc.move_page, rows[0], min(self.doc.page_count - 1, rows[0] + 1), structural=True),
        }
        a = m.exec_(self.thumbs.mapToGlobal(pos))
        if a in acts:
            acts[a]()
            self.refresh_thumbs()

    # ---------- properties ----------
    def _load_props(self):
        m = self.cv.selected if self.cv else None
        for w in (self.p_subject, self.p_comment, self.p_status, self.p_width, self.p_opacity, self.p_stroke,
                  self.p_fill, self.p_reply):
            w.setEnabled(m is not None)
            w.blockSignals(True)
        self.p_replies.clear()
        if m is not None:
            try:
                self.p_subject.setText(m.subject)
                self.p_comment.setPlainText(m.comment)
                self.p_status.setCurrentIndex(STATUSES.index(m.status) if m.status in STATUSES else 0)
                self.p_width.setValue(m.annot.border.get("width", 1) or 1)
                self.p_opacity.setValue(m.annot.opacity if m.annot.opacity >= 0 else 1)
                for a, t in m.replies():
                    self.p_replies.addItem(f"{a}: {t}")
            except Exception:
                pass
        else:
            self.p_subject.clear(); self.p_comment.clear()
        for w in (self.p_subject, self.p_comment, self.p_status, self.p_width, self.p_opacity, self.p_stroke,
                  self.p_fill, self.p_reply):
            w.blockSignals(False)

    def _prop(self, fn, only_if_changed=False):
        m = self.cv.selected if self.cv else None
        if m is None:
            return
        if only_if_changed and self.p_comment.toPlainText() == m.comment:
            return
        try:
            fn(m)
        except ERRORS as e:
            return self.statusBar().showMessage(str(e))
        self.cv.invalidate([m.page_no])
        self._after_change()

    def _prop_color(self, which):
        c = W.QColorDialog.getColor(parent=self)
        if c.isValid():
            rgb = (c.redF(), c.greenF(), c.blueF())
            self._prop(lambda m: m.set_colors(**{which: rgb}))

    def _add_reply(self):
        m = self.cv.selected if self.cv else None
        if m is not None and self.p_reply.text().strip():
            self.doc.add_reply(m, self.p_reply.text().strip())
            self.p_reply.clear()
            self.cv.invalidate([m.page_no])
            self._after_change()

    # ---------- edit ----------
    def undo(self):
        if self._need_doc():
            if self.doc.undo():
                self.cv.reload(); self._after_change(); self.refresh_thumbs(); self.refresh_toc()
            else:
                self.statusBar().showMessage("Nothing to undo")

    def redo(self):
        if self._need_doc():
            if self.doc.redo():
                self.cv.reload(); self._after_change(); self.refresh_thumbs(); self.refresh_toc()

    def copy_markup(self):
        m = self.cv.selected if self.cv else None
        if m is None:
            return
        try:
            self.clipboard = m.to_dict()
        except ERRORS as e:
            self.statusBar().showMessage(str(e))

    def paste_markup(self):
        if not (self._need_doc() and self.clipboard):
            return
        pno = self.cv.current_page()
        self._run(self.doc.add_from_dict, pno, self.clipboard, (12, 12), msg="Pasted")

    # ---------- bookmarks / tool chest / search ----------
    def add_bookmark(self):
        if self._need_doc():
            t, ok = W.QInputDialog.getText(self, "Bookmark", "Title:")
            if ok and t:
                if self._run(self.doc.add_bookmark, t, self.cv.current_page()):
                    self.refresh_toc()

    def remove_bookmark(self):
        it = self.toc.currentItem()
        if it and self._need_doc():
            toc = [e for e in self.doc.toc() if not (e[1] == it.text(0) and e[2] == it.data(0, QtCore.Qt.UserRole))]
            if self._run(self.doc.set_toc, toc):
                self.refresh_toc()

    def _refresh_chest(self):
        self.chest_list.clear()
        self.chest_list.addItems(sorted(self.chest.items))

    def chest_add(self):
        m = self.cv.selected if self.cv else None
        if m is None:
            return self.statusBar().showMessage("Select a markup first")
        n, ok = W.QInputDialog.getText(self, "Tool Chest", "Name:", text=m.subject)
        if ok and n:
            self.chest.add(n, m)
            self._refresh_chest()

    def chest_remove(self):
        it = self.chest_list.currentItem()
        if it:
            self.chest.remove(it.text())
            self._refresh_chest()

    def _arm_chest(self, it):
        if not self.cv:
            return
        name = it.text()

        def place(pno, pt):
            self.cv.tool_chest_item = None
            self._run(self.chest.place, self.doc, name, pno, pt)

        self.cv.tool_chest_item = place
        self.statusBar().showMessage(f"Click on the page to place '{name}'")

    def do_search(self):
        if not self._need_doc() or not self.search_edit.text():
            return
        self._hits = self.doc.search(self.search_edit.text())
        self.search_list.clear()
        for p, r in self._hits:
            self.search_list.addItem(f"Page {p + 1}   ({r.x0:.0f}, {r.y0:.0f})")
        self.cv.set_hits(self._hits)
        self.statusBar().showMessage(f"{len(self._hits)} match(es)")
        if self._hits:
            self._goto_hit_idx(0)

    def _goto_hit_idx(self, i):
        p, r = self._hits[i]
        self.search_list.setCurrentRow(i)
        self.cv.goto_page(p, max(0, r.y0 - 100))

    def _goto_hit(self, it):
        self._goto_hit_idx(self.search_list.row(it))

    def next_hit(self):
        if self._hits:
            self._goto_hit_idx((self.search_list.currentRow() + 1) % len(self._hits))

    # ---------- document operations ----------
    def _ask_pages(self, title, default=""):
        t, ok = W.QInputDialog.getText(self, title, f"Pages (e.g. 1-3,5) of {self.doc.page_count}:", text=default)
        if not ok:
            return None
        try:
            return parse_pages(t, self.doc.page_count)
        except ValueError as e:
            W.QMessageBox.warning(self, "OpenRevu", str(e))
            return None

    def _structural(self, fn, *a, **kw):
        if self._need_doc() and self._run(fn, *a, structural=True, **kw):
            self.refresh_thumbs()
            self.refresh_toc()
            self.page_spin.setMaximum(max(1, self.doc.page_count))
            self.page_lbl.setText(f" / {self.doc.page_count} ")

    def insert_blank(self):
        if self._need_doc():
            at, ok = W.QInputDialog.getInt(self, "Insert blank", "Insert before page:", self.cv.current_page() + 1, 1, self.doc.page_count + 1)
            if ok:
                self._structural(self.doc.insert_blank, at - 1)

    def insert_pdf(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getOpenFileName(self, "Insert PDF", "", "PDF (*.pdf)")
            if p:
                at, ok = W.QInputDialog.getInt(self, "Insert", "Insert before page:", self.doc.page_count + 1, 1, self.doc.page_count + 1)
                if ok:
                    self._structural(self.doc.insert_pdf, p, at - 1)

    def insert_image(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getOpenFileName(self, "Insert image", "", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")
            if p:
                self._structural(self.doc.insert_image_page, p)

    def delete_pages(self):
        if self._need_doc():
            pg = self._ask_pages("Delete pages", str(self.cv.current_page() + 1))
            if pg:
                self._structural(self.doc.delete_pages, pg)

    def rotate_pages(self):
        if self._need_doc():
            pg = self._ask_pages("Rotate pages", f"1-{self.doc.page_count}")
            if pg:
                deg, ok = W.QInputDialog.getItem(self, "Rotate", "Degrees clockwise:", ["90", "180", "270"], 0, False)
                if ok:
                    self._structural(self.doc.rotate_pages, pg, int(deg))

    def move_page(self):
        if self._need_doc():
            n = self.doc.page_count
            a, ok = W.QInputDialog.getInt(self, "Move page", "Move page:", self.cv.current_page() + 1, 1, n)
            if ok:
                b, ok = W.QInputDialog.getInt(self, "Move page", "To position:", 1, 1, n)
                if ok:
                    self._structural(self.doc.move_page, a - 1, b - 1)

    def extract_pages(self):
        if self._need_doc():
            pg = self._ask_pages("Extract pages")
            if pg:
                p, _ = W.QFileDialog.getSaveFileName(self, "Save extracted pages", "", "PDF (*.pdf)")
                if p:
                    self._run(self.doc.extract_pages, pg, p, msg=f"Extracted {len(pg)} page(s)")

    def split_doc(self):
        if self._need_doc():
            n, ok = W.QInputDialog.getInt(self, "Split", "Pages per file:", 1, 1, self.doc.page_count)
            if ok:
                d = W.QFileDialog.getExistingDirectory(self, "Output folder")
                if d:
                    r = self._run(self.doc.split, d, every=n)
                    if r:
                        self.statusBar().showMessage(f"Wrote {len(r)} files")

    def crop_page(self):
        if self._need_doc():
            pno = self.cv.current_page()
            r = self.doc.doc[pno].rect
            t, ok = W.QInputDialog.getText(self, "Crop", "x0,y0,x1,y1 in points:", text=f"{r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f}")
            if ok:
                try:
                    box = [float(v) for v in t.split(",")]
                    assert len(box) == 4
                except (ValueError, AssertionError):
                    return W.QMessageBox.warning(self, "OpenRevu", "Enter four numbers separated by commas")
                self._structural(self.doc.crop_page, pno, fitz.Rect(*box))

    def page_labels(self):
        if self._need_doc():
            t, ok = W.QInputDialog.getText(self, "Page labels", "Prefix (e.g. A-):")
            if ok:
                self._run(self.doc.set_page_labels, [{"startpage": 0, "prefix": t, "style": "D", "firstpagenum": 1}])
                self.refresh_thumbs()

    def _form_dialog(self, title, fields):
        dlg = W.QDialog(self)
        dlg.setWindowTitle(title)
        form = W.QFormLayout(dlg)
        edits = {}
        for name, default in fields:
            edits[name] = W.QLineEdit(default)
            form.addRow(name, edits[name])
        bb = W.QDialogButtonBox(W.QDialogButtonBox.Ok | W.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        return {k: e.text() for k, e in edits.items()} if dlg.exec_() == W.QDialog.Accepted else None

    def header_footer(self):
        if not self._need_doc():
            return
        v = self._form_dialog("Header / footer — tokens {page} {pages} {date}", [
            ("Header left", ""), ("Header centre", ""), ("Header right", ""),
            ("Footer left", ""), ("Footer centre", "Page {page} of {pages}"), ("Footer right", "")])
        if v:
            self._run(self.doc.header_footer, header=(v["Header left"], v["Header centre"], v["Header right"]),
                      footer=(v["Footer left"], v["Footer centre"], v["Footer right"]), name=self.doc.author)

    def watermark(self):
        if self._need_doc():
            t, ok = W.QInputDialog.getText(self, "Watermark", "Text:", text="DRAFT")
            if ok and t:
                self._run(self.doc.watermark, t)

    def watermark_image(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getOpenFileName(self, "Watermark image", "", "Images (*.png *.jpg *.jpeg)")
            if p:
                self._run(self.doc.watermark_image, p)

    def bates(self):
        if self._need_doc():
            v = self._form_dialog("Bates numbering", [("Prefix", ""), ("Start", "1"), ("Digits", "6")])
            if v:
                try:
                    self._run(self.doc.bates, v["Prefix"], int(v["Start"]), int(v["Digits"]))
                except ValueError:
                    W.QMessageBox.warning(self, "OpenRevu", "Start and digits must be whole numbers")

    def flatten(self):
        if self._need_doc():
            if W.QMessageBox.question(self, "Flatten", "Bake all markups into the pages? (Undo is available.)") == W.QMessageBox.Yes:
                self._run(self.doc.flatten, structural=True)

    def apply_redactions(self):
        if self._need_doc():
            if W.QMessageBox.question(self, "Apply redactions", "Permanently remove content under all redaction marks?") == W.QMessageBox.Yes:
                n = self._run(self.doc.apply_redactions, structural=True)
                self.statusBar().showMessage(f"{n or 0} redaction(s) applied")

    def redact_text(self):
        if self._need_doc():
            t, ok = W.QInputDialog.getText(self, "Redact text", "Mark every match of:")
            if ok and t:
                n = self._run(self.doc.mark_redaction_text, t)
                self.statusBar().showMessage(f"{n or 0} match(es) marked — apply with Document ▸ Apply redactions")

    def ocr(self):
        if self._need_doc():
            n = self._run(self.doc.ocr, structural=True)
            if n is not None:
                self.statusBar().showMessage(f"OCR added a text layer to {n} page(s)")

    def auto_bookmarks(self):
        if self._need_doc():
            n = self._run(self.doc.auto_bookmarks)
            if n is not None:
                self.refresh_toc()
                self.statusBar().showMessage(f"{n} sheet bookmark(s) created" if n else "No sheet numbers found")

    def auto_links(self):
        if self._need_doc():
            n = self._run(self.doc.auto_hyperlinks)
            if n is not None:
                self.statusBar().showMessage(f"{n} sheet reference link(s) created")

    def metadata(self):
        if self._need_doc():
            md = self.doc.doc.metadata or {}
            v = self._form_dialog("Document properties", [(k.title(), md.get(k) or "") for k in ("title", "author", "subject", "keywords")])
            if v:
                keep = (md.get("keywords") or "").split(";")
                hidden = [p for p in keep if p.startswith("OpenRevu-scale")]
                kw = v["Keywords"]
                self._run(self.doc.set_metadata, title=v["Title"], author=v["Author"], subject=v["Subject"],
                          keywords=";".join(([kw] if kw and "OpenRevu-scale" not in kw else []) + hidden))

    def compare(self):
        if not self._need_doc():
            return
        if not self.doc.path:
            return W.QMessageBox.information(self, "Compare", "Save the document first.")
        other, _ = W.QFileDialog.getOpenFileName(self, "Compare with (new version)", "", "PDF (*.pdf)")
        if not other:
            return
        out, _ = W.QFileDialog.getSaveFileName(self, "Save comparison as", "comparison.pdf", "PDF (*.pdf)")
        if out:
            try:
                stats = compare_pdfs(self.doc.path, other, out)
            except ERRORS as e:
                return W.QMessageBox.warning(self, "Compare", str(e))
            self.open(out)
            self.statusBar().showMessage("Changed: " + ", ".join(f"p{i + 1} {s:.1%}" for i, s in enumerate(stats) if s))

    def fill_form(self):
        if not self._need_doc():
            return
        fields = self.doc.form_fields()
        if not fields:
            return self.statusBar().showMessage("No form fields in this PDF")
        names = sorted({f["name"] for f in fields})
        n, ok = W.QInputDialog.getItem(self, "Form field", "Field:", names, 0, False)
        if ok:
            cur = next(f["value"] for f in fields if f["name"] == n)
            v, ok = W.QInputDialog.getText(self, n, "Value:", text=str(cur or ""))
            if ok:
                self._run(self.doc.set_form_value, n, v)

    def layers_dialog(self):
        if not self._need_doc():
            return
        ls = self.doc.layers()
        if not ls:
            return self.statusBar().showMessage("No layers in this PDF")
        dlg = W.QDialog(self)
        dlg.setWindowTitle("Layers")
        lay = W.QVBoxLayout(dlg)
        boxes = []
        for l in ls:
            cb = W.QCheckBox(l["text"] or f"Layer {l['number']}")
            cb.setChecked(l["on"])
            lay.addWidget(cb)
            boxes.append((l["number"], cb))
        bb = W.QDialogButtonBox(W.QDialogButtonBox.Ok | W.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec_() == W.QDialog.Accepted:
            for num, cb in boxes:
                self._run(self.doc.set_layer, num, cb.isChecked(), structural=True)

    # ---------- measure / sign ----------
    def scale_ratio(self):
        if not self._need_doc():
            return
        v = self._form_dialog("Scale ratio (paper : real)", [("Paper inches", "0.25"), ("Real length", "1"), ("Unit", "ft")])
        if not v:
            return
        scope, ok = W.QInputDialog.getItem(self, "Scale", "Apply to:", ["This page", "All pages"], 0, False)
        if not ok:
            return
        try:
            sc = Scale.from_ratio(float(v["Paper inches"]), float(v["Real length"]), v["Unit"])
        except ValueError as e:
            return W.QMessageBox.warning(self, "OpenRevu", str(e) or "Invalid numbers")
        if scope == "All pages":
            self.doc.set_scale(sc, reset_pages=True)
        else:
            self.doc.set_scale(sc, page=self.cv.current_page())
        self.statusBar().showMessage("Scale set")

    def show_scales(self):
        if self._need_doc():
            d = self.doc
            lines = [f"Default: 1 pt = {d.default_scale.unit_per_pt:.6g} {d.default_scale.unit}"]
            lines += [f"Page {p + 1}: 1 pt = {s.unit_per_pt:.6g} {s.unit}" for p, s in sorted(d.page_scales.items())]
            W.QMessageBox.information(self, "Scales", "\n".join(lines))

    def show_summary(self):
        if not self._need_doc():
            return
        rows = sorted(self.doc.takeoff_by_subject().items())
        dlg = W.QDialog(self)
        dlg.setWindowTitle("Measurement summary")
        dlg.resize(560, 360)
        lay = W.QVBoxLayout(dlg)
        t = W.QTableWidget(len(rows), 5)
        t.setHorizontalHeaderLabels(["Subject", "Measure", "Items", "Total", "Unit"])
        for i, ((s, k, u), (n, tot)) in enumerate(rows):
            for c, v in enumerate((s, k, str(n), f"{tot:.3f}", u)):
                t.setItem(i, c, W.QTableWidgetItem(v))
        lay.addWidget(t)
        b = W.QPushButton("Export CSV…")
        b.clicked.connect(lambda: self._export("summary"))
        lay.addWidget(b)
        dlg.exec_()

    def digital_sign(self):
        if not self._need_doc():
            return
        if not self.doc.path or self.doc.modified:
            return W.QMessageBox.information(self, "Sign", "Save the document first; signing works on the saved file.")
        p12, _ = W.QFileDialog.getOpenFileName(self, "Certificate (.p12/.pfx)", "", "PKCS#12 (*.p12 *.pfx)")
        if not p12:
            return
        pw, ok = W.QInputDialog.getText(self, "Certificate", "Passphrase:", W.QLineEdit.Password)
        if not ok:
            return
        out, _ = W.QFileDialog.getSaveFileName(self, "Signed copy", "signed.pdf", "PDF (*.pdf)")
        if out:
            from .sign import sign_pdf
            try:
                sign_pdf(self.doc.path, out, p12, pw, page=self.cv.current_page())
            except Exception as e:  # pyhanko raises many types
                return W.QMessageBox.warning(self, "Sign", str(e))
            self.open(out)

    def verify_signatures(self):
        if not self._need_doc() or not self.doc.path:
            return
        from .sign import verify_signatures
        roots, _ = W.QFileDialog.getOpenFileNames(self, "Trusted certificates (optional — Cancel for none)", "",
                                                  "Certificates (*.pem *.crt *.cer *.der)")
        try:
            sigs = verify_signatures(self.doc.path, roots)
        except Exception as e:
            return W.QMessageBox.warning(self, "Signatures", str(e))
        W.QMessageBox.information(self, "Signatures", "\n".join(
            f"{s['field']}: {s['signer']} — " + ("VALID and TRUSTED" if s["verdict_ok"] else
            f"NOT VERIFIED ({'intact' if s['intact'] else 'MODIFIED'}, {'trusted' if s['trusted'] else 'untrusted'}, "
            f"{'unchanged since signing' if s['whole_file'] else 'EDITED AFTER SIGNING'})") for s in sigs) or "No signatures")


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = W.QApplication(argv)
    win = Main(argv[1] if len(argv) > 1 else None)
    for extra in argv[2:]:
        win.open(extra)
    win.show()
    return app.exec_()
