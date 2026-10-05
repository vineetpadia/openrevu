import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fitz
import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtCore, QtGui, QtTest, QtWidgets

from openrevu.gui import Main, parse_pages


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(app, tmp_path):
    d = fitz.open()
    for i in range(3):
        p = d.new_page(); p.insert_text((72, 100), f"Sheet {i + 1} hello")
    path = tmp_path / "a.pdf"; d.save(path)
    w = Main(str(path)); w.show(); w.resize(1300, 900)
    app.processEvents()
    return w


def vp_pos(w, pno, x, y):
    """viewport position for a PDF point on page pno"""
    cv = w.cv
    sp = cv._to_scene(pno, (x, y))
    return cv.mapFromScene(sp)


def drag(w, pno, a, b):
    vp = w.cv.viewport()
    pa, pb = vp_pos(w, pno, *a), vp_pos(w, pno, *b)
    QtTest.QTest.mousePress(vp, QtCore.Qt.LeftButton, pos=pa)
    QtTest.QTest.mouseMove(vp, pb)
    QtTest.QTest.mouseRelease(vp, QtCore.Qt.LeftButton, pos=pb)


def click(w, pno, x, y, dbl=False):
    vp = w.cv.viewport()
    (QtTest.QTest.mouseDClick if dbl else QtTest.QTest.mouseClick)(vp, QtCore.Qt.LeftButton, pos=vp_pos(w, pno, x, y))


def test_parse_pages():
    assert parse_pages("1-3,5", 5) == [0, 1, 2, 4]
    assert parse_pages("2,2", 3) == [1]
    for bad in ("0", "4", "3-1", "", "a"):
        with pytest.raises(ValueError):
            parse_pages(bad, 3)


def test_draw_select_move_resize_delete_undo(win, app):
    w = win
    w.set_tool("Rectangle")
    drag(w, 0, (100, 150), (200, 220))
    assert len(w.doc.markups()) == 1 and w.mk_table.rowCount() == 1
    w.set_tool("Select")
    click(w, 0, 150, 180)
    m = w.cv.selected
    assert m is not None and m.kind == "Square"
    x0 = m.rect.x0
    drag(w, 0, (150, 180), (180, 200))
    assert w.cv.selected.rect.x0 == pytest.approx(x0 + 30, abs=1.5)
    r = w.cv.selected.rect
    # resize via bottom-right handle
    drag(w, 0, (r.x1 + 3, r.y1 + 3), (r.x1 + 43, r.y1 + 23))
    assert w.cv.selected.rect.width == pytest.approx(r.width + 40, abs=4)
    # properties edit
    w.p_subject.setText("Wall"); w.p_subject.editingFinished.emit()
    assert w.doc.markups()[0].subject == "Wall"
    w.p_status.setCurrentIndex(1); w.p_status.activated.emit(1)
    assert w.doc.markups()[0].status == "Accepted"
    w.p_reply.setText("noted"); w.p_reply.returnPressed.emit()
    assert w.p_replies.count() == 1
    # copy / paste
    w.copy_markup(); w.paste_markup()
    assert len(w.doc.markups()) == 2
    w.undo(); assert len(w.doc.markups()) == 1
    w.redo(); assert len(w.doc.markups()) == 2
    # delete via key
    w.cv.select(w.doc.markups()[0])
    QtTest.QTest.keyClick(w.cv, QtCore.Qt.Key_Delete)
    assert len(w.doc.markups()) == 1


def test_measure_on_second_page_with_continuous_layout(win):
    w = win
    w.doc.set_scale(Scale := __import__("openrevu.core", fromlist=["Scale"]).Scale("in", 1 / 72), page=1)
    w.set_tool("Length")
    w.cv.goto_page(1)
    click(w, 1, 100, 100); click(w, 1, 172, 100); click(w, 1, 172, 100, dbl=True)
    m = [x for x in w.doc.markups() if x.measurement()]
    assert len(m) == 1 and m[0].page_no == 1
    assert m[0].measurement()[1] == pytest.approx(1.0, rel=0.1)
    assert "Length" in w.totals.text()


