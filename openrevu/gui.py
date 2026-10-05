"""PyQt5 application window, laid out after the Revu workflow:
start page, document tabs, tool-group toolbar, Properties toolbar, left/right panels, Markups List."""
from __future__ import annotations

import os
import sys

import fitz
from PyQt5 import QtCore, QtGui, QtWidgets as W

from . import __version__
from .canvas import Canvas
from .core import STATUSES, Document
from .gui_ops import ERRORS, DocumentOps, parse_pages  # noqa: F401  (parse_pages re-exported)
from .icons import tool_icon
from .toolchest import ToolChest

# tool groups shown as drop-down buttons (each remembers the last tool you used)
TOOL_GROUPS = {
    "Shapes": ["Rectangle", "Ellipse", "Line", "Arrow", "Polyline", "Cloud", "Pen"],
    "Text & Review": ["Text", "Callout", "Note", "Highlight", "Underline", "Strikeout", "Squiggly"],
    "Stamp & Sign": ["Stamp", "Signature", "Redact"],
    "Insert": ["Image", "Snapshot", "Link"],
    "Measure": ["Calibrate", "Length", "Perimeter", "Area", "Fill", "RectArea", "EllipseArea", "Volume",
                "Diameter", "Angle", "Count", "Viewport"],
}
LABELS = {"RectArea": "Rectangle area", "EllipseArea": "Ellipse area", "Fill": "Fill (room area)",
          "Calibrate": "Calibrate scale", "Length": "Length", "Viewport": "Viewport scale"}
SHORTCUTS = {"Select": "V", "Pan": "H", "Rectangle": "R", "Ellipse": "E", "Line": "L", "Arrow": "A",
             "Polyline": "Y", "Cloud": "C", "Pen": "P", "Text": "T", "Callout": "O", "Note": "N",
             "Highlight": "I", "Stamp": "S", "Length": "M", "Count": "K", "Fill": "F"}
PERSISTENT_TOOLS = {"Count"}  # these stay active after each placement
STAMPS = ["APPROVED", "REVIEWED", "REJECTED", "DRAFT", "FOR CONSTRUCTION", "VOID", "AS BUILT", "CONFIDENTIAL"]
ZOOMS = [25, 50, 75, 100, 125, 150, 200, 300, 400]
TOOL_HINTS = {
    "Select": "Click a markup to select it. Drag to move. Drag the corner handle to resize. Right-click for more.",
    "Pan": "Drag to move the page. You can also drag with the middle mouse button.",
    "Calibrate": "Drag across a dimension that you know, then enter its real length.",
    "Length": "Click each point. Double-click to finish.",
    "Perimeter": "Click each corner. Double-click to finish.",
    "Area": "Click each corner. Double-click to finish.",
    "Volume": "Click each corner. Double-click to finish. The volume uses the Depth value.",
    "Polyline": "Click each point. Double-click to finish.",
    "Angle": "Click three points. The second point is the vertex.",
    "Fill": "Click inside a closed room. Columns are subtracted.",
    "Viewport": "Drag a region that has its own scale, then enter the ratio (paper inches : real length : unit).",
    "Stamp": "Click or drag on the page to place the stamp.",
    "Image": "Click or drag on the page. You choose the picture next.",
    "Snapshot": "Drag a region. OpenRevu copies it to the clipboard as a picture.",
    "Link": "Drag a region. Then enter a web address or a page number.",
    "Redact": "Drag an area. Then use Document > Apply redactions to remove the content.",
    "Count": "Click each item to count. Set the group name in the toolbar.",
}
STYLE = """
QToolBar { spacing: 3px; padding: 2px 4px; border: 0; }
QToolButton { padding: 3px 5px; border: 1px solid transparent; border-radius: 4px; }
QToolButton:hover { background: #e6eefb; }
QToolButton:checked { background: #cfe2ff; border: 1px solid #7aa7e6; }
QDockWidget::title { padding: 5px 6px; background: #e8ebf0; }
QTabBar::tab { padding: 6px 8px; }
QTreeWidget::item { padding: 3px 0; }
"""


class Prefs:
    """User preferences (recent files, window layout). Kept in memory when OPENREVU_NO_SETTINGS is set."""

    def __init__(self):
        self.mem = {} if os.environ.get("OPENREVU_NO_SETTINGS") else None
        self.q = None if self.mem is not None else QtCore.QSettings("OpenRevu", "OpenRevu")

    def get(self, key, default=None):
        return self.mem.get(key, default) if self.mem is not None else self.q.value(key, default)

    def set(self, key, value):
        if self.mem is not None:
            self.mem[key] = value
        else:
            self.q.setValue(key, value)

    def recent(self) -> list[str]:
        v = self.get("recent", [])
        v = [v] if isinstance(v, str) else list(v or [])
        return [p for p in v if os.path.exists(p)]

    def add_recent(self, path: str):
        path = os.path.abspath(path)
        self.set("recent", ([path] + [p for p in self.recent() if p != path])[:10])


class StartPage(W.QWidget):
    """Shown when no document is open (like the Revu dashboard)."""
    open_requested = QtCore.pyqtSignal()
    images_requested = QtCore.pyqtSignal()
    file_requested = QtCore.pyqtSignal(str)

    def __init__(self, prefs: Prefs):
        super().__init__()
        self.prefs = prefs
        outer = W.QHBoxLayout(self)
        outer.addStretch(1)
        col = W.QVBoxLayout()
        col.setSpacing(14)
        title = W.QLabel("OpenRevu")
        title.setStyleSheet("font-size: 30px; font-weight: 600;")
        sub = W.QLabel("Mark up, measure, and review PDF drawings.")
        sub.setStyleSheet("color: #555; font-size: 14px;")
        col.addStretch(1)
        col.addWidget(title)
        col.addWidget(sub)
        row = W.QHBoxLayout()
        self.btn_open = W.QPushButton(tool_icon("open", 22), " Open PDF…")
        self.btn_images = W.QPushButton(tool_icon("images", 22), " New from images…")
        for b in (self.btn_open, self.btn_images):
            b.setMinimumHeight(40)
            b.setStyleSheet("font-size: 14px; padding: 4px 18px;")
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        col.addWidget(W.QLabel("<b>Recent files</b>"))
        self.recent = W.QListWidget()
        self.recent.setMinimumWidth(520)
        self.recent.setMaximumHeight(210)
        col.addWidget(self.recent)
        tips = W.QLabel(
            "<b>Quick start</b><br>"
            "1. Open a PDF.<br>"
            "2. Pick a tool in the toolbar, or in the Tool Chest panel.<br>"
            "3. Click or drag on the page.<br>"
            "4. To measure: pick <i>Calibrate</i>, drag across a known dimension, then pick a measure tool.<br>"
            "<br><b>Keys</b>: V select · H pan · R rectangle · C cloud · T text · M length · F room fill · "
            "Ctrl+Z undo · Ctrl+F search")
        tips.setStyleSheet("color: #444;")
        tips.setWordWrap(True)
        col.addWidget(tips)
        col.addStretch(2)
        outer.addLayout(col)
        outer.addStretch(1)
        self.btn_open.clicked.connect(self.open_requested)
        self.btn_images.clicked.connect(self.images_requested)
        self.recent.itemActivated.connect(lambda it: self.file_requested.emit(it.data(QtCore.Qt.UserRole)))
        self.refresh()

    def refresh(self):
        self.recent.clear()
        for p in self.prefs.recent():
            it = W.QListWidgetItem(f"{os.path.basename(p)}    —    {os.path.dirname(p)}")
            it.setData(QtCore.Qt.UserRole, p)
            self.recent.addItem(it)
        if not self.recent.count():
            it = W.QListWidgetItem("No recent files")
            it.setFlags(QtCore.Qt.NoItemFlags)
            self.recent.addItem(it)


