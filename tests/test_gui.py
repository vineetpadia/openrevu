import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fitz
import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtCore, QtTest, QtWidgets

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
    click(w, 0, 500, 600)  # outside: error reported, nothing added
    assert len([m for m in w.doc.markups() if m.measurement()]) == 1
    assert "enclosed" in w.statusBar().currentMessage()