def test_page_ops_via_ui(win, monkeypatch):
    w = win
    n = w.doc.page_count
    monkeypatch.setattr(QtWidgets.QInputDialog, "getInt", staticmethod(lambda *a, **k: (2, True)))
    w.insert_blank()
    assert w.doc.page_count == n + 1 and len(w.cv._rects) == n + 1
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("2", True)))
    w.delete_pages()
    assert w.doc.page_count == n
    w.undo()
    assert w.doc.page_count == n + 1 and len(w.cv._rects) == n + 1


def test_search_bookmarks_navigation(win):
    w = win
    w.search_edit.setText("hello"); w.do_search()
    assert len(w._hits) == 3 and w.search_list.count() == 3
    w.next_hit()
    w.cv.goto_page(2)
    assert w.cv.current_page() == 2 and w.page_spin.value() == 3
    w.doc.add_bookmark("Third", 2); w.refresh_toc()
    assert w.toc.topLevelItemCount() == 1


def test_tabs_open_close_dedupe(win, tmp_path):
    w = win
    path = w.doc.path
    w.open(path)
    assert w.tabs.count() == 1
    d = fitz.open(); d.new_page(); p2 = str(tmp_path / "b.pdf"); d.save(p2)
    w.open(p2)
    assert w.tabs.count() == 2
    w.close_tab(1)
    assert w.tabs.count() == 1


def test_calibrate_and_text_tools_dialog_paths(win, monkeypatch):
    w = win
    monkeypatch.setattr(QtWidgets.QInputDialog, "getDouble", staticmethod(lambda *a, **k: (10.0, True)))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda p, t, l, items, *a, **k: (items[0] if items[0] in ("This page", "All pages") else "m", True)))
    w.set_tool("Calibrate")
    drag(w, 0, (100, 300), (200, 300))
    assert w.doc.scale_for(0).unit == "m" and w.doc.scale_for(0).unit_per_pt == pytest.approx(0.1, rel=0.05)
    assert 1 not in w.doc.page_scales
    monkeypatch.setattr(QtWidgets.QInputDialog, "getMultiLineText", staticmethod(lambda *a, **k: ("hello box", True)))
    w.set_tool("Text")
    drag(w, 0, (100, 400), (250, 440))
    assert any(m.kind == "FreeText" for m in w.doc.markups())
    w.set_tool("Stamp")
    click(w, 0, 300, 500)
    assert any(m.subject == "Stamp" for m in w.doc.markups())


def test_unsaved_close_prompts(win, monkeypatch):
    w = win
    w.set_tool("Ellipse")
    drag(w, 0, (100, 100), (150, 150))
    assert w.doc.modified
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Cancel))
    w.close_tab(0)
    assert w.tabs.count() == 1
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Discard))
    w.close_tab(0)
    assert w.tabs.count() == 0


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_draw_select_move_on_rotated_page(win, rot):
    w = win
    w.doc.doc[0].set_rotation(rot)
    w.cv.reload()
    w.set_tool("Rectangle")
    drag(w, 0, (100, 150), (200, 220))
    r0 = fitz.Rect(w.doc.markups()[0].rect)
    assert tuple(r0) == pytest.approx((99, 149, 201, 221), abs=3)  # lands where it was dragged
    w.set_tool("Select")
    click(w, 0, 150, 185)
    assert w.cv.selected is not None
    drag(w, 0, (150, 185), (180, 205))
    assert w.cv.selected.rect.x0 == pytest.approx(r0.x0 + 30, abs=2)
    assert w.cv.selected.rect.y0 == pytest.approx(r0.y0 + 20, abs=2)


