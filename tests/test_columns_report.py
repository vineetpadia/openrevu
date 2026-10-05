import csv

import fitz
import pytest

from openrevu.core import Document, Scale
from tests.test_sheets import SET_V1, make_set


@pytest.fixture
def pdf(tmp_path):
    return make_set(tmp_path / "t.pdf", SET_V1)


# ---------- custom columns ----------
def test_define_columns_and_set_values_with_validation(pdf):
    d = Document(pdf)
    d.add_column("Cost code"); d.add_column("Cost", "number"); d.add_column("Trade", "choice", ["Electrical", " Plumbing ", ""])
    assert [(c["name"], c["type"], c["choices"]) for c in d.columns] == [
        ("Cost code", "text", []), ("Cost", "number", []), ("Trade", "choice", ["Electrical", "Plumbing"])]
    d.add_rect(0, fitz.Rect(10, 10, 60, 60)); m = d.markups()[0]
    m.set_custom("Cost code", "03-300"); m.set_custom("Cost", "1250.5"); m.set_custom("Trade", "Plumbing")
    assert d.markups()[0].custom() == {"Cost code": "03-300", "Cost": 1250.5, "Trade": "Plumbing"}
    for name, bad, exc in (("Cost", "abc", ValueError), ("Trade", "Roofing", ValueError), ("Nope", "x", KeyError)):
        with pytest.raises(exc):
            d.markups()[0].set_custom(name, bad)
    assert d.markups()[0].custom()["Cost"] == 1250.5                 # a rejected value changes nothing
    d.markups()[0].set_custom("Cost", "")                            # empty clears
    assert "Cost" not in d.markups()[0].custom()


def test_column_definition_errors(pdf):
    d = Document(pdf)
    d.add_column("Cost", "number")
    for args in (("",), ("  ",), ("Cost",), ("Subject",), ("X", "date"), ("Y", "choice"), ("Z", "choice", [" ", ""])):
        with pytest.raises(ValueError):
            d.add_column(*args)
    assert [c["name"] for c in d.columns] == ["Cost"] and len(d._undo) == 1      # failures left no undo steps
    with pytest.raises(KeyError):
        d.remove_column("Nope")


def test_columns_persist_undo_and_removal_drops_values(pdf, tmp_path):
    d = Document(pdf)
    d.add_column("Cost", "number"); d.add_rect(0, fitz.Rect(10, 10, 60, 60)); d.markups()[0].set_custom("Cost", 5)
    out = str(tmp_path / "c.pdf"); d.save(out)
    r = Document(out)
    assert r.columns == [{"name": "Cost", "type": "number", "choices": []}] and r.markups()[0].custom() == {"Cost": 5.0}
    d.remove_column("Cost")
    assert d.columns == [] and d.markups()[0].custom() == {}
    d.undo()
    assert [c["name"] for c in d.columns] == ["Cost"] and d.markups()[0].custom() == {"Cost": 5.0}
    d.undo(); assert d.markups()[0].custom() == {}                   # undo of the value itself


def test_column_totals_and_csv(pdf, tmp_path):
    d = Document(pdf)
    d.add_column("Cost", "number"); d.add_column("Note")
    d.add_count(0, (5, 5), group="Door"); d.add_count(0, (9, 9), group="Door"); d.add_count(1, (3, 3), group="Window")
    for m, v in zip(d.markups(), (100, 250.5, 40)):
        m.set_custom("Cost", v)
    d.markups()[0].set_custom("Note", "gold, \"plated\"")
    assert d.column_totals("Cost") == {"Door": 350.5, "Window": 40.0}
    with pytest.raises(ValueError):
        d.column_totals("Note")
    p = tmp_path / "m.csv"; d.export_csv(str(p))
    rows = list(csv.reader(open(p, encoding="utf-8")))
    assert rows[0][-2:] == ["Cost", "Note"] and rows[1][-2:] == ["100.0", "gold, \"plated\""] and rows[3][-1] == ""


def test_columns_survive_copy_of_document_pages(pdf, tmp_path):
    d = Document(pdf)
    d.add_column("Cost", "number"); d.add_rect(0, fitz.Rect(10, 10, 60, 60)); d.markups()[0].set_custom("Cost", 7)
    n = d.markups()[0]
    d.add_from_dict(1, n.to_dict())                                  # a pasted markup has no custom values
    assert [m.custom() for m in d.markups() if m.page_no == 1] == [{}]


# ---------- markup summary report ----------
def build(pdf):
    d = Document(pdf)
    d.set_scale(Scale("m", 0.1))
    d.add_column("Cost", "number")
    d.add_rect(0, fitz.Rect(100, 200, 200, 260)); a = d.markups()[-1]
    a.set_subject("Wall crack"); a.set_comment("Repair before painting. Contact the site engineer, ref É-42."); a.set_status("Accepted")
    a.set_custom("Cost", 120.5); d.add_reply(a, "Booked for Monday")
    d.add_length(0, [(100, 300), (250, 300)])
    d.add_cloud(1, fitz.Rect(100, 200, 220, 280)); d.markups()[-1].set_comment("Revised stairs")
    d.add_note(2, (300, 300), "Check beam size"); d.markups()[-1].set_status("Rejected")
    return d


