import csv

import fitz
import pytest

from openrevu import compare as cmp
from openrevu.compare import compare_documents, diff_pages, diff_text, summarize, write_csv
from openrevu.core import Document
from tests.test_sheets import make_set

OLD = [("A-101", "Ground floor", "wall layout rev one"), ("A-102", "First floor", "stairs rev one"),
       ("S-201", "Roof", "beams rev one")]


@pytest.fixture
def old(tmp_path):
    return make_set(tmp_path / "old.pdf", OLD)


def by_number(results):
    return {r.number: r for r in results}


def test_identical_sets_are_unchanged(old, tmp_path):
    res = compare_documents(old, old, str(tmp_path / "o.pdf"))
    assert [r.status for r in res] == ["unchanged"] * 3 and all(r.changed_fraction == 0 for r in res)
    assert summarize(res) == "0 changed, 3 unchanged, 0 added, 0 removed"
    assert fitz.open(str(tmp_path / "o.pdf")).page_count == 3


def test_sheets_are_matched_by_number_not_position(old, tmp_path):
    new = make_set(tmp_path / "new.pdf", [("S-201", "Roof", "beams rev TWO"), ("A-103", "Second floor", "new sheet"),
                                          ("A-101", "Ground floor", "wall layout rev one")])
    res = by_number(compare_documents(old, new, str(tmp_path / "o.pdf")))
    assert res["A-101"].status == "unchanged" and (res["A-101"].old_page, res["A-101"].new_page) == (0, 2)
    assert res["S-201"].status == "changed" and (res["S-201"].old_page, res["S-201"].new_page) == (2, 0)
    assert res["A-103"].status == "added" and res["A-103"].old_page is None
    assert res["A-102"].status == "removed" and res["A-102"].new_page is None
    assert summarize(list(res.values())) == "1 changed, 1 unchanged, 1 added, 1 removed"
    # by page position the same files look completely different
    pos = compare_documents(old, new, match="page")
    assert [r.status for r in pos] == ["changed", "changed", "changed"]


def test_text_changes_are_reported_in_words(old, tmp_path):
    new = make_set(tmp_path / "new.pdf", [("A-101", "Ground floor", "wall layout rev TWO moved"), OLD[1], OLD[2]])
    r = by_number(compare_documents(old, new))["A-101"]
    assert r.status == "changed" and r.words_added == 2 and r.words_removed == 1
    assert any("one" in n and "TWO moved" in n for n in r.text_changes)
    assert r.changed_fraction > 0 and r.area_fraction > 0
    assert diff_text(None, None) == (0, 0, [])


def test_drawing_only_change_without_text_is_found(old, tmp_path):
    def extra(p, number):
        if number == "A-102":
            p.draw_rect(fitz.Rect(100, 300, 300, 400), width=3)
    new = make_set(tmp_path / "new.pdf", OLD, extra=extra)
    res = by_number(compare_documents(old, new))
    assert res["A-102"].status == "changed" and res["A-102"].words_added == 0 and res["A-102"].changed_fraction > 0.2
    assert res["A-101"].status == "unchanged"


def test_tolerance_ignores_a_one_pixel_shift(tmp_path):
    def build(shift):
        d = fitz.open(); p = d.new_page()
        p.draw_rect(fitz.Rect(100 + shift, 100, 300 + shift, 300), width=1)
        return d
    a, b = build(0), build(1)             # at 72 dpi one point is one pixel
    strict = diff_pages(a[0], b[0], dpi=72, tolerance=0)
    tolerant = diff_pages(a[0], b[0], dpi=72, tolerance=1)
    assert strict[3] > 100 and tolerant[3] == 0


def test_numpy_and_pure_python_paths_agree(monkeypatch):
    pytest.importorskip("numpy")
    d = fitz.open(); p = d.new_page(width=120, height=100); p.draw_rect(fitz.Rect(10, 10, 60, 50), width=2); p.insert_text((20, 80), "hi")
    e = fitz.open(); q = e.new_page(width=120, height=100); q.draw_rect(fitz.Rect(15, 10, 65, 50), width=2); q.insert_text((20, 80), "ho")
    fast = diff_pages(d[0], e[0], dpi=72, tolerance=0)
    monkeypatch.setattr(cmp, "np", None)
    slow = diff_pages(d[0], e[0], dpi=72, tolerance=0)
    assert fast == slow


def test_different_page_sizes_are_flagged_and_still_compared(old, tmp_path):
    new = make_set(tmp_path / "big.pdf", [("A-101", "Ground floor", "wall layout rev one")], size=(1224, 792))
    r = by_number(compare_documents(old, new))["A-101"]
    assert r.size_changed and r.status in ("changed", "unchanged")


def test_csv_report_and_overlay_pdf(old, tmp_path):
    new = make_set(tmp_path / "new.pdf", [("A-101", "Ground floor", "wall layout rev TWO"), OLD[1]])
    out = str(tmp_path / "ov.pdf")
    res = compare_documents(old, new, out)
    assert fitz.open(out).page_count == 3
    assert "A-101" in fitz.open(out)[0].get_text() and "changed" in fitz.open(out)[0].get_text()
    px = fitz.open(out)[0].get_pixmap(dpi=50)       # red and blue ink are present on the changed sheet
    colours = {px.pixel(x, y) for x in range(px.width) for y in range(px.height)}
    assert any(c[0] > 180 and c[1] < 100 for c in colours) and any(c[2] > 180 and c[0] < 100 for c in colours)
    path = tmp_path / "r.csv"; write_csv(res, str(path))
    rows = list(csv.reader(open(path, encoding="utf-8")))
    assert rows[0][:3] == ["sheet", "status", "old page"] and {r[1] for r in rows[1:]} == {"changed", "unchanged", "removed"}


def test_encrypted_inputs_and_bad_arguments(old, tmp_path):
    enc = str(tmp_path / "enc.pdf"); Document(old).save_encrypted(enc, "pw")
    with pytest.raises(PermissionError):
        compare_documents(enc, old)
    res = compare_documents(enc, old, old_password="pw")
    assert summarize(res) == "0 changed, 3 unchanged, 0 added, 0 removed"
    with pytest.raises(ValueError, match="match must be"):
        compare_documents(old, old, match="foo")


def test_legacy_function_and_cli(old, tmp_path):
    from openrevu.cli import main
    from openrevu.pages import compare_pdfs
    new = make_set(tmp_path / "new.pdf", [OLD[0], ("A-102", "First floor", "stairs rev TWO"), OLD[2]])
    assert compare_pdfs(old, new, str(tmp_path / "l.pdf"), dpi=50)[0] == 0
    assert compare_pdfs(old, new, str(tmp_path / "l.pdf"), dpi=50)[1] > 0
    csvp = str(tmp_path / "c.csv")
    assert main(["compare", old, new, str(tmp_path / "c.pdf"), "--csv", csvp]) == 0
    assert "A-102" in open(csvp, encoding="utf-8").read()
    assert main(["compare", old, str(tmp_path / "missing.pdf"), str(tmp_path / "c2.pdf")]) == 1
    enc = str(tmp_path / "enc.pdf"); Document(old).save_encrypted(enc, "pw")
    assert main(["compare", enc, new, str(tmp_path / "c3.pdf")]) == 1
    assert main(["compare", enc, new, str(tmp_path / "c3.pdf"), "--in-password", "pw", "--match", "page"]) == 0