def test_fill_tool_measures_clicked_room(app, tmp_path):
    pytest.importorskip("scipy")
    d = fitz.open(); p = d.new_page(width=600, height=800)
    p.draw_rect(fitz.Rect(100, 100, 400, 300), width=3)
    path = tmp_path / "room.pdf"; d.save(path)
    w = Main(str(path)); w.show(); app.processEvents()
    w.doc.set_scale(__import__("openrevu.core", fromlist=["Scale"]).Scale("m", 0.01))
    w.set_tool("Fill")
    click(w, 0, 250, 200)
    ms = [m for m in w.doc.markups() if m.measurement()]
    assert len(ms) == 1 and ms[0].subject == "Fill Area"
    assert ms[0].measurement()[1] * 10000 == pytest.approx(294 * 194, rel=0.03)
    w.set_tool("Fill")
    click(w, 0, 500, 600)  # outside: error reported, nothing added
    assert len([m for m in w.doc.markups() if m.measurement()]) == 1
    assert "enclosed" in w.statusBar().currentMessage()


def test_viewport_tool_and_sheet_actions(win, monkeypatch):
    w = win
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("0.5:1:m", True)))
    w.set_tool("Viewport")
    drag(w, 0, (200, 200), (400, 400))
    assert len(w.doc.viewports) == 1 and w.doc.viewports[0][2].unit == "m"
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("bogus", True)))
    w.set_tool("Viewport")  # single-use tools return to Select after each placement
    drag(w, 0, (100, 500), (150, 550))
    assert len(w.doc.viewports) == 1 and "Invalid" in w.statusBar().currentMessage()
    w.auto_bookmarks()
    assert "No sheet numbers" in w.statusBar().currentMessage() or "created" in w.statusBar().currentMessage()


# ---------- Revu-style workflow ----------
def test_single_use_tools_return_to_select_and_keep_tool_option(win):
    w = win
    w.set_tool("Rectangle")
    drag(w, 0, (100, 150), (200, 220))
    assert w.cv.tool == "Select" and w.tool_actions["Select"].isChecked()
    w.act_keep.setChecked(True)
    w.set_tool("Ellipse")
    drag(w, 0, (250, 150), (350, 220)); drag(w, 0, (250, 300), (350, 380))
    assert w.cv.tool == "Ellipse" and len([m for m in w.doc.markups() if m.kind == "Circle"]) == 2
    w.act_keep.setChecked(False)
    w.set_tool("Count")
    click(w, 0, 100, 400); click(w, 0, 150, 400)
    assert w.cv.tool == "Count" and w.doc.takeoff()[("count", "ea")] == 2


def test_toolbar_groups_remember_last_tool_and_show_options(win):
    w = win
    assert w.group_buttons["Shapes"].defaultAction() is w.tool_actions["Rectangle"]
    w.set_tool("Cloud")
    assert w.group_buttons["Shapes"].defaultAction() is w.tool_actions["Cloud"] and w.tool_actions["Cloud"].isChecked()
    assert w.cv.viewport().cursor().shape() == QtCore.Qt.CrossCursor
    w.set_tool("Pan"); assert w.cv.viewport().cursor().shape() == QtCore.Qt.OpenHandCursor
    w.set_tool("Select"); assert w.cv.viewport().cursor().shape() == QtCore.Qt.ArrowCursor
    assert not w.w_depth_visible() if hasattr(w, "w_depth_visible") else True
    w.set_tool("Volume"); assert all(a.isVisible() for a in w.opt_widgets["Volume"])
    assert not any(a.isVisible() for a in w.opt_widgets["Stamp"])
    w.set_tool("Stamp"); assert all(a.isVisible() for a in w.opt_widgets["Stamp"])


def test_properties_toolbar_edits_selection_or_defaults(win):
    w = win
    w._set_stroke((0.0, 0.0, 1.0))                 # nothing selected: sets the default for new markups
    assert w.color == (0.0, 0.0, 1.0) and w.cv.color == (0.0, 0.0, 1.0)
    w.set_tool("Rectangle"); drag(w, 0, (100, 150), (200, 220))
    m = w.doc.markups()[0]
    assert tuple(m.annot.colors["stroke"]) == (0.0, 0.0, 1.0)
    w.cv.select(m)                                  # selected: edits that markup only
    w._set_stroke((0.0, 1.0, 0.0)); w.w_width.setValue(5.0); w.w_opacity.setValue(0.5)
    m2 = w.doc.markups()[0]
    assert tuple(m2.annot.colors["stroke"]) == (0.0, 1.0, 0.0) and m2.annot.border["width"] == 5.0
    assert w.color == (0.0, 0.0, 1.0)               # default unchanged
    w.cv.select(None)
    assert w.w_width.value() == w.cv.width          # toolbar shows the defaults again
    w.set_tool("Rectangle"); w._set_fill((1.0, 1.0, 0.0)); drag(w, 0, (300, 150), (380, 220))
    filled = [x for x in w.doc.markups() if x.annot.colors.get("fill")]
    assert len(filled) == 1 and tuple(filled[0].annot.colors["fill"]) == (1.0, 1.0, 0.0)


