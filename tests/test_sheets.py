import csv

import fitz
import pytest

from openrevu.core import Document, Scale


def make_set(path, sheets, size=(612, 792), extra=None):
    """sheets: list of (number, title, body). The number is the largest text; the title is the next largest."""
    d = fitz.open()
    for number, title, body in sheets:
        p = d.new_page(width=size[0], height=size[1])
        p.insert_text((400, 740), number, fontsize=28)
        p.insert_text((72, 90), title, fontsize=16)
        p.insert_text((72, 150), body, fontsize=10)
        if extra:
            extra(p, number)
    d.save(path)
    return str(path)


SET_V1 = [("A-101", "Ground floor plan", "Rev one wall layout"), ("A-102", "First floor plan", "Rev one stairs"),
          ("S-201", "Roof framing", "Rev one beams")]


@pytest.fixture
def v1(tmp_path):
    return make_set(tmp_path / "v1.pdf", SET_V1)


def test_detect_sheets_numbers_titles_and_table(v1):
    d = Document(v1)
    assert d.sheet_record(0) == {}                       # nothing stored yet
    assert d.sheet_info(1)["number"] == "A-102" and d.sheet_info(1)["title"] == "First floor plan"
    assert d.detect_sheets() == 3
    assert d.sheet_record(2) == {"number": "S-201", "title": "Roof framing"}
    t = d.sheet_table()
    assert [r["number"] for r in t] == ["A-101", "A-102", "S-201"] and t[0]["title"] == "Ground floor plan"
    assert d.detect_sheets() == 0                        # nothing new to store
    d.set_sheet(0, title="Ground floor (edited)", discipline="Architecture")
    assert d.detect_sheets() == 0 and d.sheet_info(0)["title"] == "Ground floor (edited)"
    d.detect_sheets(overwrite=True)
    assert d.sheet_info(0)["title"] == "Ground floor plan"


def test_set_sheet_rejects_duplicate_numbers_and_bad_page(v1):
    d = Document(v1)
    with pytest.raises(ValueError, match="already used on page 1"):
        d.set_sheet(1, number="A-101")
    with pytest.raises(IndexError):
        d.set_sheet(9, number="X-1")
    d.set_sheet(1, number="A-102R")
    assert d.sheet_info(1)["number"] == "A-102R"
    assert len(d._undo) == 1                              # the failed calls left no undo steps


def test_revisions_validation_order_and_removal(v1):
    d = Document(v1)
    d.add_revision(0, "A", "Issued for review", date="2026-01-05")
    d.add_revision(0, "B", "Walls moved")
    revs = d.sheet_info(0)["revisions"]
    assert [r["rev"] for r in revs] == ["A", "B"] and revs[0]["date"] == "2026-01-05" and revs[1]["description"] == "Walls moved"
    assert d.current_revision(0) == "B" and d.current_revision(1) == ""
    assert d.sheet_record(0)["number"] == "A-101"         # the detected number is stored with the first revision
    for bad in (dict(rev=""), dict(rev="A"), dict(rev="C", date="05/01/2026")):
        with pytest.raises(ValueError):
            d.add_revision(0, **bad)
    assert [r["rev"] for r in d.sheet_info(0)["revisions"]] == ["A", "B"]
    d.remove_revision(0, "A")
    assert d.current_revision(0) == "B"
    with pytest.raises(KeyError):
        d.remove_revision(0, "Z")
    d.undo(); assert [r["rev"] for r in d.sheet_info(0)["revisions"]] == ["A", "B"]


