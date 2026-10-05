import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fitz
import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtCore, QtTest, QtWidgets

from openrevu.gui import Main


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(app, tmp_path):
    d = fitz.open(); d.new_page(); d.new_page()
    p = tmp_path / "a.pdf"; d.save(p)
    w = Main(str(p)); w.show()
    return w


def drag(w, a, b):
    vp = w.canvas.viewport()
    QtTest.QTest.mousePress(vp, QtCore.Qt.LeftButton, pos=QtCore.QPoint(*a))
    QtTest.QTest.mouseMove(vp, QtCore.QPoint(*b))
    QtTest.QTest.mouseRelease(vp, QtCore.Qt.LeftButton, pos=QtCore.QPoint(*b))


def test_draw_rectangle_and_delete(win):
    win.set_tool("Rectangle")
    drag(win, (50, 50), (200, 150))
    assert len(win.canvas.doc.markups()) == 1 and win.list.count() == 1
    win.list.setCurrentRow(0); win.delete_selected()
    assert win.list.count() == 0


def test_page_navigation(win):
    win.go(1); assert win.canvas.page == 1
    win.go(5); assert win.canvas.page == 1


def test_length_polyline_uses_all_clicked_points(win):
    from openrevu.core import Scale
    win.canvas.doc.set_scale(Scale("in", 1 / 72))  # 1 pt == 1/72 in -> at zoom 1.5, 108 px == 1 in
    win.set_tool("Length")
    vp = win.canvas.viewport()
    for p in [(30, 30), (138, 30)]:
        QtTest.QTest.mouseClick(vp, QtCore.Qt.LeftButton, pos=QtCore.QPoint(*p))
    QtTest.QTest.mouseDClick(vp, QtCore.Qt.LeftButton, pos=QtCore.QPoint(138, 30))
    t = win.canvas.doc.takeoff()
    assert t[("length", "in")] == pytest.approx(1.0, rel=0.05)