def test_context_menu_actions(win, monkeypatch):
    w = win
    w.set_tool("Rectangle"); drag(w, 0, (100, 150), (200, 220))
    m = w.doc.markups()[0]; w.cv.select(m)
    menu = w.build_context_menu(m)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert labels == ["Properties", "Copy", "Duplicate", "Delete", "Set status", "Reply…", "Add to Tool Chest…"]
    status_menu = [a for a in menu.actions() if a.text() == "Set status"][0].menu()
    [a for a in status_menu.actions() if a.text() == "Rejected"][0].trigger()
    assert w.doc.markups()[0].status == "Rejected"
    [a for a in menu.actions() if a.text() == "Duplicate"][0].trigger()
    assert len(w.doc.markups()) == 2
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("looks good", True)))
    [a for a in menu.actions() if a.text() == "Reply…"][0].trigger()
    assert [t for _, t in w.cv.selected.replies()] == ["looks good"]
    [a for a in menu.actions() if a.text() == "Delete"][0].trigger()
    assert len(w.doc.markups()) == 1
    empty = w.build_context_menu(None, 0, (50.0, 60.0))
    paste = empty.actions()[0]
    assert paste.text() == "Paste here" and paste.isEnabled()      # clipboard was filled by Duplicate
    paste.trigger()
    assert len(w.doc.markups()) == 2


def test_tool_chest_panel_activates_tools_stamps_and_saved(win, monkeypatch, tmp_path):
    w = win
    w.chest.path = str(tmp_path / "tc.json"); w.chest.items = {}
    top = {w.chest_tree.topLevelItem(i).text(0): w.chest_tree.topLevelItem(i) for i in range(w.chest_tree.topLevelItemCount())}
    assert set(top) == {"Shapes", "Text & Review", "Stamp & Sign", "Insert", "Measure", "Stamps", "My Tools"}
    cloud = [top["Shapes"].child(i) for i in range(top["Shapes"].childCount()) if top["Shapes"].child(i).text(0) == "Cloud"][0]
    w._chest_clicked(cloud); assert w.cv.tool == "Cloud"
    rejected = [top["Stamps"].child(i) for i in range(top["Stamps"].childCount()) if top["Stamps"].child(i).text(0) == "REJECTED"][0]
    w._chest_clicked(rejected); assert w.cv.tool == "Stamp" and w.cv.stamp_text == "REJECTED"
    click(w, 0, 300, 300)
    assert any(m.comment == "REJECTED" for m in w.doc.markups())
    # a stamp cannot be saved as a tool: the user gets a message, not a failing tool
    w.cv.select([m for m in w.doc.markups() if m.subject == "Stamp"][0])
    w.chest_add()
    assert "cannot be saved as a tool" in w.statusBar().currentMessage() and w.chest.items == {}
    w.copy_markup()
    assert "cannot be copied" in w.statusBar().currentMessage() and w.clipboard is None
    # a shape can
    w.set_tool("Rectangle"); drag(w, 0, (100, 150), (200, 220))
    w.cv.select([m for m in w.doc.markups() if m.kind == "Square"][0])
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("My box", True)))
    w.chest_add()
    assert "My box" in w.chest.items
    mine = w.chest_my
    assert [mine.child(i).text(0) for i in range(mine.childCount())] == ["My box"]
    w._chest_clicked(mine.child(0))
    n = len(w.doc.markups()); click(w, 0, 150, 500)
    assert len(w.doc.markups()) == n + 1


