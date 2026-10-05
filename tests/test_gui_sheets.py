import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fitz
import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtCore, QtWidgets

from openrevu import pdfa
from openrevu.gui import Main
from tests.test_sheets import SET_V1, make_set


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(app, tmp_path):
    w = Main(make_set(tmp_path / "set.pdf", SET_V1)); w.resize(1300, 900); w.show(); app.processEvents()
    w.show_panel("Sheets"); app.processEvents()
    return w


def cells(w, col):
    return [w.sheets_table.item(r, col).text() for r in range(w.sheets_table.rowCount())]


def test_sheets_panel_lists_detected_sheets_and_goes_to_page(win):
    w = win
    assert cells(w, 1) == ["A-101", "A-102", "S-201"] and cells(w, 2)[1] == "First floor plan"
    w.sheets_table.selectRow(2)
    assert w.cv.current_page() == 2
    w.detect_sheets()
    assert w.doc.sheet_record(0)["number"] == "A-101"
    assert "3 sheet number" in w.statusBar().currentMessage()
    w.detect_sheets()
    assert "No new sheet numbers" in w.statusBar().currentMessage()


def test_editing_a_cell_changes_the_document_and_a_duplicate_is_rejected(win, monkeypatch):
    w = win
    w.sheets_table.item(1, 3).setText("Architecture")
    assert w.doc.sheet_info(1)["discipline"] == "Architecture" and cells(w, 3)[1] == "Architecture"
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
    w.sheets_table.item(1, 1).setText("A-101")             # a number that another sheet already uses
    assert shown and "already used on page 1" in shown[0]
    assert cells(w, 1) == ["A-101", "A-102", "S-201"] and w.doc.sheet_info(1)["number"] == "A-102"


def test_add_revision_for_selected_sheets_and_bad_revision_is_reported(win, monkeypatch):
    w = win
    w.sheets_table.selectRow(0)
    monkeypatch.setattr(w, "_form_dialog", lambda *a, **k: {"Revision": "B", "Description": "Walls moved",
                                                            "Date (YYYY-MM-DD, blank = today)": "2026-03-01"})
    w.add_revision_dialog()
    assert w.doc.current_revision(0) == "B" and cells(w, 4)[0] == "B"
    assert "Walls moved" in w.sheets_table.item(0, 4).toolTip()
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
    w.add_revision_dialog()                                 # the same revision again
    assert shown and "already exists" in shown[0]
    w.sheets_table.clearSelection()
    w.add_revision_dialog()
    assert "Select a sheet" in w.statusBar().currentMessage()


def test_insert_index_page_from_the_panel(win):
    w = win
    w.insert_index_page()
    assert w.doc.page_count == 4 and "Sheet Index" in w.doc.page_text(0)
    assert "Sheet index inserted" in w.statusBar().currentMessage()
    assert len(w.cv._rects) == 4


def test_slip_sheet_from_the_panel(win, tmp_path, monkeypatch):
    w = win
    w.doc.add_rect(0, fitz.Rect(100, 200, 200, 260)); w._after_change()
    new = make_set(tmp_path / "new.pdf", [("A-101", "Ground floor plan", "REVISED text"), ("A-104", "Annex", "added")])
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (new, "")))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("By sheet number", True)))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("C", True)))
    infos = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a[2])))
    w.slip_sheet_dialog()
    assert "REVISED text" in w.doc.page_text(0) and len(w.doc.markups()) == 1 and w.doc.current_revision(0) == "C"
    assert w.doc.page_count == 4 and infos and "1 sheet(s) replaced, 1 added" in infos[0]
    assert len(w.cv._rects) == 4 and cells(w, 1) == ["A-101", "A-102", "S-201", "A-104"]
    w.undo()
    assert w.doc.page_count == 3 and "REVISED" not in w.doc.page_text(0)


def test_compare_flow_shows_results_and_opens_the_overlay(win, tmp_path, monkeypatch, app):
    w = win
    new = make_set(tmp_path / "new.pdf", [SET_V1[0], ("A-102", "First floor plan", "stairs CHANGED"), ("A-110", "New", "x")])
    out = str(tmp_path / "overlay.pdf")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (new, "")))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("By sheet number", True)))
    seen = {}
    monkeypatch.setattr(QtWidgets.QDialog, "exec_", lambda self: seen.update(table=[[self.results_table.item(r, c).text() for c in range(3)]
                                                                                   for r in range(self.results_table.rowCount())]) or 0)
    w.compare()
    rows = {r[0]: r for r in seen["table"]}
    assert rows["A-101"][1] == "unchanged" and rows["A-102"][1] == "changed" and rows["A-110"][1] == "added" and rows["S-201"][1] == "removed"
    assert rows["A-102"][2].endswith("%") and os.path.exists(out)
    # unsaved changes block the comparison with a message
    w.doc.add_rect(0, fitz.Rect(1, 1, 5, 5)); w._after_change()
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    w.compare()
    assert shown and "Save the document first" in shown[0]


@pytest.mark.skipif(not pdfa.available(), reason="Ghostscript not installed")
def test_export_pdfa_from_the_menu(win, tmp_path, monkeypatch):
    w = win
    out = str(tmp_path / "a.pdf")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("2b", True)))
    msgs = []
    for name in ("information", "warning"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(lambda *a, **k: msgs.append(a[2])))
    w.export_pdfa()
    assert os.path.exists(out) and msgs and "PDF/A-2b" in msgs[0]


def test_revision_on_several_selected_sheets_keeps_the_selection(win, monkeypatch):
    w = win
    sm = w.sheets_table.selectionModel()
    for r in (0, 2):
        sm.select(w.sheets_table.model().index(r, 0), QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
    assert w.sheets_selected_pages() == [0, 2]
    monkeypatch.setattr(w, "_form_dialog", lambda *a, **k: {"Revision": "R1", "Description": "", "Date (YYYY-MM-DD, blank = today)": ""})
    w.add_revision_dialog()
    assert [w.doc.current_revision(i) for i in range(3)] == ["R1", "", "R1"]
    assert w.sheets_selected_pages() == [0, 2]


def test_the_panel_is_widened_so_every_column_is_visible(app, tmp_path):
    w = Main(make_set(tmp_path / "set.pdf", SET_V1)); w.resize(1400, 900); w.show(); app.processEvents()
    w.show_panel("Sheets"); app.processEvents(); app.processEvents()
    assert w.left_dock.width() >= 380
    assert not w.sheets_table.horizontalScrollBar().isVisible()                    # every column fits, at this width
    assert w.sheets_table.columnWidth(2) >= 80                                       # the title keeps a usable width
    w.resizeDocks([w.left_dock], [230], QtCore.Qt.Horizontal); app.processEvents()
    assert not w.sheets_table.horizontalScrollBar().isVisible()                    # and when the panel is narrow