class Main(DocumentOps, W.QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setWindowTitle("OpenRevu")
        self.resize(1480, 920)
        self.setStyleSheet(STYLE)
        self.prefs = Prefs()
        self.clipboard: dict | None = None
        self.chest = ToolChest()
        self.color = (1.0, 0.0, 0.0)
        self.fill_color = None
        self._rows: list = []
        self._hits: list = []
        self._busy = False
        self.tabs = W.QTabWidget(documentMode=True, tabsClosable=True, movable=True)
        self.start = StartPage(self.prefs)
        self.stack = W.QStackedWidget()
        self.stack.addWidget(self.start)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.start.open_requested.connect(self.open_dialog)
        self.start.images_requested.connect(self.new_from_images)
        self.start.file_requested.connect(self.open)
        self._build_panels()
        self._build_menus()
        self._build_toolbars()
        self._build_statusbar()
        self.setAcceptDrops(True)
        g, s = self.prefs.get("geometry"), self.prefs.get("state")
        if g and s:
            self.restoreGeometry(g)
            self.restoreState(s)
        self._sync_central()
        self.statusBar().showMessage("Open a PDF to begin (Ctrl+O)")
        if path:
            self.open(path)

    # ---------- current document ----------
    @property
    def cv(self) -> Canvas | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, Canvas) else None

    @property
    def doc(self) -> Document | None:
        return self.cv.doc if self.cv else None

    def _need_doc(self) -> bool:
        if not self.cv:
            self.statusBar().showMessage("No document is open.")
        return self.cv is not None

    def _run(self, fn, *a, structural=False, msg=None, **kw):
        """Run a document operation. Show errors in a dialog and refresh the window."""
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

    def _sync_central(self):
        docks = (self.left_dock, self.right_dock, self.d_markups)
        if hasattr(self, "props_bar"):
            self.props_bar.setEnabled(bool(self.tabs.count()))  # nothing to edit on the start page
        if self.tabs.count():
            self.stack.setCurrentIndex(1)
            if getattr(self, "_saved_dock_vis", None):  # bring the panels back after the start page
                for d, vis in zip(docks, self._saved_dock_vis):
                    d.setVisible(vis)
                self._saved_dock_vis = None
        else:
            self.stack.setCurrentIndex(0)
            if getattr(self, "_saved_dock_vis", None) is None:
                self._saved_dock_vis = [not d.isHidden() for d in docks]
                for d in docks:
                    d.hide()
            self.start.refresh()
            self.setWindowTitle("OpenRevu")

    # ---------- panels ----------
    def _side_tabs(self):
        t = W.QTabWidget()
        t.setTabPosition(W.QTabWidget.West)
        t.setDocumentMode(True)
        t.setIconSize(QtCore.QSize(22, 22))
        return t

    @staticmethod
    def _add_panel(tabs, widget, name, icon_name):
        """Icon-only tab. The name is kept in the tab data and the tooltip."""
        # Qt turns the contents of a west-side tab 90 degrees counter-clockwise; turn the icon back so it reads upright
        pm = tool_icon(icon_name, 22).pixmap(22, 22).transformed(QtGui.QTransform().rotate(90), QtCore.Qt.SmoothTransformation)
        i = tabs.addTab(widget, QtGui.QIcon(pm), "")
        tabs.setTabToolTip(i, name)
        tabs.tabBar().setTabData(i, name)
        return i

    @staticmethod
    def panel_name(tabs, i) -> str:
        return tabs.tabBar().tabData(i) or ""

    def _dock(self, title, widget, area):
        d = W.QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(widget)
        d.setFeatures(W.QDockWidget.DockWidgetClosable | W.QDockWidget.DockWidgetMovable)
        self.addDockWidget(area, d)
        return d

    def _build_panels(self):
        # ---- left: Thumbnails, Bookmarks, Search, Layers
        self.left_tabs = self._side_tabs()
        self.thumbs = W.QListWidget(viewMode=W.QListView.IconMode, iconSize=QtCore.QSize(100, 100),
                                    gridSize=QtCore.QSize(108, 128), resizeMode=W.QListView.Adjust,
                                    movement=W.QListView.Static, wordWrap=True)
        self.thumbs.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.thumbs.setSelectionMode(W.QAbstractItemView.ExtendedSelection)
        self.thumbs.itemClicked.connect(lambda it: self.cv and self.cv.goto_page(self.thumbs.row(it)))
        self.thumbs.customContextMenuRequested.connect(self._thumb_menu)
        self._add_panel(self.left_tabs, self.thumbs, "Thumbnails", "thumbnails")

        bw = W.QWidget()
        bl = W.QVBoxLayout(bw)
        self.toc = W.QTreeWidget(headerHidden=True)
        self.toc.itemClicked.connect(lambda it, _: self.cv and self.cv.goto_page(it.data(0, QtCore.Qt.UserRole) - 1))
        bl.addWidget(self.toc)
        row = W.QHBoxLayout()
        for text, icon, tip, fn in (("Add", "add", "Add a bookmark for the current page", self.add_bookmark),
                                    ("Remove", "delete", "Remove the selected bookmark", self.remove_bookmark),
                                    ("Auto", "auto", "Make bookmarks from the sheet numbers", self.auto_bookmarks)):
            b = W.QPushButton(tool_icon(icon, 18), text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        bl.addLayout(row)
        self._add_panel(self.left_tabs, bw, "Bookmarks", "bookmark")

        sw = W.QWidget()
        sl = W.QVBoxLayout(sw)
        self.search_edit = W.QLineEdit(placeholderText="Search text, then press Enter")
        self.search_edit.returnPressed.connect(self.do_search)
        self.search_list = W.QListWidget()
        self.search_list.itemClicked.connect(self._goto_hit)
        sl.addWidget(self.search_edit)
        sl.addWidget(self.search_list)
        self._add_panel(self.left_tabs, sw, "Search", "search")

        lw = W.QWidget()
        ll = W.QVBoxLayout(lw)
        self.layer_list = W.QListWidget()
        self.layer_list.itemChanged.connect(self._layer_toggled)
        ll.addWidget(W.QLabel("Tick a layer to show it."))
        ll.addWidget(self.layer_list)
        self._add_panel(self.left_tabs, lw, "Layers", "layers")
        shw = W.QWidget()
        shl = W.QVBoxLayout(shw)
        self.sheets_table = W.QTableWidget(0, 5)
        self.sheets_table.setHorizontalHeaderLabels(["Page", "Sheet", "Title", "Discipline", "Rev"])
        self.sheets_table.verticalHeader().hide()
        self.sheets_table.horizontalHeader().setStretchLastSection(True)
        self.sheets_table.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        self.sheets_table.setSelectionMode(W.QAbstractItemView.ExtendedSelection)
        self.sheets_table.setToolTip("Double-click Sheet, Title, or Discipline to edit. Click a row to go to the page.")
        self.sheets_table.itemChanged.connect(self._sheet_edited)
        self.sheets_table.itemSelectionChanged.connect(self._sheet_selected)
        shl.addWidget(self.sheets_table)
        srow = W.QHBoxLayout()
        for text, icon, tip, fn in (("Detect", "auto", "Find the sheet numbers and titles", self.detect_sheets),
                                    ("Revision", "add", "Add a revision to the selected sheets", self.add_revision_dialog),
                                    ("Index", "sheets", "Insert a sheet index page with links", self.insert_index_page),
                                    ("Slip", "open", "Replace sheets with a new version and keep the markups", self.slip_sheet_dialog)):
            b = W.QPushButton(tool_icon(icon, 18), text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f=fn: f())
            srow.addWidget(b)
        shl.addLayout(srow)
        bexp = W.QPushButton("Export sheet list (CSV)…")
        bexp.clicked.connect(self.export_sheet_csv)
        shl.addWidget(bexp)
        self._add_panel(self.left_tabs, shw, "Sheets", "sheets")
        self.left_tabs.currentChanged.connect(lambda _i: self.refresh_sheets())
        self.left_dock = self._dock("Panels", self.left_tabs, QtCore.Qt.LeftDockWidgetArea)

        # ---- right: Properties, Tool Chest, Measurements
        self.right_tabs = self._side_tabs()
        pw = W.QWidget()
        pouter = W.QVBoxLayout(pw)
        pform = W.QWidget()
        pl = W.QFormLayout(pform)
        pl.setContentsMargins(0, 0, 0, 0)
        pouter.addWidget(pform)
        pouter.addStretch(1)
        self.p_subject = W.QLineEdit()
        self.p_comment = W.QPlainTextEdit(maximumHeight=80)
        self.p_status = W.QComboBox()
        self.p_status.addItems([s or "(none)" for s in STATUSES])
        self.p_replies = W.QListWidget(maximumHeight=110)
        self.p_reply = W.QLineEdit(placeholderText="Write a reply, then press Enter")
        self.p_info = W.QLabel("Select a markup to see its properties.")
        self.p_info.setWordWrap(True)
        self.p_info.setStyleSheet("color: #666;")
        pl.addRow(self.p_info)
        self.p_custom_form = W.QFormLayout()          # one editor for each custom column
        self.p_custom_editors: dict = {}
        self._cols_sig = None
        for lbl, w in (("Subject", self.p_subject), ("Comment", self.p_comment), ("Status", self.p_status)):
            pl.addRow(lbl, w)
        pl.addRow(self.p_custom_form)
        for lbl, w in (("Replies", self.p_replies), ("", self.p_reply)):
            pl.addRow(lbl, w)
        self.p_subject.editingFinished.connect(lambda: self._prop(lambda m: m.set_subject(self.p_subject.text())))
        self.p_comment.installEventFilter(self)
        self.p_status.activated.connect(lambda i: self._prop(lambda m: m.set_status(STATUSES[i])))
        self.p_reply.returnPressed.connect(self._add_reply)
        self._add_panel(self.right_tabs, pw, "Properties", "properties")

        self.chest_tree = W.QTreeWidget(headerHidden=True, iconSize=QtCore.QSize(20, 20))
        self.chest_tree.itemClicked.connect(self._chest_clicked)
        cw = W.QWidget()
        cl = W.QVBoxLayout(cw)
        cl.addWidget(self.chest_tree)
        row = W.QHBoxLayout()
        for text, icon, tip, fn in (("Add selected", "add", "Save the selected markup as a tool", self.chest_add),
                                    ("Remove", "delete", "Remove the selected tool from My Tools", self.chest_remove)):
            b = W.QPushButton(tool_icon(icon, 18), text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        cl.addLayout(row)
        self._add_panel(self.right_tabs, cw, "Tool Chest", "toolchest")
        self._refresh_chest()

        mw = W.QWidget()
        ml = W.QVBoxLayout(mw)
        self.scale_lbl = W.QLabel()
        self.scale_lbl.setWordWrap(True)
        ml.addWidget(self.scale_lbl)
        row = W.QHBoxLayout()
        for text, icon, tip, fn in (("Calibrate", "Calibrate", "Drag across a known dimension", lambda: self.set_tool("Calibrate")),
                                    ("Ratio…", "measure", "Set the scale as a ratio", self.scale_ratio),
                                    ("Viewport", "Viewport", "Give a region of the page its own scale", lambda: self.set_tool("Viewport"))):
            b = W.QPushButton(tool_icon(icon, 18), text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        ml.addLayout(row)
        self.meas_table = W.QTableWidget(0, 4, editTriggers=W.QAbstractItemView.NoEditTriggers)
        self.meas_table.setHorizontalHeaderLabels(["Subject", "Items", "Total", "Unit"])
        self.meas_table.horizontalHeader().setStretchLastSection(True)
        self.meas_table.verticalHeader().hide()
        ml.addWidget(self.meas_table)
        b = W.QPushButton("Export summary (CSV)…")
        b.clicked.connect(lambda: self._export("summary"))
        ml.addWidget(b)
        self._add_panel(self.right_tabs, mw, "Measurements", "measure")
        self.right_dock = self._dock("Properties and Tools", self.right_tabs, QtCore.Qt.RightDockWidgetArea)

        # ---- bottom: Markups List
        mkw = W.QWidget()
        mkl = W.QVBoxLayout(mkw)
        frow = W.QHBoxLayout()
        self.mk_filter = W.QLineEdit(placeholderText="Filter markups…")
        self.mk_status = W.QComboBox()
        self.mk_status.addItems(["Any status"] + [s or "(none)" for s in STATUSES])
        self.totals = W.QLabel()
        frow.addWidget(self.mk_filter, 2)
        frow.addWidget(self.mk_status)
        frow.addWidget(self.totals, 3)
        mkl.addLayout(frow)
        self.mk_table = W.QTableWidget(0, 7, selectionBehavior=W.QAbstractItemView.SelectRows,
                                       editTriggers=W.QAbstractItemView.NoEditTriggers, sortingEnabled=True)
        self.mk_table.setHorizontalHeaderLabels(["Page", "Subject", "Type", "Author", "Status", "Comment", "Value"])
        self.mk_table.horizontalHeader().setStretchLastSection(True)
        self.mk_table.verticalHeader().hide()
        self.mk_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.mk_table.customContextMenuRequested.connect(self._table_menu)
        self.mk_table.itemSelectionChanged.connect(self._table_select)
        hh = self.mk_table.horizontalHeader()
        hh.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        hh.setToolTip("Right-click to add or remove a column")
        self.mk_filter.textChanged.connect(self.refresh_markups)
        self.mk_status.currentIndexChanged.connect(self.refresh_markups)
        mkl.addWidget(self.mk_table)
        self.d_markups = self._dock("Markups List", mkw, QtCore.Qt.BottomDockWidgetArea)
        self.resizeDocks([self.left_dock, self.right_dock], [230, 290], QtCore.Qt.Horizontal)
        self.resizeDocks([self.d_markups], [190], QtCore.Qt.Vertical)

    def show_panel(self, name: str):
        """Bring a panel to the front: Thumbnails, Bookmarks, Search, Layers, Properties, Tool Chest, Measurements."""
        for tabs, dock in ((self.left_tabs, self.left_dock), (self.right_tabs, self.right_dock)):
            for i in range(tabs.count()):
                if self.panel_name(tabs, i) == name:
                    dock.show()
                    tabs.setCurrentIndex(i)
                    return
        if name == "Markups List":
            self.d_markups.show()

    def eventFilter(self, obj, ev):
        if obj is self.p_comment and ev.type() == QtCore.QEvent.FocusOut:
            self._prop(lambda m: m.set_comment(self.p_comment.toPlainText()), only_if_changed=True)
        return super().eventFilter(obj, ev)

    # ---------- menus ----------
    def _act(self, menu, text, fn, key=None, icon=None):
        a = menu.addAction(text)
        a.triggered.connect(lambda _=False, f=fn: f())
        if key:
            a.setShortcut(QtGui.QKeySequence(key))
        if icon is not None:
            a.setIcon(icon)
        return a

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        self._act(f, "Open…", self.open_dialog, "Ctrl+O", tool_icon("open"))
        self._act(f, "New from images…", self.new_from_images)
        self._act(f, "Save", self.save, "Ctrl+S", tool_icon("save"))
        self._act(f, "Save As…", self.save_as, "Ctrl+Shift+S")
        self._act(f, "Save encrypted copy…", self.save_encrypted)
        self._act(f, "Optimize / compress copy…", self.optimize)
        self._act(f, "Export as PDF/A…", self.export_pdfa)
        f.addSeparator()
        self._act(f, "Export page as PNG…", self.export_png)
        self._act(f, "Export Markup Summary (PDF)…", self.export_summary_pdf)
        self._act(f, "Export markups list (CSV)…", lambda: self._export("csv"))
        self._act(f, "Export measurement summary (CSV)…", lambda: self._export("summary"))
        self._act(f, "Print…", self.print_doc, "Ctrl+P", tool_icon("print"))
        f.addSeparator()
        self._act(f, "Close tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        self._act(f, "Quit", self.close, "Ctrl+Q")

        e = mb.addMenu("&Edit")
        self.act_undo = self._act(e, "Undo", self.undo, "Ctrl+Z", tool_icon("undo"))
        self.act_redo = self._act(e, "Redo", self.redo, "Ctrl+Y", tool_icon("redo"))
        e.addSeparator()
        self._act(e, "Copy markup", self.copy_markup, "Ctrl+C", tool_icon("copy"))
        self._act(e, "Paste markup", self.paste_markup, "Ctrl+V")
        self._act(e, "Duplicate markup", lambda: (self.copy_markup(), self.paste_markup()), "Ctrl+D", tool_icon("duplicate"))
        self._act(e, "Delete markup", self.delete_selected, "Delete", tool_icon("delete"))
        e.addSeparator()
        self._act(e, "Find…", lambda: (self.show_panel("Search"), self.search_edit.setFocus()), "Ctrl+F", tool_icon("search"))
        self._act(e, "Next result", self.next_hit, "F3")

        v = mb.addMenu("&View")
        self._act(v, "Zoom in", lambda: self.cv and self.cv.set_zoom(self.cv.zoom * 1.25), "Ctrl+=", tool_icon("zoom-in"))
        self._act(v, "Zoom out", lambda: self.cv and self.cv.set_zoom(self.cv.zoom / 1.25), "Ctrl+-", tool_icon("zoom-out"))
        self._act(v, "Fit width", lambda: self.cv and self.cv.fit_width(), "Ctrl+0", tool_icon("fit-width"))
        self._act(v, "Fit page", lambda: self.cv and self.cv.fit_page(), "Ctrl+9", tool_icon("fit-page"))
        self._act(v, "Next page", lambda: self.cv and self.cv.goto_page(self.cv.current_page() + 1), "PgDown")
        self._act(v, "Previous page", lambda: self.cv and self.cv.goto_page(self.cv.current_page() - 1), "PgUp")
        v.addSeparator()
        for i, name in enumerate(("Thumbnails", "Bookmarks", "Search", "Layers"), 1):
            self._act(v, f"{name} panel", lambda n=name: self.show_panel(n), f"Alt+{i}")
        for i, name in enumerate(("Properties", "Tool Chest", "Measurements"), 5):
            self._act(v, f"{name} panel", lambda n=name: self.show_panel(n), f"Alt+{i}")
        self._act(v, "Markups List", lambda: self.show_panel("Markups List"), "Alt+9")
        v.addSeparator()
        for d in (self.left_dock, self.right_dock, self.d_markups):
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
        self._act(m, "Calibrate scale (drag a known length)", lambda: self.set_tool("Calibrate"))
        self._act(m, "Set scale by ratio…", self.scale_ratio)
        self._act(m, "Add viewport scale (drag a region)", lambda: self.set_tool("Viewport"))
        self.act_arch = self._act(m, "Show feet and inches (12' 6 1/2\")", self.toggle_arch_units)
        self.act_arch.setCheckable(True)
        self._act(m, "Show scales", self.show_scales)
        self._act(m, "Measurement summary…", self.show_summary)

        s = mb.addMenu("&Sign")
        self._act(s, "Place visual signature", lambda: self.set_tool("Signature"))
        self._act(s, "Digitally sign (PKCS#12)…", self.digital_sign)
        self._act(s, "Verify signatures…", self.verify_signatures)

        h = mb.addMenu("&Help")
        self._act(h, "About", lambda: W.QMessageBox.about(
            self, "OpenRevu", f"OpenRevu {__version__}\nFree PDF markup, measurement, and review tool.\nAGPL-3.0-or-later."))

    # ---------- toolbars ----------
    def _make_tool_action(self, name: str) -> W.QAction:
        label = LABELS.get(name, name)
        a = W.QAction(tool_icon(name), label, self)
        a.setCheckable(True)
        tip = TOOL_HINTS.get(name, "")
        key = SHORTCUTS.get(name)
        if key:
            a.setShortcut(QtGui.QKeySequence(key))
        a.setToolTip(f"{label}" + (f" ({key})" if key else "") + (f"\n{tip}" if tip else ""))
        a.triggered.connect(lambda _=False, n=name: self.set_tool(n))
        self.addAction(a)  # keeps the shortcut active even when the drop-down menu is closed
        return a

    def _build_toolbars(self):
        self.tool_group = W.QActionGroup(self)
        self.tool_actions: dict[str, W.QAction] = {}
        self.group_buttons: dict[str, W.QToolButton] = {}
        self.tool_to_group: dict[str, str] = {}
        st = self.style()

        tb = self.addToolBar("Main")
        tb.setObjectName("Main")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(22, 22))
        for icon, text, fn, tip in (("open", "Open", self.open_dialog, "Open (Ctrl+O)"),
                                    ("save", "Save", self.save, "Save (Ctrl+S)")):
            a = tb.addAction(tool_icon(icon), text)
            a.setToolTip(tip)
            a.triggered.connect(lambda _=False, f=fn: f())
        tb.addSeparator()
        ua = tb.addAction(tool_icon("undo"), "Undo")
        ua.setToolTip("Undo (Ctrl+Z)")
        ua.triggered.connect(lambda: self.undo())
        ra = tb.addAction(tool_icon("redo"), "Redo")
        ra.setToolTip("Redo (Ctrl+Y)")
        ra.triggered.connect(lambda: self.redo())
        tb.addSeparator()
        for name in ("Select", "Pan"):
            a = self._make_tool_action(name)
            self.tool_group.addAction(a)
            self.tool_actions[name] = a
            tb.addAction(a)
        self.tool_actions["Select"].setChecked(True)
        tb.addSeparator()
        for title, names in TOOL_GROUPS.items():
            btn = W.QToolButton()
            btn.setPopupMode(W.QToolButton.MenuButtonPopup)
            btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            btn.setMinimumWidth(120)
            menu = W.QMenu(btn)
            for n in names:
                a = self._make_tool_action(n)
                self.tool_group.addAction(a)
                self.tool_actions[n] = a
                self.tool_to_group[n] = title
                menu.addAction(a)
            btn.setMenu(menu)
            btn.setDefaultAction(self.tool_actions[names[0]])
            btn.setToolTip(f"{title}: click to use the current tool, or open the list.")
            self.group_buttons[title] = btn
            tb.addWidget(btn)
        tb.addSeparator()
        # zoom
        zo = tb.addAction(tool_icon("zoom-out"), "Zoom out")
        zo.setToolTip("Zoom out (Ctrl+-)")
        zo.triggered.connect(lambda: self.cv and self.cv.set_zoom(self.cv.zoom / 1.25))
        self.zoom_box = W.QComboBox(editable=True)
        self.zoom_box.setFixedWidth(84)
        self.zoom_box.addItems([f"{z}%" for z in ZOOMS])
        self.zoom_box.setCurrentText("100%")
        self.zoom_box.activated.connect(lambda _i: self._zoom_from_box())
        self.zoom_box.lineEdit().editingFinished.connect(self._zoom_from_box)
        tb.addWidget(self.zoom_box)
        zi = tb.addAction(tool_icon("zoom-in"), "Zoom in")
        zi.setToolTip("Zoom in (Ctrl+=)")
        zi.triggered.connect(lambda: self.cv and self.cv.set_zoom(self.cv.zoom * 1.25))
        for text, icon, tip, fn in (("Fit width", "fit-width", "Fit width (Ctrl+0)", lambda: self.cv and self.cv.fit_width()),
                                    ("Fit page", "fit-page", "Fit page (Ctrl+9)", lambda: self.cv and self.cv.fit_page())):
            a = tb.addAction(tool_icon(icon), text)
            a.setToolTip(tip)
            a.triggered.connect(lambda _=False, f=fn: f())
        tb.addSeparator()
        # page navigation
        pv = tb.addAction(tool_icon("page-up"), "Previous page")
        pv.setToolTip("Previous page (PgUp)")
        pv.triggered.connect(lambda: self.cv and self.cv.goto_page(self.cv.current_page() - 1))
        self.page_spin = W.QSpinBox(minimum=1, maximum=1)
        self.page_spin.setFixedWidth(64)
        self.page_lbl = W.QLabel(" / 0 ")
        tb.addWidget(self.page_spin)
        tb.addWidget(self.page_lbl)
        nx = tb.addAction(tool_icon("page-down"), "Next page")
        nx.setToolTip("Next page (PgDown)")
        nx.triggered.connect(lambda: self.cv and self.cv.goto_page(self.cv.current_page() + 1))
        self.page_spin.editingFinished.connect(lambda: self.cv and self.cv.goto_page(self.page_spin.value() - 1))

        # ---- Properties toolbar (second row)
        self.addToolBarBreak()
        pt = self.addToolBar("Properties")
        self.props_bar = pt
        pt.setObjectName("PropertiesBar")
        pt.setMovable(False)
        pt.addWidget(self._icon_label("line-colour", "Line colour"))
        self.btn_stroke = W.QToolButton()
        self.btn_stroke.setFixedSize(34, 22)
        self.btn_stroke.setToolTip("Line colour (applies to the selected markup, or to new markups)")
        self.btn_stroke.clicked.connect(self.pick_stroke)
        pt.addWidget(self.btn_stroke)
        pt.addWidget(self._icon_label("fill-colour", "Fill colour"))
        self.btn_fill = W.QToolButton()
        self.btn_fill.setFixedSize(34, 22)
        self.btn_fill.setPopupMode(W.QToolButton.InstantPopup)
        fm = W.QMenu(self.btn_fill)
        fm.addAction("No fill", lambda: self._set_fill(None))
        fm.addAction("Choose colour…", self.pick_fill)
        self.btn_fill.setMenu(fm)
        self.btn_fill.setToolTip("Fill colour for rectangles, ellipses, and the selected markup")
        pt.addWidget(self.btn_fill)
        self.w_width = W.QDoubleSpinBox(minimum=0.25, maximum=40, singleStep=0.5, value=1.5, prefix="Width ")
        self.w_opacity = W.QDoubleSpinBox(minimum=0.05, maximum=1, singleStep=0.05, value=1.0, prefix="Opacity ")
        for w in (self.w_width, self.w_opacity):
            w.setKeyboardTracking(False)
            pt.addWidget(w)
        self.w_width.valueChanged.connect(lambda v: self._set_numeric("width", v))
        self.w_opacity.valueChanged.connect(lambda v: self._set_numeric("opacity", v))
        pt.addSeparator()
        self.act_keep = pt.addAction(tool_icon("keep"), "Keep tool")
        self.act_keep.setCheckable(True)
        self.act_keep.setToolTip("Keep the tool selected after you place a markup.\nOff: the tool returns to Select, so you can adjust the markup.")
        pt.addSeparator()
        # tool options, shown only when relevant
        self.opt_stamp_lbl = W.QLabel(" Stamp ")
        self.w_stamp = W.QComboBox(editable=True)
        self.w_stamp.addItems(STAMPS)
        self.opt_group_lbl = W.QLabel(" Count group ")
        self.w_group = W.QLineEdit("Count")
        self.w_group.setMaximumWidth(120)
        self.w_depth = W.QDoubleSpinBox(minimum=0.001, maximum=1e6, value=1.0, decimals=3, prefix="Depth ")
        self.opt_widgets = {"Stamp": [pt.addWidget(self.opt_stamp_lbl), pt.addWidget(self.w_stamp)],
                            "Count": [pt.addWidget(self.opt_group_lbl), pt.addWidget(self.w_group)],
                            "Volume": [pt.addWidget(self.w_depth)]}
        self.w_stamp.currentTextChanged.connect(lambda t: self._each_canvas("stamp_text", t))
        self.w_group.textChanged.connect(lambda t: self._each_canvas("count_group", t))
        self.w_depth.valueChanged.connect(lambda v: self._each_canvas("depth", v))
        self._paint_swatch(self.btn_stroke, self.color)
        self._paint_swatch(self.btn_fill, None)
        self._show_tool_options("Select")

    @staticmethod
    def _icon_label(icon_name, tip):
        lbl = W.QLabel()
        lbl.setPixmap(tool_icon(icon_name, 18).pixmap(18, 18))
        lbl.setToolTip(tip)
        lbl.setContentsMargins(6, 0, 2, 0)
        return lbl

    @staticmethod
    def _paint_swatch(btn, rgb):
        if rgb is None:
            btn.setStyleSheet("background: white; border: 1px solid #888; background-image: none;")
            btn.setText("∅")
        else:
            btn.setText("")
            btn.setStyleSheet("background-color: rgb(%d,%d,%d); border: 1px solid #555;" % tuple(int(v * 255) for v in rgb))

    def _show_tool_options(self, tool: str):
        for key, acts in self.opt_widgets.items():
            for a in acts:
                a.setVisible(key == tool)

    def _each_canvas(self, attr, value):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, Canvas):
                setattr(w, attr, value)

    def _zoom_from_box(self):
        if not self.cv:
            return
        try:
            pct = float(self.zoom_box.currentText().strip().rstrip("%"))
        except ValueError:
            return self._update_status_widgets()
        self.cv.set_zoom(max(10.0, min(800.0, pct)) / 100.0 * 96 / 72)

    # ---------- status bar ----------
    def _build_statusbar(self):
        sb = self.statusBar()
        self.lbl_scale, self.lbl_page, self.lbl_zoom = W.QLabel(), W.QLabel(), W.QLabel()
        for lbl in (self.lbl_scale, self.lbl_page, self.lbl_zoom):
            lbl.setStyleSheet("padding: 0 10px;")
            sb.addPermanentWidget(lbl)

    def _update_status_widgets(self):
        cv = self.cv
        if not cv:
            for lbl in (self.lbl_scale, self.lbl_page, self.lbl_zoom):
                lbl.setText("")
            return
        d, p = cv.doc, cv.current_page()
        sc = d.scale_for(p)
        self.lbl_scale.setText(f"Scale: 1 in = {sc.unit_per_pt * 72:.4g} {sc.unit}" + (" (viewports on page)" if any(v[0] == p for v in d.viewports) else ""))
        self.lbl_page.setText(f"Page {p + 1} of {d.page_count}")
        pct = round(cv.zoom * 72 / 96 * 100)
        self.lbl_zoom.setText(f"{pct}%")
        self.zoom_box.blockSignals(True)
        self.zoom_box.setCurrentText(f"{pct}%")
        self.zoom_box.blockSignals(False)
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, d.page_count))
        self.page_spin.setValue(p + 1)
        self.page_spin.blockSignals(False)
        self.page_lbl.setText(f" / {d.page_count} ")
        sh = d.scale_for(p)
        self.scale_lbl.setText(f"<b>Page {p + 1}</b><br>1 in on paper = {sh.unit_per_pt * 72:.4g} {sh.unit}<br>"
                               f"Default: 1 in = {d.default_scale.unit_per_pt * 72:.4g} {d.default_scale.unit}<br>"
                               f"Viewports on this page: {sum(1 for v in d.viewports if v[0] == p)}")

    # ---------- tabs / files ----------
    def open_dialog(self):
        ps, _ = W.QFileDialog.getOpenFileNames(self, "Open PDF", "", "PDF (*.pdf)")
        for p in ps:
            self.open(p)

    def open(self, path, password=None):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, Canvas) and w.doc.path == path:
                self.tabs.setCurrentIndex(i)
                return
        try:
            d = Document(path, password)
        except PermissionError:
            pw, ok = W.QInputDialog.getText(self, "Password", f"Password for {os.path.basename(path)}:", W.QLineEdit.Password)
            return self.open(path, pw) if ok else None
        except Exception as e:  # unreadable or corrupt file
            return W.QMessageBox.critical(self, "Open failed", str(e))
        self.prefs.add_recent(path)
        self._attach(d, os.path.basename(path))

    def _attach(self, d: Document, title):
        cv = Canvas(d)
        cv.status.connect(self.statusBar().showMessage)
        cv.changed.connect(self._after_change)
        cv.selection_changed.connect(self._load_props)
        cv.page_changed.connect(lambda _i: self._update_status_widgets())
        cv.zoom_changed.connect(lambda _z: self._update_status_widgets())
        cv.tool_done.connect(self._tool_done)
        cv.context_requested.connect(self._canvas_menu)
        cv.color, cv.fill, cv.width, cv.opacity = self.color, self.fill_color, self.w_width.value(), self.w_opacity.value()
        cv.stamp_text, cv.count_group, cv.depth = self.w_stamp.currentText(), self.w_group.text(), self.w_depth.value()
        cv.set_tool(next((n for n, a in self.tool_actions.items() if a.isChecked()), "Select"))
        i = self.tabs.addTab(cv, title)
        self.tabs.setCurrentIndex(i)
        self._sync_central()
        self.refresh_all()
        QtCore.QTimer.singleShot(0, cv.fit_width)  # start at page width, like Revu
        self.statusBar().showMessage(f"{title}: {d.page_count} page(s). Pick a tool from the toolbar.")

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
            r = W.QMessageBox.question(self, "Unsaved changes", "Save changes before you close?",
                                       W.QMessageBox.Save | W.QMessageBox.Discard | W.QMessageBox.Cancel)
            if r == W.QMessageBox.Cancel:
                return
            if r == W.QMessageBox.Save and not self._save(cv.doc):
                return
        self.tabs.removeTab(i)
        cv.deleteLater()
        self._sync_central()
        if not self.tabs.count():
            self._tab_changed(-1)

    def closeEvent(self, ev):
        while self.tabs.count():
            n = self.tabs.count()
            self.close_tab(0)
            if self.tabs.count() == n:
                return ev.ignore()
        self.prefs.set("geometry", self.saveGeometry())
        self.prefs.set("state", self.saveState())
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
                self.prefs.add_recent(p)
        except ERRORS as e:
            W.QMessageBox.warning(self, "Save failed", str(e))
            return False
        self.statusBar().showMessage("Saved")
        return True

    def save(self):
        if self._need_doc():
            self.cv.select(None)  # a save renumbers objects, so drop the selection
            self._save(self.doc)
            self.cv.reload()
            self._after_change()

    def save_as(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getSaveFileName(self, "Save As", "", "PDF (*.pdf)")
            if p:
                self.cv.select(None)
                if self._run(self.doc.save, p, msg=f"Saved {p}"):
                    self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(p))
                    self.prefs.add_recent(p)

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
                    self.statusBar().showMessage(f"{r[0]:,} to {r[1]:,} bytes")

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

    # ---------- tools & the Properties toolbar ----------
    def set_tool(self, name, announce=True):
        if name in self.tool_actions:
            self.tool_actions[name].setChecked(True)
        grp = self.tool_to_group.get(name)
        if grp:
            self.group_buttons[grp].setDefaultAction(self.tool_actions[name])
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, Canvas):
                w.set_tool(name)
        self._show_tool_options(name)
        if announce:  # a silent switch keeps the result message of the tool that just finished
            hint = TOOL_HINTS.get(name, "")
            self.statusBar().showMessage(f"{LABELS.get(name, name)}. {hint}".strip())

    def _tool_done(self):
        """After a markup is placed, go back to Select (as in Revu) unless the tool is kept."""
        cv = self.cv
        if cv and cv.tool not in PERSISTENT_TOOLS and not self.act_keep.isChecked():
            self.set_tool("Select", announce=False)

    def toggle_arch_units(self):
        if not self._need_doc():
            return self.act_arch.setChecked(False)
        style = "feet-inches" if self.act_arch.isChecked() else "decimal"
        if self._run(self.doc.set_unit_style, style, self.doc.fraction) is not None:
            self.refresh_markups()
            self.statusBar().showMessage("Lengths in feet and inches." if style == "feet-inches" else "Decimal lengths.")

    def _selected(self):
        return self.cv.selected if self.cv else None

    def pick_stroke(self):
        c = W.QColorDialog.getColor(QtGui.QColor(*[int(v * 255) for v in self.color]), self)
        if c.isValid():
            self._set_stroke((c.redF(), c.greenF(), c.blueF()))

    def pick_fill(self):
        c = W.QColorDialog.getColor(parent=self)
        if c.isValid():
            self._set_fill((c.redF(), c.greenF(), c.blueF()))

    def _set_stroke(self, rgb):
        """Selected markup if there is one, otherwise the default for new markups."""
        if self._selected() is not None:
            self._prop(lambda m: m.set_colors(stroke=rgb))
        else:
            self.color = rgb
            self._each_canvas("color", rgb)
        self._paint_swatch(self.btn_stroke, rgb)

    def _set_fill(self, rgb):
        if self._selected() is not None:
            if rgb is not None:
                self._prop(lambda m: m.set_colors(fill=rgb))
        else:
            self.fill_color = rgb
            self._each_canvas("fill", rgb)
        self._paint_swatch(self.btn_fill, rgb)

    def _set_numeric(self, attr, value):
        if self._busy:
            return
        m = self._selected()
        if m is not None:
            self._prop((lambda m: m.set_width(value)) if attr == "width" else (lambda m: m.set_opacity(value)))
        else:
            self._each_canvas(attr, value)

    # ---------- refresh ----------
    def _tab_changed(self, _i):
        self.refresh_all()

    def _after_change(self):
        if self.doc:
            self.act_arch.setChecked(self.doc.unit_style == "feet-inches")  # undo and redo can change the style
        self.refresh_sheets()
        self.refresh_markups()
        self._load_props()
        self._update_status_widgets()
        if self.cv:
            self.tabs.setTabText(self.tabs.currentIndex(), self._title())

    def _title(self):
        d = self.doc
        return ("*" if d.modified else "") + (os.path.basename(d.path) if d and d.path else "Untitled")

    def refresh_all(self):
        self.refresh_markups()
        self.refresh_thumbs()
        self.refresh_toc()
        self.refresh_layers()
        self.refresh_sheets()
        self._load_props()
        self._update_status_widgets()
        d = self.doc
        self.act_arch.setChecked(bool(d) and d.unit_style == "feet-inches")
        self.setWindowTitle(f"OpenRevu — {d.path}" if d and d.path else "OpenRevu")

    def refresh_markups(self):
        self._rows = []
        t = self.mk_table
        t.setSortingEnabled(False)
        t.setRowCount(0)
        self.meas_table.setRowCount(0)
        d = self.doc
        cols = d.columns if d else []
        t.setColumnCount(7 + len(cols))
        t.setHorizontalHeaderLabels(["Page", "Subject", "Type", "Author", "Status", "Comment", "Value"] + [c["name"] for c in cols])
        if d:
            q, st = self.mk_filter.text().lower(), self.mk_status.currentIndex()
            for m in d.markups():
                status = m.status
                if st and STATUSES[st - 1] != status:
                    continue
                r = m.measurement()
                val = d.fmt(*r) if r else ""
                cv = m.custom()
                row = [str(m.page_no + 1), m.subject, m.kind, m.author, status, m.comment, val] \
                    + [("%g" % cv[c["name"]]) if isinstance(cv.get(c["name"]), float) else str(cv.get(c["name"], "")) for c in cols]
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
            for k, ((subj, kind, unit), (n, tot)) in enumerate(sorted(d.takeoff_by_subject().items())):
                self.meas_table.insertRow(k)
                for c, v in enumerate((subj, str(n), d.fmt(kind, tot, unit).split(" ")[0], {"area": f"{unit}²", "volume": f"{unit}³"}.get(kind, unit))):
                    self.meas_table.setItem(k, c, W.QTableWidgetItem(v))
        t.setSortingEnabled(True)
        self._rebuild_custom_fields()
        if d:
            lines = [f"{k.title()}: {d.fmt(k, v, u)}" for (k, u), v in sorted(d.takeoff().items())]
            self.totals.setText("Totals — " + ("; ".join(lines) if lines else "no measurements yet"))
        else:
            self.totals.setText("")

    def _table_select(self):
        rows = self.mk_table.selectionModel().selectedRows()
        if rows and self.cv:
            idx = self.mk_table.item(rows[0].row(), 0).data(QtCore.Qt.UserRole)
            m = self._rows[idx]
            self.cv.select(m)
            self.cv.goto_page(m.page_no, max(0, m.rect.y0 - 80))

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
                pix = d.thumbnail(i, 100)
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

    def refresh_layers(self):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        d = self.doc
        layers = d.layers() if d else []
        if d and not layers:
            it = W.QListWidgetItem("This PDF has no layers.")
            it.setFlags(QtCore.Qt.NoItemFlags)
            self.layer_list.addItem(it)
        for l in layers:
            it = W.QListWidgetItem(l["text"] or f"Layer {l['number']}")
            it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if l["on"] else QtCore.Qt.Unchecked)
            it.setData(QtCore.Qt.UserRole, l["number"])
            self.layer_list.addItem(it)
        self.layer_list.blockSignals(False)

    def refresh_sheets(self):
        """Fill the Sheets table. This reads text from every page, so it runs only while the panel is visible."""
        if self.panel_name(self.left_tabs, self.left_tabs.currentIndex()) != "Sheets" or self.left_dock.isHidden():
            return
        t = self.sheets_table
        keep = set(self.sheets_selected_pages())
        t.blockSignals(True)
        t.setRowCount(0)
        d = self.doc
        for r in (d.sheet_table() if d else []):
            i = t.rowCount()
            t.insertRow(i)
            info = d.sheet_info(r["page"] - 1, detect=False)
            vals = [str(r["page"]), r["number"], r["title"], r["discipline"], r["revision"]]
            for c, v in enumerate(vals):
                it = W.QTableWidgetItem(v)
                if c in (0, 4):
                    it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
                if c == 4 and info["revisions"]:
                    it.setToolTip("\n".join(f"{x['rev']}  {x['date']}  {x['description']}" for x in info["revisions"]))
                t.setItem(i, c, it)
            if r["page"] - 1 in keep:  # selectRow would drop the earlier rows, so add each row to the selection
                t.selectionModel().select(t.model().index(i, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
        t.blockSignals(False)

    def sheets_selected_pages(self) -> list[int]:
        return sorted({int(self.sheets_table.item(i.row(), 0).text()) - 1 for i in self.sheets_table.selectedIndexes()})

    def _sheet_selected(self):
        pages = self.sheets_selected_pages()
        if pages and self.cv:
            self.cv.goto_page(pages[0])

    def _sheet_edited(self, item):
        if not self.cv or item.column() not in (1, 2, 3):
            return
        pno = int(self.sheets_table.item(item.row(), 0).text()) - 1
        field = ("number", "title", "discipline")[item.column() - 1]
        self._run(self.doc.set_sheet, pno, **{field: item.text()})
        self.refresh_sheets()              # also puts the old value back when the edit was rejected (a duplicate number)

    def export_sheet_csv(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getSaveFileName(self, "Export sheet list", "sheets.csv", "CSV (*.csv)")
            if p:
                self._run(self.doc.export_sheet_index_csv, p)

    def _layer_toggled(self, item):
        if self.cv and item.data(QtCore.Qt.UserRole) is not None:
            on = item.checkState() == QtCore.Qt.Checked
            self._run(self.doc.set_layer, item.data(QtCore.Qt.UserRole), on, structural=True)

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

    # ---------- properties panel ----------
    def _load_props(self):
        m = self._selected()
        panel = (self.p_subject, self.p_comment, self.p_status, self.p_reply)
        for w in panel + (self.w_width, self.w_opacity):
            w.blockSignals(True)
        self._busy = True
        for w in panel:
            w.setEnabled(m is not None)
        self.p_replies.clear()
        if m is not None:
            try:
                self.p_info.setText(f"{m.kind} on page {m.page_no + 1}" + (f"\n{self.doc.fmt(*m.measurement())}" if m.measurement() else ""))
                self.p_subject.setText(m.subject)
                self.p_comment.setPlainText(m.comment)
                self.p_status.setCurrentIndex(STATUSES.index(m.status) if m.status in STATUSES else 0)
                colors = m.annot.colors
                if colors.get("stroke"):
                    self._paint_swatch(self.btn_stroke, tuple(colors["stroke"]))
                self._paint_swatch(self.btn_fill, tuple(colors["fill"]) if colors.get("fill") else None)
                self.w_width.setValue(m.annot.border.get("width", 1) or 1)
                self.w_opacity.setValue(m.annot.opacity if m.annot.opacity >= 0 else 1)
                for a, t in m.replies():
                    self.p_replies.addItem(f"{a}: {t}")
                cv = m.custom()
                for name, (col, ed) in self.p_custom_editors.items():
                    val = cv.get(name, "")
                    val = "%g" % val if isinstance(val, float) else str(val)
                    ed.blockSignals(True)
                    (ed.setCurrentText if isinstance(ed, W.QComboBox) else ed.setText)(val)
                    ed.blockSignals(False)
                    ed.setEnabled(True)
            except Exception:
                pass
        else:
            for _col, ed in self.p_custom_editors.values():
                ed.setEnabled(False)
            self.p_info.setText("Select a markup to see its properties.")
            self.p_subject.clear()
            self.p_comment.clear()
            self._paint_swatch(self.btn_stroke, self.color)
            self._paint_swatch(self.btn_fill, self.fill_color)
            self.w_width.setValue(self.cv.width if self.cv else 1.5)
            self.w_opacity.setValue(self.cv.opacity if self.cv else 1.0)
        self._busy = False
        for w in panel + (self.w_width, self.w_opacity):
            w.blockSignals(False)

    def _prop(self, fn, only_if_changed=False):
        m = self._selected()
        if m is None:
            return
        if only_if_changed and self.p_comment.toPlainText() == m.comment:
            return
        try:
            fn(m)
        except ERRORS as e:
            self.statusBar().showMessage(str(e))
            return self._load_props()          # put the stored value back into the field that was refused
        self.cv.invalidate([m.page_no])
        self._after_change()

    def _rebuild_custom_fields(self):
        """One editor per custom column in the Properties panel. Rebuilt only when the columns change."""
        cols = self.doc.columns if self.doc else []
        sig = repr(cols)
        if sig == self._cols_sig:
            return
        self._cols_sig = sig
        while self.p_custom_form.rowCount():
            self.p_custom_form.removeRow(0)
        self.p_custom_editors = {}
        for col in cols:
            if col["type"] == "choice":
                ed = W.QComboBox()
                ed.addItems([""] + col["choices"])
                ed.activated.connect(lambda _i, n=col["name"], e=ed: self._prop(lambda m: m.set_custom(n, e.currentText())))
            else:
                ed = W.QLineEdit(placeholderText="number" if col["type"] == "number" else "")
                ed.editingFinished.connect(lambda n=col["name"], e=ed: self._prop(lambda m: m.set_custom(n, e.text())))
            ed.setEnabled(False)
            self.p_custom_form.addRow(col["name"], ed)
            self.p_custom_editors[col["name"]] = (col, ed)

    def _header_menu(self, pos):
        if not self._need_doc():
            return
        menu = W.QMenu(self)
        menu.addAction(tool_icon("add"), "Add column…").triggered.connect(self.add_column_dialog)
        if self.doc.columns:
            rm = menu.addMenu(tool_icon("delete"), "Remove column")
            for c in self.doc.columns:
                rm.addAction(c["name"]).triggered.connect(lambda _=False, n=c["name"]: self.remove_column(n))
        menu.exec_(self.mk_table.horizontalHeader().mapToGlobal(pos))

    def add_column_dialog(self):
        if not self._need_doc():
            return
        v = self._form_dialog("Add a column", [("Name", ""), ("Type (text, number, choice)", "text"), ("Choices (separate with commas)", "")])
        if v and self._run(self.doc.add_column, v["Name"], v["Type (text, number, choice)"].strip().lower(),
                           v["Choices (separate with commas)"].split(",")) is not None:
            self.refresh_markups()

    def remove_column(self, name):
        if self._need_doc() and self._run(self.doc.remove_column, name) is not None:
            self.refresh_markups()

    def export_summary_pdf(self):
        if not self._need_doc():
            return
        mode, ok = W.QInputDialog.getItem(self, "Markup Summary", "Include a picture of each markup?", ["Yes", "No"], 0, False)
        if not ok:
            return
        p, _ = W.QFileDialog.getSaveFileName(self, "Save the Markup Summary", "markup-summary.pdf", "PDF (*.pdf)")
        if p:
            n = self._run(self.doc.export_markup_summary, p, mode == "Yes")
            if n is not None:
                self.statusBar().showMessage(f"Markup Summary saved: {n} markup(s).")

    def _add_reply(self):
        m = self._selected()
        if m is not None and self.p_reply.text().strip():
            self.doc.add_reply(m, self.p_reply.text().strip())
            self.p_reply.clear()
            self.cv.invalidate([m.page_no])
            self._after_change()

    # ---------- context menus ----------
    def build_context_menu(self, markup, pno=None, pt=None) -> W.QMenu:
        """Menu for a markup (or for empty page space when markup is None)."""
        menu = W.QMenu(self)
        if markup is None:
            a = menu.addAction("Paste here")
            a.setEnabled(self.clipboard is not None)
            a.triggered.connect(lambda: self.paste_markup(pno, pt))
            return menu
        menu.addAction(tool_icon("properties"), "Properties").triggered.connect(lambda: self.show_panel("Properties"))
        menu.addSeparator()
        menu.addAction(tool_icon("copy"), "Copy").triggered.connect(self.copy_markup)
        menu.addAction(tool_icon("duplicate"), "Duplicate").triggered.connect(lambda: (self.copy_markup(), self.paste_markup()))
        menu.addAction(tool_icon("delete"), "Delete").triggered.connect(self.delete_selected)
        menu.addSeparator()
        sm = menu.addMenu("Set status")
        for s in STATUSES:
            a = sm.addAction(s or "(none)")
            a.setCheckable(True)
            a.setChecked(markup.status == s)
            a.triggered.connect(lambda _=False, st=s: self._prop(lambda m: m.set_status(st)))
        menu.addAction(tool_icon("reply"), "Reply…").triggered.connect(self._reply_dialog)
        menu.addSeparator()
        menu.addAction(tool_icon("toolchest"), "Add to Tool Chest…").triggered.connect(self.chest_add)
        return menu

    def _canvas_menu(self, markup, gpos, pno, pt):
        self.build_context_menu(markup, pno, pt).exec_(gpos)

    def _table_menu(self, pos):
        rows = self.mk_table.selectionModel().selectedRows()
        row = self.mk_table.rowAt(pos.y())
        if row < 0 or not self.cv:
            return
        self.mk_table.selectRow(row)
        m = self._selected()
        if m is not None:
            self.build_context_menu(m).exec_(self.mk_table.viewport().mapToGlobal(pos))

    def _reply_dialog(self):
        m = self._selected()
        if m is None:
            return
        t, ok = W.QInputDialog.getText(self, "Reply", "Reply:")
        if ok and t.strip():
            self.doc.add_reply(m, t.strip())
            self.cv.invalidate([m.page_no])
            self._after_change()

    # ---------- edit ----------
    def undo(self):
        if self._need_doc():
            if self.doc.undo():
                self.cv.reload(); self._after_change(); self.refresh_thumbs(); self.refresh_toc(); self.refresh_layers()
            else:
                self.statusBar().showMessage("Nothing to undo.")

    def redo(self):
        if self._need_doc():
            if self.doc.redo():
                self.cv.reload(); self._after_change(); self.refresh_thumbs(); self.refresh_toc(); self.refresh_layers()
            else:
                self.statusBar().showMessage("Nothing to redo.")

    def copy_markup(self):
        m = self._selected()
        if m is None:
            return
        if not m.can_copy:
            return self.statusBar().showMessage(f"A {m.subject or m.kind} markup cannot be copied. Place a new one instead.")
        try:
            self.clipboard = m.to_dict()
        except ERRORS as e:
            self.statusBar().showMessage(str(e))

    def paste_markup(self, pno=None, pt=None):
        """Paste the copied markup: at a clicked point if given, otherwise offset slightly on the current page."""
        if not (self._need_doc() and self.clipboard):
            return
        page = self.cv.current_page() if pno is None else pno
        off = (12, 12) if pt is None else (pt[0] - self.clipboard["rect"][0], pt[1] - self.clipboard["rect"][1])
        self._run(self.doc.add_from_dict, page, self.clipboard, off, msg="Pasted")

    def delete_selected(self):
        m = self._selected()
        if m is not None:
            pno = m.page_no
            self.cv.select(None)  # clear first: the panels must not touch a deleted markup
            self._run(self.doc.delete, m)
            self.cv.invalidate([pno])

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
        self.chest_tree.clear()
        sets = dict(TOOL_GROUPS)
        for title, names in sets.items():
            top = W.QTreeWidgetItem([title])
            top.setFlags(QtCore.Qt.ItemIsEnabled)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.chest_tree.addTopLevelItem(top)
            for n in names:
                it = W.QTreeWidgetItem([LABELS.get(n, n)])
                it.setIcon(0, tool_icon(n))
                it.setData(0, QtCore.Qt.UserRole, ("tool", n))
                top.addChild(it)
            top.setExpanded(title in ("Shapes", "Measure"))
        stamps = W.QTreeWidgetItem(["Stamps"])
        stamps.setFlags(QtCore.Qt.ItemIsEnabled)
        sf = stamps.font(0); sf.setBold(True); stamps.setFont(0, sf)
        self.chest_tree.addTopLevelItem(stamps)
        for s in STAMPS:
            it = W.QTreeWidgetItem([s])
            it.setIcon(0, tool_icon("Stamp"))
            it.setData(0, QtCore.Qt.UserRole, ("stamp", s))
            stamps.addChild(it)
        mine = W.QTreeWidgetItem(["My Tools"])
        mine.setFlags(QtCore.Qt.ItemIsEnabled)
        mf = mine.font(0); mf.setBold(True); mine.setFont(0, mf)
        self.chest_tree.addTopLevelItem(mine)
        for name in sorted(self.chest.items):
            it = W.QTreeWidgetItem([name])
            it.setData(0, QtCore.Qt.UserRole, ("saved", name))
            mine.addChild(it)
        mine.setExpanded(True)
        self.chest_my = mine

    def _chest_clicked(self, item, _col=0):
        data = item.data(0, QtCore.Qt.UserRole)
        if not data:
            item.setExpanded(not item.isExpanded())
            return
        kind, value = data
        if kind == "tool":
            self.set_tool(value)
        elif kind == "stamp":
            self.w_stamp.setCurrentText(value)
            self.set_tool("Stamp")
        elif kind == "saved":
            self._arm_chest(value)

    def chest_add(self):
        m = self._selected()
        if m is None:
            return self.statusBar().showMessage("Select a markup first.")
        if not m.can_copy:
            return self.statusBar().showMessage(f"A {m.subject or m.kind} markup cannot be saved as a tool. Shapes, lines, pens and text boxes can.")
        n, ok = W.QInputDialog.getText(self, "Tool Chest", "Name:", text=m.subject)
        if ok and n:
            self.chest.add(n, m)
            self._refresh_chest()
            self.show_panel("Tool Chest")

    def chest_remove(self):
        it = self.chest_tree.currentItem()
        data = it.data(0, QtCore.Qt.UserRole) if it else None
        if data and data[0] == "saved":
            self.chest.remove(data[1])
            self._refresh_chest()
        else:
            self.statusBar().showMessage("Select one of your own tools under My Tools.")

    def _arm_chest(self, name):
        if not self.cv:
            return

        def place(pno, pt):
            self.cv.tool_chest_item = None
            self._run(self.chest.place, self.doc, name, pno, pt)

        self.cv.tool_chest_item = place
        self.statusBar().showMessage(f"Click on the page to place '{name}'.")

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


def install_excepthook(parent_getter=lambda: None):
    """Show unexpected errors in a dialog. Without this, PyQt5 aborts the whole program on an error in an event handler."""
    import traceback

    busy = {"on": False}

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        sys.stderr.write(text)
        if busy["on"] or W.QApplication.instance() is None:
            return
        busy["on"] = True
        try:
            box = W.QMessageBox(parent_getter())
            box.setIcon(W.QMessageBox.Critical)
            box.setWindowTitle("OpenRevu")
            box.setText("OpenRevu hit an unexpected problem.")
            box.setInformativeText("You can keep working. Press Ctrl+S to save, or use Undo if something looks wrong.")
            box.setDetailedText(text)
            box.exec_()
        finally:
            busy["on"] = False

    sys.excepthook = hook
    return hook


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = W.QApplication(argv)
    app.setStyle("Fusion")
    win = Main(argv[1] if len(argv) > 1 else None)
    install_excepthook(lambda: win)
    for extra in argv[2:]:
        win.open(extra)
    win.show()
    return app.exec_()