def test_start_page_recent_files_and_panels(app, tmp_path):
    d = fitz.open(); d.new_page(); p = str(tmp_path / "r.pdf"); d.save(p)
    w = Main(); w.show(); app.processEvents()
    assert w.stack.currentWidget() is w.start and w.left_dock.isHidden() and w.d_markups.isHidden()
    assert w.start.recent.item(0).text() == "No recent files"
    w.open(p); app.processEvents()
    assert w.stack.currentWidget() is w.tabs and not w.left_dock.isHidden() and not w.d_markups.isHidden()
    assert w.prefs.recent() == [p]
    w.close_tab(0); app.processEvents()
    assert w.stack.currentWidget() is w.start and w.left_dock.isHidden()
    assert "r.pdf" in w.start.recent.item(0).text()
    opened = []
    w.start.file_requested.connect(opened.append)
    w.start.recent.itemActivated.emit(w.start.recent.item(0))
    assert opened == [p]


def test_status_bar_zoom_box_layers_and_measurements_panel(win, tmp_path):
    w = win
    app = QtWidgets.QApplication.instance()
    w.cv.set_zoom(96 / 72 * 2)                      # 200%
    assert w.lbl_zoom.text() == "200%" and w.zoom_box.currentText() == "200%"
    assert w.lbl_page.text() == "Page 1 of 3" and w.lbl_scale.text().startswith("Scale: 1 in = ")
    w.zoom_box.setCurrentText("50%"); w._zoom_from_box()
    assert w.cv.zoom == pytest.approx(0.5 * 96 / 72)
    w.zoom_box.setCurrentText("junk"); w._zoom_from_box()
    assert w.zoom_box.currentText() == "50%"
    w.cv.fit_page()
    assert w.cv.zoom < 96 / 72 * 2
    w.cv.goto_page(2); assert w.lbl_page.text() == "Page 3 of 3" and w.page_spin.value() == 3
    # measurements panel
    w.doc.add_count(0, (5, 5), group="Door"); w.doc.add_count(0, (9, 9), group="Door"); w._after_change()
    rows = [[w.meas_table.item(r, c).text() for c in range(4)] for r in range(w.meas_table.rowCount())]
    assert rows == [["Door", "2", "2", "ea"]]
    assert "Page 3" in w.scale_lbl.text() and "Viewports on this page: 0" in w.scale_lbl.text()
    w.show_panel("Measurements"); assert w.panel_name(w.right_tabs, w.right_tabs.currentIndex()) == "Measurements"
    w.show_panel("Search"); assert w.panel_name(w.left_tabs, w.left_tabs.currentIndex()) == "Search"


def test_layers_panel_toggles_layers(app, tmp_path):
    d = fitz.open(); p = d.new_page(); d.add_ocg("Dims", on=True); d.add_ocg("Notes", on=False)
    path = str(tmp_path / "l.pdf"); d.save(path)
    w = Main(path); w.show(); app.processEvents()
    items = [w.layer_list.item(i) for i in range(w.layer_list.count())]
    assert [(i.text(), i.checkState() == QtCore.Qt.Checked) for i in items] == [("Dims", True), ("Notes", False)]
    items[1].setCheckState(QtCore.Qt.Checked)
    assert [l["on"] for l in w.doc.layers()] == [True, True]
    plain = fitz.open(); plain.new_page(); pp = str(tmp_path / "p.pdf"); plain.save(pp)
    w.open(pp)
    assert w.layer_list.item(0).text() == "This PDF has no layers."