def test_records_follow_pages_and_survive_save_undo_merge_extract_split(v1, tmp_path):
    d = Document(v1)
    d.detect_sheets(); d.add_revision(1, "A", "first")
    d.move_page(1, 0)
    assert [d.sheet_info(i)["number"] for i in range(3)] == ["A-102", "A-101", "S-201"]
    assert d.sheet_info(0)["revisions"][0]["description"] == "first"
    d.delete_pages([2])
    out = str(tmp_path / "o.pdf"); d.save(out)
    r = Document(out)
    assert [r.sheet_info(i, detect=False)["number"] for i in range(2)] == ["A-102", "A-101"] and r.current_revision(0) == "A"
    d.undo(); assert d.page_count == 3
    # merge, extract and split keep the records
    other = Document(v1); other.detect_sheets(); other.add_revision(2, "Z"); other.save(str(tmp_path / "other.pdf"))
    m = Document(v1)
    m.insert_pdf(str(tmp_path / "other.pdf"), pages=[2])
    assert m.sheet_record(3)["number"] == "S-201" and m.current_revision(3) == "Z"
    m.insert_pdf(str(tmp_path / "other.pdf"), at=0)
    assert m.sheet_record(0)["number"] == "A-101" and m.sheet_record(5) == {} and m.sheet_record(6)["number"] == "S-201"
    ex = other.extract_pages([2, 0], str(tmp_path / "ex.pdf"))
    e = Document(ex)
    assert [e.sheet_record(i)["number"] for i in range(2)] == ["S-201", "A-101"] and e.current_revision(0) == "Z"
    parts = other.split(str(tmp_path / "sp"), every=1)
    assert [Document(p).sheet_record(0)["number"] for p in parts] == ["A-101", "A-102", "S-201"]


def test_unicode_titles_survive(tmp_path):
    p = make_set(tmp_path / "u.pdf", [("A-1", "Plan (étage) & «détails»", "x")])
    d = Document(p); d.detect_sheets(); d.set_sheet(0, discipline="Électricité (E)")
    d.save(str(tmp_path / "u2.pdf"))
    r = Document(str(tmp_path / "u2.pdf"))
    assert r.sheet_info(0)["title"].startswith("Plan (étage)") and r.sheet_info(0)["discipline"] == "Électricité (E)"


def test_sheet_index_csv_and_index_pages_with_links(v1, tmp_path):
    d = Document(v1)
    d.detect_sheets(); d.add_revision(1, "C", "stairs", date="2026-02-02")
    csvp = tmp_path / "idx.csv"; d.export_sheet_index_csv(str(csvp))
    rows = list(csv.reader(open(csvp, encoding="utf-8")))
    assert rows[0][:5] == ["page", "number", "title", "discipline", "revision"] and rows[2][1:5] == ["A-102", "First floor plan", "", "C"]
    n = d.insert_sheet_index(at=0)
    assert n == 1 and d.page_count == 4
    text = d.page_text(0)
    assert "Sheet Index" in text and "A-101" in text and "Roof framing" in text and "2026-02-02" in text
    assert sorted(l["page"] for l in d.links(0)) == [1, 2, 3]       # links point past the index page
    assert d.sheet_info(1)["number"] == "A-101"                      # sheets moved down by one
    many = Document(make_set(tmp_path / "big.pdf", [(f"A-{i:03d}", f"Sheet {i}", "") for i in range(1, 46)]))
    assert many.insert_sheet_index(at=0, rows_per_page=20) == 3 and many.page_count == 48


# ---------- slip sheet ----------
def annotated_old(tmp_path):
    def extra(p, number):
        pass
    path = make_set(tmp_path / "old.pdf", SET_V1)
    d = Document(path)
    d.detect_sheets(); d.add_revision(0, "A", "Issue")
    d.set_scale(Scale("ft", 0.25), page=0)
    d.add_rect(0, fitz.Rect(100, 200, 200, 260))
    d.add_length(0, [(100, 300), (200, 300)])
    note = d.add_note(0, (300, 300), "check wall")
    d.add_reply(d.markups()[-1], "done")
    d.add_rect(1, fitz.Rect(50, 50, 80, 80))
    d.add_link_goto(1, fitz.Rect(72, 140, 200, 160), 0)             # A-102 -> A-101
    d.add_link_goto(0, fitz.Rect(72, 160, 200, 180), 1)             # A-101 -> A-102
    d.set_toc([[1, "Plans", 1], [1, "Upper", 2], [1, "Roof", 3]])
    d.save(path)
    return path


V2 = [("S-201", "Roof framing", "Rev TWO beams"), ("A-101", "Ground floor plan", "Rev TWO wall layout"),
      ("A-103", "Second floor", "brand new")]