def test_report_contents_and_counts(pdf, tmp_path):
    d = build(pdf)
    out = str(tmp_path / "r.pdf")
    n = d.export_markup_summary(out)
    assert n == 4
    r = fitz.open(out); text = "".join(p.get_text() for p in r)
    for s in ("Markup Summary", "4 markup(s)", "Accepted: 1", "Rejected: 1", "(no status): 2", "Wall crack", "Repair before painting",
              "É-42", "Reply from", "Booked for Monday", "Status: Accepted", "Cost: 120.5", "Measurement: 15.00 m",
              "Page 1  (A-101)", "Page 2  (A-102)", "Revised stairs", "Check beam size"):
        assert s in text, s
    assert len(r[0].get_images()) >= 4                               # a picture of each markup


def test_report_without_images_and_with_status_filter(pdf, tmp_path):
    d = build(pdf)
    out = str(tmp_path / "r2.pdf")
    assert d.export_markup_summary(out, include_images=False, statuses=["Accepted"]) == 1
    r = fitz.open(out)
    assert r[0].get_images() == [] and "Wall crack" in r[0].get_text() and "Check beam size" not in r[0].get_text()
    r.close()                                                         # Windows cannot overwrite a file that is still open
    assert d.export_markup_summary(out, statuses=[""]) == 2           # only markups with no status


def test_report_paginates_and_handles_empty_and_long_text(pdf, tmp_path):
    d = Document(pdf)
    empty = str(tmp_path / "e.pdf")
    assert d.export_markup_summary(empty) == 0 and "There are no markups" in fitz.open(empty)[0].get_text()
    for i in range(60):
        d.add_rect(0, fitz.Rect(20 + i, 20 + i, 60 + i, 60 + i)); d.markups()[-1].set_comment(f"item {i} " + "word " * 80 + "x" * 150)
    big = str(tmp_path / "big.pdf")
    assert d.export_markup_summary(big) == 60
    r = fitz.open(big)
    assert r.page_count > 5 and "item 59" in "".join(p.get_text() for p in r) and "Page 2" in r[1].get_text()
    for p in r:                                                      # nothing is drawn past the page margins
        for b in p.get_text("blocks"):
            assert b[0] >= 0 and b[2] <= 612 + 1 and b[3] <= 792


def test_report_on_rotated_page_and_cli(pdf, tmp_path):
    from openrevu.cli import main
    d = Document(pdf)
    d.doc[0].set_rotation(90); d.add_rect(0, fitz.Rect(100, 150, 200, 250)); d.markups()[0].set_comment("on a rotated sheet")
    path = str(tmp_path / "rot.pdf"); d.save(path)
    assert main(["report", path, str(tmp_path / "o.pdf")]) == 0
    t = fitz.open(str(tmp_path / "o.pdf"))[0].get_text()
    assert "on a rotated sheet" in t
    assert main(["report", path, str(tmp_path / "o2.pdf"), "--no-images", "--status", "Accepted"]) == 0
    assert "There are no markups" in fitz.open(str(tmp_path / "o2.pdf"))[0].get_text()


# ---------- third-review regressions: nothing is cut off in the report ----------
def test_a_very_long_comment_continues_on_the_next_page(pdf, tmp_path):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(10, 10, 60, 60))
    d.markups()[0].set_comment("\n".join(f"line {i} of a very long comment" for i in range(200)))
    d.add_rect(1, fitz.Rect(10, 10, 60, 60)); d.markups()[-1].set_comment("a short one")
    out = str(tmp_path / "long.pdf")
    assert d.export_markup_summary(out) == 2
    r = fitz.open(out)
    text = "".join(p.get_text() for p in r)
    for i in (0, 57, 58, 120, 199):
        assert f"line {i} of a very long comment" in text, i              # the first, the middle, and the last line are all there
    assert r.page_count >= 4 and "a short one" in text
    for p in r:
        for b in p.get_text("blocks"):
            assert b[3] <= 792 - 40 and b[2] <= 612                       # nothing is below the bottom margin or right of the page


def test_bold_headings_are_wrapped_with_the_bold_font(pdf, tmp_path):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(10, 10, 60, 60)); m = d.markups()[0]
    m.set_subject("W" * 12 + " " + "MWMWMWMW " * 8)
    d.doc.xref_set_key(m.xref, "T", "(" + "Reviewer " * 6 + ")")
    out = str(tmp_path / "b.pdf"); d.export_markup_summary(out, include_images=False)
    for b in fitz.open(out)[0].get_text("blocks"):
        assert b[2] <= 612 - 40 + 1                                       # the wide bold text stays inside the right margin


def test_wrap_uses_the_right_font_for_each_style():
    from openrevu.report import FONTS, _wrap
    text = "Wide wide wide wide wide wide wide wide"
    for style in ("", "b", "i"):
        for line in _wrap(text, 100, 9, style):
            assert FONTS[style].text_length(line, 9) <= 100.5
    assert len(_wrap(text, 100, 9, "b")) >= len(_wrap(text, 100, 9, ""))  # bold is wider, so it needs at least as many lines
    assert _wrap("", 100) == [""] and _wrap("x" * 500, 50)[0] != ""