def test_pan_tool_drags_the_view(win):
    w = win
    w.set_tool("Pan")
    w.cv.set_zoom(3.0); w.cv.verticalScrollBar().setValue(0)
    vp = w.cv.viewport()
    QtTest.QTest.mousePress(vp, QtCore.Qt.LeftButton, pos=QtCore.QPoint(300, 400))
    # a real drag holds the button down; QTest.mouseMove would send a move without it, which Qt ignores
    QtWidgets.QApplication.sendEvent(vp, QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(300, 300),
                                     QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
    QtTest.QTest.mouseRelease(vp, QtCore.Qt.LeftButton, pos=QtCore.QPoint(300, 300))
    assert w.cv.verticalScrollBar().value() == 100
    assert w.doc.markups() == []
    assert w.cv.viewport().cursor().shape() == QtCore.Qt.OpenHandCursor


def test_feet_inches_menu_toggle_redraws_labels_and_shows_in_the_list(win):
    from openrevu.core import Scale
    w = win
    w.doc.set_scale(Scale("ft", 1 / 12))
    w.doc.add_length(0, [(10, 100), (160.5, 100)]); w._after_change()
    assert "12.54 ft" in w.mk_table.item(0, 6).text()
    w.act_arch.setChecked(True); w.toggle_arch_units()
    assert w.doc.unit_style == "feet-inches" and "12' 6 1/2\"" in w.mk_table.item(0, 6).text()
    assert "12' 6 1/2\"" in w.totals.text()
    w.undo()
    assert w.doc.unit_style == "decimal" and not w.act_arch.isChecked()      # the menu follows the document


# ---------- custom columns and the Markup Summary ----------
def test_custom_columns_in_the_list_and_the_properties_panel(win, monkeypatch):
    w = win
    w.doc.add_rect(0, fitz.Rect(100, 200, 200, 260)); w._after_change()
    answers = iter([{"Name": "Cost", "Type (text, number, choice)": "number", "Choices (separate with commas)": ""},
                    {"Name": "Trade", "Type (text, number, choice)": "choice", "Choices (separate with commas)": "Electrical, Plumbing"}])
    monkeypatch.setattr(w, "_form_dialog", lambda *a, **k: next(answers))
    w.add_column_dialog(); w.add_column_dialog()
    headers = [w.mk_table.horizontalHeaderItem(i).text() for i in range(w.mk_table.columnCount())]
    assert headers[-2:] == ["Cost", "Trade"] and set(w.p_custom_editors) == {"Cost", "Trade"}
    assert not any(ed.isEnabled() for _c, ed in w.p_custom_editors.values())      # nothing selected yet
    w.cv.select(w.doc.markups()[0])
    cost, trade = w.p_custom_editors["Cost"][1], w.p_custom_editors["Trade"][1]
    assert cost.isEnabled() and trade.isEnabled() and [trade.itemText(i) for i in range(trade.count())] == ["", "Electrical", "Plumbing"]
    cost.setText("1250.5"); cost.editingFinished.emit()
    trade.setCurrentText("Plumbing"); trade.activated.emit(2)
    assert w.doc.markups()[0].custom() == {"Cost": 1250.5, "Trade": "Plumbing"}
    row = [w.mk_table.item(0, c).text() for c in range(w.mk_table.columnCount())]
    assert row[-2:] == ["1250.5", "Plumbing"]
    w.mk_filter.setText("plumb"); assert w.mk_table.rowCount() == 1
    w.mk_filter.setText("nothing"); assert w.mk_table.rowCount() == 0
    w.mk_filter.setText("")
    cost.setText("abc"); cost.editingFinished.emit()                               # a bad number is refused with a message
    assert "needs a number" in w.statusBar().currentMessage() and w.doc.markups()[0].custom()["Cost"] == 1250.5
    assert cost.text() == "1250.5"                                                 # and the field shows the stored value again
    w.remove_column("Cost")
    assert [w.mk_table.horizontalHeaderItem(i).text() for i in range(w.mk_table.columnCount())][-1] == "Trade" and "Cost" not in w.p_custom_editors
    w.undo()
    assert "Cost" in w.p_custom_editors and w.doc.markups()[0].custom()["Cost"] == 1250.5


def test_header_menu_lists_add_and_remove(win):
    w = win
    w.doc.add_column("Cost", "number"); w._after_change()
    called = {}
    import openrevu.gui as G
    orig = G.W.QMenu.exec_
    G.W.QMenu.exec_ = lambda self, *a: called.update(actions=[x.text() for x in self.actions()]) or None
    try:
        w._header_menu(QtCore.QPoint(5, 5))
    finally:
        G.W.QMenu.exec_ = orig
    assert called["actions"] == ["Add column…", "Remove column"]


def test_export_markup_summary_from_the_menu(win, tmp_path, monkeypatch):
    w = win
    w.doc.add_rect(0, fitz.Rect(100, 200, 200, 260)); w.doc.markups()[0].set_comment("see this"); w._after_change()
    out = str(tmp_path / "sum.pdf")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("No", True)))
    w.export_summary_pdf()
    assert "Markup Summary saved: 1 markup" in w.statusBar().currentMessage()
    assert "see this" in fitz.open(out)[0].get_text() and fitz.open(out)[0].get_images() == []