def test_slip_sheet_replaces_by_number_and_keeps_everything(tmp_path):
    old = annotated_old(tmp_path)
    new = make_set(tmp_path / "new.pdf", V2)
    d = Document(old)
    rep = d.slip_sheet(new, revision="B", description="Walls changed")
    assert sorted(n for n, _ in rep.replaced) == ["A-101", "S-201"] and rep.added == ["A-103"] and rep.not_in_new == ["A-102"]
    assert rep.markups_moved == 3 and rep.size_changed == []
    assert d.page_count == 4                                         # 3 old pages, 2 replaced in place, 1 added
    assert [d.sheet_info(i)["number"] for i in range(4)] == ["A-101", "A-102", "S-201", "A-103"]
    assert "TWO wall layout" in d.page_text(0) and "Rev one stairs" in d.page_text(1) and "TWO beams" in d.page_text(2)
    assert "Rev one wall" not in d.page_text(0)
    # markups on A-101 are on the new page, with measurement, reply and scale
    ms = [m for m in d.markups() if m.page_no == 0]
    assert sorted(m.kind for m in ms) == ["Line", "Square", "Text"]
    ln = [m for m in ms if m.kind == "Line"][0]
    assert ln.measurement()[1] == pytest.approx(25.0) and [t for _, t in [m for m in ms if m.kind == "Text"][0].replies()] == ["done"]
    assert d.scale_for(0).unit_per_pt == 0.25
    assert [m.kind for m in d.markups() if m.page_no == 1] == ["Square"]    # untouched sheet keeps its markups
    # sheet record and revision history
    assert d.current_revision(0) == "B" and [r["rev"] for r in d.sheet_info(0)["revisions"]] == ["A", "B"]
    assert d.sheet_record(3)["number"] == "A-103"
    # links and bookmarks still lead to the right pages
    assert [l["page"] for l in d.links(0)] == [1] and [l["page"] for l in d.links(1)] == [0]
    assert [(t[1], t[2]) for t in d.toc()] == [("Plans", 1), ("Upper", 2), ("Roof", 3)]
    # the saved file is sound
    out = str(tmp_path / "out.pdf"); d.save(out)
    r = Document(out)
    assert r.page_count == 4 and len(r.markups()) == 4
    for i in range(r.page_count):
        r.render(i, 0.5)
    assert [l["page"] for l in r.links(1)] == [0]


def test_slip_sheet_undo_restores_the_old_set(tmp_path):
    d = Document(annotated_old(tmp_path))
    d.slip_sheet(make_set(tmp_path / "new.pdf", V2), revision="B")
    assert d.undo()
    assert d.page_count == 3 and "Rev one wall" in d.page_text(0) and len(d.markups()) == 4
    assert d.current_revision(0) == "A"


def test_slip_sheet_without_markups_and_by_page(tmp_path):
    d = Document(annotated_old(tmp_path))
    rep = d.slip_sheet(make_set(tmp_path / "new.pdf", V2), keep_markups=False)
    assert rep.markups_moved == 0 and [m.kind for m in d.markups()] == ["Square"]    # only A-102's markup remains
    e = Document(annotated_old(tmp_path))
    rep = e.slip_sheet(make_set(tmp_path / "new2.pdf", V2), match="page")
    assert len(rep.replaced) == 3 and rep.added == [] and "TWO beams" in e.page_text(0)
    assert e.page_count == 3 and len(e.markups()) == 4                                # markups stay on the same positions


def test_slip_sheet_reports_size_change_and_bad_input(tmp_path):
    d = Document(annotated_old(tmp_path))
    new = make_set(tmp_path / "big.pdf", [("A-101", "Ground floor plan", "bigger")], size=(1224, 792))
    rep = d.slip_sheet(new)
    assert rep.size_changed == [("A-101", (612, 792), (1224, 792))] and "different page size" in rep.summary()
    with pytest.raises(ValueError, match="match must be"):
        d.slip_sheet(new, match="foo")
    nonum = fitz.open(); nonum.new_page().insert_text((72, 100), "no number here at all"); nonum.save(str(tmp_path / "nn.pdf"))
    with pytest.raises(ValueError, match="no sheet numbers"):
        d.slip_sheet(str(tmp_path / "nn.pdf"))
    d.slip_sheet(make_set(tmp_path / "again.pdf", [("A-101", "x", "first try")]), revision="B")
    n_undo, text = len(d._undo), d.page_text(0)
    with pytest.raises(ValueError, match="already exists on sheet A-101"):
        d.slip_sheet(make_set(tmp_path / "again2.pdf", [("A-101", "x", "second try")]), revision="B")
    assert len(d._undo) == n_undo and d.page_text(0) == text and d.page_count == 3       # nothing changed
    dup = Document(make_set(tmp_path / "dup.pdf", [("A-1", "a", ""), ("A-1", "b", "")]))
    with pytest.raises(ValueError, match="is on pages 1 and 2"):
        dup.slip_sheet(new)