# ---------- Image, Snapshot, Link tools ----------
def test_snapshot_copies_the_region_to_the_clipboard(win):
    w = win
    QtWidgets.QApplication.clipboard().clear()
    w.set_tool("Snapshot")
    drag(w, 0, (100, 100), (300, 250))
    img = QtWidgets.QApplication.clipboard().image()
    assert not img.isNull() and abs(img.width() - 2 * 200) <= 4 and abs(img.height() - 2 * 150) <= 4     # 2x of a 200 x 150 pt region
    assert "Snapshot copied" in w.statusBar().currentMessage() and w.doc.markups() == []                 # not stored in the PDF


def test_image_tool_places_a_picture_by_drag_or_click(win, tmp_path, monkeypatch):
    w = win
    png = str(tmp_path / "logo.png")
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 50), False); pm.set_rect(pm.irect, (200, 30, 30)); pm.save(png)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (png, "")))
    w.set_tool("Image"); drag(w, 0, (100, 100), (300, 200))
    m = [x for x in w.doc.markups() if x.subject == "Image"]
    assert len(m) == 1 and m[0].kind == "Stamp" and m[0].rect.width <= 201
    w.set_tool("Image"); click(w, 0, 100, 400)                                  # a click keeps the picture's proportions
    r = [x for x in w.doc.markups() if x.subject == "Image"][-1].rect
    assert r.width == pytest.approx(100, abs=3) and r.height == pytest.approx(50, abs=3) and r.y0 == pytest.approx(400, abs=3)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    n = len(w.doc.markups()); w.set_tool("Image"); drag(w, 0, (100, 500), (200, 560))
    assert len(w.doc.markups()) == n                                            # cancelled: nothing placed
    bad = tmp_path / "bad.png"; bad.write_bytes(b"not an image")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    w.set_tool("Image"); drag(w, 0, (100, 500), (200, 560))
    assert "Cannot use that image" in w.statusBar().currentMessage() and len(w.doc.markups()) == n


def test_link_tool_makes_page_and_web_links_and_refuses_bad_input(win, monkeypatch):
    w = win
    def link(text, region=((100, 100), (200, 140))):
        monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: (text, True)))
        w.set_tool("Link"); drag(w, 0, *region)
    link("3"); link("www.example.org", ((100, 200), (200, 240))); link("https://a.b/c", ((100, 300), (200, 340)))
    links = w.doc.links(0)
    assert sorted((l["kind"], l.get("page", l.get("uri"))) for l in links) == sorted(
        [(fitz.LINK_GOTO, 2), (fitz.LINK_URI, "https://www.example.org"), (fitz.LINK_URI, "https://a.b/c")])
    for bad, msg in (("99", "There is no page 99"), ("0", "There is no page 0"), ("javascript:alert(1)", "needs a page number"), ("hello", "needs a page number")):
        n = len(w.doc.links(0)); link(bad, ((100, 400), (200, 440)))
        assert msg in w.statusBar().currentMessage() and len(w.doc.links(0)) == n
    n = len(w.doc.links(0))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    w.set_tool("Link"); drag(w, 0, (100, 500), (200, 540))
    assert len(w.doc.links(0)) == n                                              # cancelled


def test_unexpected_errors_show_a_dialog_instead_of_killing_the_app(app, monkeypatch):
    import sys
    from openrevu.gui import install_excepthook
    seen = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec_", lambda self: seen.update(text=self.text(), details=self.detailedText()) or 0)
    old = sys.excepthook
    hook = install_excepthook()
    try:
        try:
            raise RuntimeError("boom from a handler")
        except RuntimeError:
            hook(*sys.exc_info())
    finally:
        sys.excepthook = old
    assert seen["text"] == "OpenRevu hit an unexpected problem." and "boom from a handler" in seen["details"]


def test_import_btx_from_the_menu(win, tmp_path, monkeypatch):
    from tests.test_btx import RECT, item, write
    w = win
    w.chest.path = str(tmp_path / "tc.json"); w.chest.items = {}
    p = write(tmp_path, [item("Rect", RECT), item("Stamp", "<</Subtype/Stamp>>")])
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (p, "")))
    shown = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec_", lambda self: shown.update(text=self.text(), details=self.detailedText()) or 0)
    w.import_btx()
    assert shown["text"] == "1 tool(s) imported, 1 skipped." and "Stamp type cannot be a tool" in shown["details"]
    mine = w.chest_my
    assert [mine.child(i).text(0) for i in range(mine.childCount())] == ["Rect"]
    bad = tmp_path / "bad.btx"; bad.write_text("not xml")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    w.import_btx()
    assert warned and "not a readable tool set" in warned[0]


# ---------- Copy Text tool and following links ----------
def test_copy_text_tool(win):
    w = win
    QtWidgets.QApplication.clipboard().clear()
    w.doc.doc[0].insert_text((100, 300), "Fire door FD30 rated", fontsize=12)
    w.cv.invalidate()
    w.set_tool("CopyText")
    drag(w, 0, (90, 285), (300, 310))
    assert QtWidgets.QApplication.clipboard().text() == "Fire door FD30 rated"
    assert "Copied 20 characters" in w.statusBar().currentMessage()
    w.set_tool("CopyText"); drag(w, 0, (400, 500), (500, 560))                    # a region without text
    assert "no text in that region" in w.statusBar().currentMessage() and QtWidgets.QApplication.clipboard().text() == "Fire door FD30 rated"
    assert w.doc.markups() == []


def test_clicking_links_follows_page_links_and_opens_only_safe_web_links(win, monkeypatch):
    w = win
    w.doc.add_link_goto(0, fitz.Rect(100, 100, 200, 130), 2)
    w.doc.add_link_uri(0, fitz.Rect(100, 200, 200, 230), "https://example.org/x")
    w.doc.add_link_uri(0, fitz.Rect(100, 300, 200, 330), "file:///etc/passwd")
    w.doc.add_link_uri(0, fitz.Rect(100, 400, 200, 430), "javascript:alert(1)")
    opened = []
    monkeypatch.setattr(QtGui.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString()) or True))
    w.cv.set_zoom(0.5); w.cv.goto_page(0)
    for y in (115, 215, 315, 415, 600):                                              # every click point is visible
        assert w.cv.viewport().rect().contains(vp_pos(w, 0, 150, y)), y
    for tool in ("Select", "Pan"):
        w.set_tool(tool)
        w.cv.goto_page(0); click(w, 0, 150, 115)
        assert w.cv.current_page() == 2 and "Went to page 3" in w.statusBar().currentMessage()
    w.cv.goto_page(0)
    click(w, 0, 150, 215); assert opened == ["https://example.org/x"]
    for y in (315, 415):
        click(w, 0, 150, y)
        assert "Did not open the link" in w.statusBar().currentMessage()
    assert opened == ["https://example.org/x"]                                      # nothing else was opened
    click(w, 0, 450, 600)                                                           # empty space: nothing happens
    assert opened == ["https://example.org/x"]
    # a drag is not a click: dragging over a link only pans
    w.set_tool("Pan"); w.cv.set_zoom(2.0); w.cv.goto_page(0)
    vp = w.cv.viewport(); p = vp_pos(w, 0, 150, 215)
    QtTest.QTest.mousePress(vp, QtCore.Qt.LeftButton, pos=p)
    QtWidgets.QApplication.sendEvent(vp, QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(p.x() + 30, p.y() + 30),
                                     QtCore.Qt.NoButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
    QtTest.QTest.mouseRelease(vp, QtCore.Qt.LeftButton, pos=QtCore.QPoint(p.x() + 30, p.y() + 30))
    assert opened == ["https://example.org/x"]