def test_a_failure_halfway_restores_the_document(v1, tmp_path, monkeypatch):
    from openrevu.pages import PageOps
    d = Document(v1)
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    before = (d.page_count, d.page_text(0), len(d.markups()), len(d._undo))
    other = make_set(tmp_path / "o.pdf", [("B-1", "x", "y"), ("B-2", "x", "y")])
    monkeypatch.setattr(PageOps, "_remap_scales", lambda self, m: (_ for _ in ()).throw(RuntimeError("boom after inserting")))
    with pytest.raises(RuntimeError, match="boom"):
        d.insert_pdf(other)                       # pages are inserted, then the step after them fails
    monkeypatch.undo()
    assert (d.page_count, d.page_text(0), len(d.markups()), len(d._undo)) == before
    with pytest.raises(IndexError):
        d.insert_pdf(other, pages=[0, 99])        # checked before the first page is inserted
    assert d.page_count == 3
    d.add_rect(0, fitz.Rect(60, 60, 90, 90)); assert len(d.markups()) == 2     # the document is still fully usable


def test_title_does_not_repeat_the_sheet_number(tmp_path):
    d = fitz.open(); p = d.new_page()
    p.insert_text((400, 740), "A-101", fontsize=28)
    p.insert_text((72, 90), "A-101 - Ground floor plan", fontsize=16)
    p.insert_text((72, 130), "A-101: Ground floor plan", fontsize=12)
    q = d.new_page(); q.insert_text((400, 740), "S-2", fontsize=28); q.insert_text((72, 90), "S-2", fontsize=16)
    path = str(tmp_path / "t.pdf"); d.save(path)
    doc = Document(path)
    assert doc.sheet_info(0)["title"] == "Ground floor plan"
    assert doc.sheet_info(1)["title"] == ""                  # nothing but the number: no title


# ---------- third-review regressions: slip sheet ----------
def test_named_destinations_and_open_action_follow_the_new_page(tmp_path):
    path = make_set(tmp_path / "old.pdf", SET_V1)
    d = fitz.open(path)
    cat, p0, p2 = d.pdf_catalog(), d.page_xref(0), d.page_xref(2)
    d.xref_set_key(cat, "Dests", f"<</top [{p0} 0 R /XYZ 0 792 0]/roof [{p2} 0 R /Fit]>>")
    d.xref_set_key(cat, "OpenAction", f"[{p0} 0 R /Fit]")
    link = d.get_new_xref(); d.update_object(link, "<</Type/Annot/Subtype/Link/Rect[10 10 100 40]/Border[0 0 0]/A<</S/GoTo/D(roof)>>>>")
    d[1].set_rotation(0)
    d.xref_set_key(d.page_xref(1), "Annots", f"[{link} 0 R]")
    d.saveIncr()
    doc = Document(path)
    assert [l["page"] for l in doc.links(1)] == [2]                                  # the named link reaches the roof sheet
    doc.slip_sheet(make_set(tmp_path / "new.pdf", [("S-201", "Roof", "Rev TWO"), ("A-101", "Ground", "Rev TWO")]))
    assert "Rev TWO" in doc.page_text(2) and [l["page"] for l in doc.links(1)] == [2]     # still the (new) roof sheet
    cat = doc.doc.pdf_catalog()
    assert doc.doc.xref_get_key(cat, "OpenAction")[1].startswith(f"[{doc.doc.page_xref(0)} 0 R")
    dests = doc.doc.xref_get_key(cat, "Dests")[1]
    compact = dests.replace(" ", "")
    assert f"[{doc.doc.page_xref(0)}0R/XYZ" in compact and f"[{doc.doc.page_xref(2)}0R/Fit" in compact
    out = str(tmp_path / "o.pdf"); doc.save(out)
    r = Document(out)
    assert [l["page"] for l in r.links(1)] == [2]                                    # survives a save too
    for i in range(r.page_count):
        r.render(i, 0.4)


def test_the_page_tree_is_not_touched_by_retargeting(tmp_path):
    path = make_set(tmp_path / "old.pdf", SET_V1)
    d = Document(path)
    d.slip_sheet(make_set(tmp_path / "new.pdf", [("A-101", "x", "y")]))
    out = str(tmp_path / "o.pdf"); d.save(out)
    r = fitz.open(out)
    assert r.page_count == 3 and len({r.page_xref(i) for i in range(3)}) == 3        # three distinct pages, none duplicated


def test_duplicate_numbers_in_the_new_file_are_reported_and_do_not_poison_later_slips(tmp_path):
    d = Document(make_set(tmp_path / "old.pdf", SET_V1))
    new = make_set(tmp_path / "new.pdf", [("A-101", "x", "one"), ("A-101", "x", "two"), ("B-9", "y", "z"), ("B-9", "y", "w")])
    rep = d.slip_sheet(new)
    assert rep.added == ["A-101", "B-9", "B-9"] and rep.duplicate_numbers == ["A-101", "B-9"]
    assert "added without a number" in rep.summary()
    nums = [d.sheet_record(i).get("number") for i in range(d.page_count)]
    assert [n for n in nums if n].count("B-9") == 1 and nums.count("A-101") == 1      # each number is stored once
    d.slip_sheet(make_set(tmp_path / "next.pdf", [("A-101", "x", "three")]))         # a later slip still works
    assert "three" in d.page_text(0)
    dup = [i for i in range(d.page_count) if d.sheet_record(i).get("unnumbered")]
    assert dup and all(d.sheet_info(i)["number"] == "" for i in dup)                 # never detected again from the text
    d.detect_sheets()                                                                # detection numbers the other pages...
    assert all(d.sheet_info(i)["number"] == "" for i in dup)                         # ...but leaves these alone
    d.set_sheet(dup[0], number="A-777")                                              # the user can give one a number later
    assert d.sheet_info(dup[0])["number"] == "A-777" and "unnumbered" not in d.sheet_record(dup[0])


def test_a_rotation_difference_is_reported(tmp_path):
    old = make_set(tmp_path / "old.pdf", SET_V1)
    f = fitz.open(old); f[0].set_rotation(90); f.saveIncr()
    d = Document(old)
    rep = d.slip_sheet(make_set(tmp_path / "new.pdf", [("A-101", "x", "y")], size=(792, 612)), match="page")   # no rotation
    assert rep.rotation_changed == [("A-101", 90, 0)] and "different page rotation" in rep.summary()
    assert [num for num, *_ in rep.replaced] == ["A-101"]


def test_detect_sheets_changes_nothing_if_it_fails_halfway(v1, monkeypatch):
    d = Document(v1)
    real = Document._detect
    calls = {"n": 0}
    def flaky(self, pno, pattern=None, corner=None):
        calls["n"] += 1
        if pno == 2:
            raise RuntimeError("page 3 cannot be read")
        return real(self, pno, pattern, corner)
    monkeypatch.setattr(Document, "_detect", flaky)
    steps = len(d._undo)
    with pytest.raises(RuntimeError, match="page 3"):
        d.detect_sheets()
    assert [d.sheet_record(i) for i in range(3)] == [{}, {}, {}]      # pages 1 and 2 were not written
    assert len(d._undo) == steps and not d.modified


def test_a_failure_after_an_in_place_change_restores_the_document(v1):
    """The page and object counts stay the same here, so only a full rollback can undo the change."""
    from openrevu._util import mutates

    class D(Document):
        @mutates
        def half_done(self):
            self.doc.xref_set_key(self.doc.page_xref(0), "OR_Test", "(changed)")
            self.set_scale(Scale("m", 0.5))
            raise RuntimeError("fails after two in-place changes")

    d = D(v1)
    steps, modified = len(d._undo), d.modified
    with pytest.raises(RuntimeError, match="fails after"):
        d.half_done()
    assert d.doc.xref_get_key(d.doc.page_xref(0), "OR_Test")[0] == "null"       # the stray key is gone
    assert d.scale.unit != "m" and (len(d._undo), d.modified) == (steps, modified)
    d.add_rect(0, fitz.Rect(1, 1, 9, 9)); assert len(d.markups()) == 1            # the document is fully usable
