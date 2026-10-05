import math
import os

import fitz
import pytest

from openrevu.core import STATUSES, Document, Scale, angle_deg, format_value
from openrevu.pages import compare_pdfs
from openrevu.toolchest import ToolChest


def flat(pts):
    """Flatten a list of points so pytest.approx can compare it."""
    return [c for p in pts for c in p]


def make_pdf(path, pages=3, text=True):
    d = fitz.open()
    for i in range(pages):
        p = d.new_page(width=612, height=792)
        if text:
            p.insert_text((72, 100), f"Plan sheet {i + 1} SECRET data")
    d.save(path)
    return str(path)


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / "t.pdf")


# ---------- markup editing ----------
def test_edit_properties_status_and_replies(pdf):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(50, 50, 150, 100))
    m = d.markups()[0]
    m.set_colors(stroke=(0, 1, 0), fill=(1, 1, 0))
    m.set_width(4); m.set_opacity(0.5); m.set_subject("Door"); m.set_comment("fix")
    m.set_status("Accepted")
    m = d.markups()[0]
    assert tuple(m.annot.colors["stroke"]) == (0, 1, 0) and m.annot.border["width"] == 4
    assert m.subject == "Door" and m.comment == "fix" and m.status == "Accepted"
    with pytest.raises(ValueError):
        m.set_status("bogus")
    m.set_status("")
    assert d.markups()[0].status == ""
    d.add_reply(m, "thanks")
    d.add_reply(m, "ok")
    ms = d.markups()
    assert len(ms) == 1 and [t for _, t in ms[0].replies()] == ["thanks", "ok"]
    d.delete(ms[0])  # replies go with parent
    assert d.markups(include_children=True) == []
    assert "" in STATUSES


def test_move_resize_all_geometry_types(pdf):  # noqa: C901
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(50, 50, 150, 100))
    d.add_line(0, (10, 10), (60, 60))
    d.add_freehand(0, [(10, 10), (30, 40), (50, 10)])
    d.add_polygon(0, [(0, 0), (40, 0), (40, 40)])
    d.add_cloud(0, fitz.Rect(200, 200, 300, 260))
    d.add_highlight(0, fitz.Rect(60, 85, 200, 105))
    for m in d.markups():
        before = fitz.Rect(m.rect)
        m.move(10, 20)
        after = d.markups()[[x.xref for x in d.markups()].index(m.xref)].rect
        assert after.x0 == pytest.approx(before.x0 + 10, abs=1.5) and after.y0 == pytest.approx(before.y0 + 20, abs=1.5), m.kind
    r = [m for m in d.markups() if m.kind == "Square"][0]
    r.resize(fitz.Rect(0, 0, 40, 20))
    nr = [m for m in d.markups() if m.kind == "Square"][0].rect
    assert nr.width == pytest.approx(40, abs=2.5) and nr.height == pytest.approx(20, abs=2.5)


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_geometry_is_visual_on_rotated_cropped_pages(pdf, rot, crop):
    """Public coordinates are what you see: create, read back, move and resize all agree."""
    d = Document(pdf)
    if crop:
        d.doc[0].set_cropbox(fitz.Rect(100, 100, 500, 500))
    d.doc[0].set_rotation(rot)
    vis = d.doc[0].rect
    pts = [(50, 50), (150, 50), (150, 120)]
    d.add_polyline(0, pts)
    d.add_line(0, (20, 30), (90, 60))
    d.add_freehand(0, [(10, 10), (40, 70)])
    d.add_rect(0, fitz.Rect(200, 60, 260, 110))
    got = {m.kind: m for m in d.markups()}
    assert got["PolyLine"].points() == pytest.approx(pts, abs=0.5)
    assert tuple(got["Square"].rect) == pytest.approx((200, 60, 260, 110), abs=1.5)
    assert vis.contains(got["Square"].rect)
    for m in list(d.markups()):
        m.move(10, 20)
    got = {m.kind: m for m in d.markups()}
    assert flat(got["PolyLine"].points()) == pytest.approx(flat([(60, 70), (160, 70), (160, 140)]), abs=0.5)
    assert flat(got["Line"].points()) == pytest.approx(flat([(30, 50), (100, 80)]), abs=0.5)
    assert flat(got["Ink"].points()[0]) == pytest.approx(flat([(20, 30), (50, 90)]), abs=0.5)
    assert tuple(got["Square"].rect) == pytest.approx((210, 80, 270, 130), abs=1.5)
    got["Square"].resize(fitz.Rect(210, 80, 310, 140))
    assert tuple([m for m in d.markups() if m.kind == "Square"][0].rect) == pytest.approx((210, 80, 310, 140), abs=2)
    got["PolyLine"].resize(fitz.Rect(60, 70, 260, 140))
    assert flat([m for m in d.markups() if m.kind == "PolyLine"][0].points()) == pytest.approx(flat([(60, 70), (260, 70), (260, 140)]), abs=2)


@pytest.mark.parametrize("rot", [0, 90, 270])
def test_measurement_label_follows_on_rotated_page(pdf, rot):
    d = Document(pdf)
    d.doc[0].set_rotation(rot)
    d.add_length(0, [(50, 50), (150, 50)])
    m = d.markups()[0]
    lab = lambda: [x for x in d.markups(include_children=True) if x.subject == "Label"][0].rect  # noqa: E731
    c0 = lab()
    m.move(30, 10)
    c1 = lab()
    assert (c1.x0 - c0.x0, c1.y0 - c0.y0) == pytest.approx((30, 10), abs=1.5)
    assert d.markups()[0].measurement()[1] == pytest.approx(100 * d.scale.unit_per_pt)


def test_tools_on_rotated_page_land_where_drawn(pdf):
    d = Document(pdf)
    d.doc[0].set_rotation(90)
    r = fitz.Rect(100, 150, 220, 200)
    d.add_cloud(0, r); d.add_stamp(0, fitz.Rect(300, 300, 400, 340), "OK")
    d.add_count(0, (50, 400)); d.add_text(0, fitz.Rect(300, 500, 400, 530), "t")
    d.add_note(0, (60, 60), "n")
    rects = {m.subject: m.rect for m in d.markups()}
    assert rects["Cloud"].intersects(r) and abs(rects["Cloud"].x0 - 100) < 15
    assert fitz.Rect(299, 299, 401, 341).contains(rects["Stamp"]) and rects["Stamp"].width > 90  # aspect-fitted, upright
    assert rects["Count"].contains(fitz.Point(50, 400))
    assert abs(rects["Text"].x0 - 300) < 2 and abs(rects["Text"].y0 - 500) < 2
    assert d.markup_at(0, (50, 400)).subject == "Count"
    hit = d.search("Plan")[0][1]
    h = d.add_highlight(0, hit)
    assert fitz.Rect(hit).contains(h.rect) or h.rect.intersects(d._ru(0, hit))


def test_page_ops_on_rotated_pages(pdf, tmp_path):
    d = Document(pdf)
    d.doc[0].set_rotation(90)
    d.crop_page(0, fitz.Rect(0, 0, 300, 100))
    assert (d.doc[0].rect.width, d.doc[0].rect.height) == pytest.approx((300, 100))
    d.crop_page(0, fitz.Rect(10, 10, 110, 60))  # relative to the visible area
    assert (d.doc[0].rect.width, d.doc[0].rect.height) == pytest.approx((100, 50))
    d.insert_blank(at=1); d.doc[1].set_rotation(270)
    d.header_footer(footer=("", "FOOT {page}", ""))
    d.bates("B-")
    d.watermark("WM", pages=[1])
    for p in (0, 1):
        assert "FOOT" in d.page_text(p) and "B-000001" in d.page_text(0)
    # text must read upright: its direction in visual space is (1, 0)
    for p in (0, 1):
        for b in d.doc[p].get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                if "FOOT" in "".join(s["text"] for s in ln["spans"]):
                    dx, dy = ln["dir"]
                    v = fitz.Point(dx, dy) * d.doc[p].rotation_matrix - fitz.Point(0, 0) * d.doc[p].rotation_matrix
                    assert (round(v.x), round(v.y)) == (1, 0), (p, ln["dir"])


def test_search_hits_are_visual_and_redaction_on_rotated_page(pdf, tmp_path):
    d = Document(pdf)
    d.doc[0].set_rotation(90)
    hit = d.search("SECRET", pages=[0])[0][1]
    assert d.doc[0].rect.contains(hit)
    d.mark_redaction_text("SECRET"); d.apply_redactions()
    assert "SECRET" not in d.page_text(0)


def test_measurement_label_moves_and_deletes_with_parent(pdf):
    d = Document(pdf)
    d.add_length(0, [(0, 100), (100, 100)])
    assert len(d.markups()) == 1 and len(d.markups(include_children=True)) == 2
    m = d.markups()[0]
    lab_before = [x for x in d.markups(include_children=True) if x.subject == "Label"][0].rect
    m.move(10, 10)
    lab_after = [x for x in d.markups(include_children=True) if x.subject == "Label"][0].rect
    assert lab_after.x0 == pytest.approx(lab_before.x0 + 10, abs=1)
    d.delete(d.markups()[0])
    assert d.markups(include_children=True) == []


def test_hit_testing_and_copy_paste(pdf):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(50, 50, 150, 100))
    d.add_line(0, (200, 200), (260, 240), arrow=True)
    d.add_freehand(0, [(300, 300), (320, 340)])
    assert d.markup_at(0, (100, 75)).kind == "Square"
    assert d.markup_at(0, (500, 500)) is None
    for m in list(d.markups()):
        d.add_from_dict(1, m.to_dict(), offset=(5, 5))
    p1 = [m for m in d.markups() if m.page_no == 1]
    assert sorted(m.kind for m in p1) == ["Ink", "Line", "Square"]
    ln = [m for m in p1 if m.kind == "Line"][0]
    assert ln.to_dict()["arrow"] is True and ln.annot.vertices[0] == pytest.approx((205, 205), abs=1)


# ---------- undo / redo ----------
def test_undo_redo(pdf):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    d.add_ellipse(0, fitz.Rect(60, 60, 90, 90))
    assert len(d.markups()) == 2
    assert d.undo() and len(d.markups()) == 1
    assert d.undo() and len(d.markups()) == 0
    assert not d.undo()
    assert d.redo() and d.redo() and len(d.markups()) == 2
    assert not d.redo()
    d.undo(); d.add_rect(0, fitz.Rect(1, 1, 9, 9))  # new action clears redo
    assert not d.redo()
    m = d.markups()[0]
    m.set_width(7)
    m2 = d.markups()[0]
    assert m2.annot.border["width"] == 7
    d.undo()
    m3 = d.markups()[0]
    assert m3.annot.border["width"] != 7


def test_undo_page_ops_and_scales(pdf):
    d = Document(pdf)
    d.set_scale(Scale("m", 0.1), page=2)
    d.delete_pages([0])
    assert d.page_count == 2 and d.scale_for(1).unit_per_pt == 0.1
    d.undo()
    assert d.page_count == 3 and d.scale_for(2).unit_per_pt == 0.1


# ---------- measurements ----------
def test_all_measurement_kinds(pdf):
    d = Document(pdf)
    d.set_scale(Scale.from_calibration((0, 0), (100, 0), 10, "m"))  # 0.1 m/pt
    sq = [(0, 0), (100, 0), (100, 100), (0, 100)]
    assert d.add_perimeter(0, sq)[1] == pytest.approx(40)
    _, v = d.add_area(0, sq, cutouts=[[(0, 0), (50, 0), (50, 50), (0, 50)]])
    assert v == pytest.approx(100 - 25)
    with pytest.raises(ValueError):
        d.add_area(0, [(0, 0), (10, 0), (10, 10)], cutouts=[sq])
    _, vol = d.add_area(0, sq, depth=0.5)
    assert vol == pytest.approx(50)
    assert d.add_rect_area(0, fitz.Rect(0, 0, 50, 20))[1] == pytest.approx(10)
    assert d.add_ellipse_area(0, fitz.Rect(0, 0, 100, 100))[1] == pytest.approx(math.pi * 25)
    assert d.add_diameter(0, (0, 0), (60, 0))[1] == pytest.approx(6)
    assert d.add_angle(0, (10, 0), (0, 0), (0, 10))[1] == pytest.approx(90)
    d.add_count(0, (5, 5), group="Door"); d.add_count(0, (9, 9), group="Door"); d.add_count(0, (1, 1), group="Window")
    t = d.takeoff()
    assert t[("count", "ea")] == 3 and t[("angle", "deg")] == pytest.approx(90)
    s = d.takeoff_by_subject()
    assert s[("Door", "count", "ea")] == (2, 2) and s[("Window", "count", "ea")] == (1, 1)
    assert s[("Area", "area", "m")][0] == 3  # area + rect + ellipse (volume is its own kind)
    out = d.markups()
    assert all(m.measurement() for m in out if m.subject != "Cutout")


def test_angle_math():
    assert angle_deg((1, 0), (0, 0), (0, 1)) == pytest.approx(90)
    assert angle_deg((1, 0), (0, 0), (-1, 0)) == pytest.approx(180)
    with pytest.raises(ValueError):
        angle_deg((0, 0), (0, 0), (1, 1))


def test_per_page_scale_persist_and_ratio(pdf, tmp_path):
    d = Document(pdf)
    d.set_scale(Scale.from_ratio(0.25, 1, "ft"))  # 1/4" = 1'
    assert d.scale.unit_per_pt == pytest.approx(1 / 18)
    d.set_scale(Scale("m", 0.5), page=1)
    _, a = d.add_length(0, [(0, 0), (18, 0)]); _, b = d.add_length(1, [(0, 0), (18, 0)])
    assert a == pytest.approx(1) and b == pytest.approx(9)
    out = str(tmp_path / "o.pdf"); d.save(out)
    r = Document(out)
    assert r.scale_for(1).unit == "m" and r.scale_for(0).unit == "ft" and r.scale_for(2).unit == "ft"
    with pytest.raises(ValueError):
        Scale.from_ratio(0, 1, "ft")


# ---------- text markup, stamps, signatures ----------
def test_text_markups_stamps_signature(pdf):
    d = Document(pdf)
    area = fitz.Rect(70, 85, 250, 105)
    for fn in (d.add_highlight, d.add_underline, d.add_strikeout, d.add_squiggly):
        fn(0, area)
    d.add_callout(0, fitz.Rect(300, 300, 400, 330), "see here", (200, 150))
    d.add_stamp(0, fitz.Rect(300, 50, 500, 110), "REVIEWED")
    d.add_stamp(0, fitz.Rect(300, 120, 500, 160), "DRAFT", date=False)
    d.add_signature_text(0, fitz.Rect(300, 600, 500, 660), "Jane Doe")
    kinds = [m.subject for m in d.markups()]
    for k in ("Highlight", "Underline", "Strikeout", "Squiggly", "Callout", "Stamp", "Signature"):
        assert k in kinds
    d.render(0, 1.0)  # appearance streams render without error


def test_image_stamp(pdf, tmp_path):
    img = tmp_path / "s.png"
    fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 10), False).save(str(img))
    d = Document(pdf)
    d.add_image_stamp(0, fitz.Rect(10, 10, 110, 60), str(img))
    assert d.markups()[0].kind == "Stamp"


# ---------- tool chest ----------
def test_toolchest(pdf, tmp_path):
    d = Document(pdf)
    d.add_cloud(0, fitz.Rect(100, 100, 200, 160), color=(0, 0, 1))
    m = d.markups()[0]
    tc = ToolChest(str(tmp_path / "tc.json"))
    tc.add("rev cloud", m)
    tc2 = ToolChest(str(tmp_path / "tc.json"))
    assert "rev cloud" in tc2.items
    tc2.place(d, "rev cloud", 1, (20, 20))
    p = [x for x in d.markups() if x.page_no == 1][0]
    assert p.rect.x0 == pytest.approx(20, abs=2) and tuple(p.annot.colors["stroke"]) == (0, 0, 1)
    tc2.remove("rev cloud")
    assert ToolChest(str(tmp_path / "tc.json")).items == {}


# ---------- page management ----------
def test_page_operations(pdf, tmp_path):
    d = Document(pdf)
    d.insert_blank(at=1)
    assert d.page_count == 4 and "Plan sheet 2" in d.page_text(2)
    d.move_page(0, 3)
    assert "Plan sheet 1" in d.page_text(3)
    d.reorder([3, 0, 1, 2])
    assert "Plan sheet 1" in d.page_text(0)
    with pytest.raises(ValueError):
        d.reorder([0, 0, 1, 2])
    d.rotate_pages([0], 90)
    assert d.doc[0].rotation == 90
    with pytest.raises(ValueError):
        d.rotate_pages([0], 45)
    d.crop_page(0, fitz.Rect(0, 0, 300, 300))
    assert d.doc[0].rect.width == 300 or d.doc[0].rect.height == 300
    with pytest.raises(ValueError):
        d.crop_page(1, fitz.Rect(1000, 1000, 1100, 1100))
    d.delete_pages([1])
    assert d.page_count == 3
    with pytest.raises(ValueError):
        d.delete_pages([0, 1, 2])
    with pytest.raises(IndexError):
        d.delete_pages([9])


def test_scales_follow_pages(pdf):
    d = Document(pdf)
    d.set_scale(Scale("m", 0.3), page=1)
    d.insert_blank(at=0)
    assert d.scale_for(2).unit == "m" and d.scale_for(1).unit != "m" or d.scale_for(1).unit_per_pt != 0.3
    d.move_page(2, 0)
    assert d.scale_for(0).unit_per_pt == 0.3
    d.delete_pages([0])
    assert 0.3 not in [s.unit_per_pt for s in d.page_scales.values()]


def test_merge_split_extract_images(pdf, tmp_path):
    other = make_pdf(tmp_path / "o.pdf", pages=2)
    d = Document(pdf)
    assert d.insert_pdf(other, at=1) == 2 and d.page_count == 5
    d.insert_pdf(other, pages=[1])
    assert d.page_count == 6
    outs = d.split(str(tmp_path / "sp"), every=4)
    assert [fitz.open(o).page_count for o in outs] == [4, 2]
    assert [fitz.open(o).page_count for o in d.split(str(tmp_path / "r"), ranges=[(0, 0), (2, 3)])] == [1, 2]
    with pytest.raises(ValueError):
        d.split(str(tmp_path / "x"))
    with pytest.raises(IndexError):
        d.split(str(tmp_path / "x"), ranges=[(0, 99)])
    e = d.extract_pages([0, 5], str(tmp_path / "e.pdf"))
    assert fitz.open(e).page_count == 2
    img = tmp_path / "i.png"
    fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 30), False).save(str(img))
    n = d.page_count
    d.insert_image_page(str(img), at=0)
    assert d.page_count == n + 1
    assert Document.from_images([str(img), str(img)]).page_count == 2


def test_navigation_bookmarks_links_labels(pdf):
    d = Document(pdf)
    d.add_bookmark("Intro", 0); d.add_bookmark("Details", 2, level=2)
    assert [t[1] for t in d.toc()] == ["Intro", "Details"]
    d.add_link_uri(0, fitz.Rect(10, 10, 100, 30), "https://example.org")
    d.add_link_goto(0, fitz.Rect(10, 40, 100, 60), 2)
    kinds = sorted(l["kind"] for l in d.links(0))
    assert kinds == sorted([fitz.LINK_URI, fitz.LINK_GOTO])
    d.set_page_labels([{"startpage": 0, "prefix": "A-", "style": "D", "firstpagenum": 1}])
    assert d.page_label(1) == "A-2"
    with pytest.raises(IndexError):
        d.add_bookmark("x", 99)


def test_search_and_text(pdf):
    d = Document(pdf)
    hits = d.search("SECRET")
    assert len(hits) == 3 and {p for p, _ in hits} == {0, 1, 2}
    assert d.search("SECRET", pages=[1])[0][0] == 1 and d.search("nothing-here") == []


def test_ocr_reports_missing_tesseract(pdf, monkeypatch):
    d = Document(pdf)
    monkeypatch.setattr(Document, "ocr_available", staticmethod(lambda: False))
    with pytest.raises(RuntimeError, match="tesseract"):
        d.ocr()


# ---------- document-wide ----------
def test_watermark_header_footer_bates(pdf):
    d = Document(pdf)
    d.watermark("CONFIDENTIAL")
    d.header_footer(header=("L", "", "R"), footer=("", "Page {page} of {pages}", ""), name="x")
    d.bates("BN-", start=10, digits=4)
    t = d.page_text(1)
    assert "CONFIDENTIAL" in t and "Page 2 of 3" in t and "BN-0011" in t
    d.watermark("ONLY1", pages=[0])
    assert "ONLY1" in d.page_text(0) and "ONLY1" not in d.page_text(1)


def test_watermark_image(pdf, tmp_path):
    img = tmp_path / "w.png"
    fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False).save(str(img))
    d = Document(pdf)
    d.watermark_image(str(img), pages=[0])
    assert d.doc[0].get_images()


def test_redaction_removes_text_permanently(pdf, tmp_path):
    d = Document(pdf)
    assert d.mark_redaction_text("SECRET") == 3
    d.mark_redaction(0, fitz.Rect(0, 0, 10, 10))
    assert "SECRET" in d.page_text(0)  # still there until applied
    assert d.apply_redactions() == 4
    out = str(tmp_path / "r.pdf"); d.save(out)
    assert all("SECRET" not in fitz.open(out)[i].get_text() for i in range(3))
    assert "Plan sheet 1" in fitz.open(out)[0].get_text()


def test_flatten(pdf, tmp_path):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    d.add_text(0, fitz.Rect(100, 300, 200, 330), "flat text")
    d.flatten()
    assert d.markups() == [] and "flat text" in d.page_text(0)


def test_encrypt_and_open(pdf, tmp_path):
    d = Document(pdf)
    out = str(tmp_path / "enc.pdf")
    d.save_encrypted(out, "pw1")
    with pytest.raises(PermissionError):
        Document(out)
    with pytest.raises(PermissionError):
        Document(out, "wrong")
    assert Document(out, "pw1").page_count == 3
    assert fitz.open(out).needs_pass


def test_metadata_optimize(pdf, tmp_path):
    d = Document(pdf)
    d.set_metadata(title="T", author="A", bogus="x")
    out = str(tmp_path / "opt.pdf")
    before, after = d.optimize(out)
    assert after > 0 and fitz.open(out).metadata["title"] == "T"


def test_forms_and_layers(tmp_path):
    d = fitz.open(); p = d.new_page()
    w = fitz.Widget(); w.field_name = "name"; w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.rect = fitz.Rect(50, 50, 200, 70); w.field_value = ""
    p.add_widget(w)
    ocg = d.add_ocg("Dims", on=True)
    path = str(tmp_path / "f.pdf"); d.save(path)
    doc = Document(path)
    f = doc.form_fields()
    assert f[0]["name"] == "name" and f[0]["type"] == "Text"
    doc.set_form_value("name", "Bob")
    assert doc.form_fields()[0]["value"] == "Bob"
    with pytest.raises(KeyError):
        doc.set_form_value("zzz", "x")
    assert doc.layers()[0]["text"] == "Dims"
    doc.set_layer(0, False)
    assert doc.layers()[0]["on"] is False


def test_thumbnail_and_export(pdf, tmp_path):
    d = Document(pdf)
    assert d.thumbnail(0, 100).width == 100
    png = d.export_png(0, str(tmp_path / "p.png"), dpi=72, clip=fitz.Rect(0, 0, 100, 100))
    assert os.path.getsize(png) > 0


def test_compare(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", pages=2)
    d = fitz.open(a); d[1].insert_text((72, 300), "EXTRA NEW TEXT", fontsize=20)
    b = str(tmp_path / "b.pdf"); d.save(b)
    stats = compare_pdfs(a, b, str(tmp_path / "diff.pdf"), dpi=50)
    assert stats[0] == 0 and stats[1] > 0 and fitz.open(str(tmp_path / "diff.pdf")).page_count == 2


def test_summary_csv(pdf, tmp_path):
    d = Document(pdf)
    d.add_count(0, (5, 5), group="Door"); d.add_count(0, (9, 9), group="Door")
    p = tmp_path / "s.csv"; d.export_summary_csv(str(p))
    assert "Door,count,2,2.0000,ea" in p.read_text()


# ---------- digital signatures ----------
def test_digital_signature(pdf, tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    import datetime
    from openrevu.sign import list_signatures, sign_pdf

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Test Signer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    p12 = tmp_path / "id.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(b"id", key, cert, None, serialization.BestAvailableEncryption(b"pw")))
    out = str(tmp_path / "signed.pdf")
    sign_pdf(pdf, out, str(p12), "pw", reason="approve")
    sigs = list_signatures(out)
    assert sigs[0][1] == "Test Signer" and sigs[0][2] and sigs[0][3]
    with pytest.raises(ValueError):
        sign_pdf(pdf, str(tmp_path / "bad.pdf"), str(p12), "wrong")
    # tampering after signing breaks coverage
    with open(out, "ab") as f:
        f.write(b"\n% appended")
    assert not list_signatures(out)[0][3]


# ---------- CLI ----------
def test_cli(pdf, tmp_path):
    from openrevu.cli import main
    o = lambda n: str(tmp_path / n)  # noqa: E731
    assert main(["watermark", pdf, o("w.pdf"), "--text", "X"]) == 0
    assert main(["footer", pdf, o("f.pdf")]) == 0
    assert main(["numbering", pdf, o("n.pdf"), "--prefix", "A"]) == 0
    assert main(["redact", pdf, o("r.pdf"), "--text", "SECRET"]) == 0
    assert "SECRET" not in fitz.open(o("r.pdf"))[0].get_text()
    assert main(["merge", pdf, o("m.pdf"), pdf]) == 0 and fitz.open(o("m.pdf")).page_count == 6
    assert main(["flatten", pdf, o("fl.pdf")]) == 0
    assert main(["optimize", pdf, o("op.pdf")]) == 0
    assert main(["encrypt", pdf, o("e.pdf"), "--password", "x"]) == 0
    assert main(["csv", pdf, o("c.csv")]) == 0 and main(["summary", pdf, o("s.csv")]) == 0
    assert main(["split", pdf, o("sp"), "--every", "2"]) == 0
    assert main(["compare", pdf, o("w.pdf"), o("cmp.pdf")]) == 0
    assert main(["watermark", o("missing.pdf"), o("z.pdf"), "--text", "X"]) == 1
    from openrevu.core import Document as D
    assert main(["ocr", pdf, o("ocr.pdf")]) == (0 if D.ocr_available() else 1)


def test_stale_markup_raises_instead_of_crashing(pdf):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    m = d.markups()[0]
    d.undo()
    with pytest.raises(RuntimeError, match="stale"):
        m.rect
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    m = d.markups()[0]
    d.save()
    with pytest.raises(RuntimeError, match="stale"):
        m.subject


def test_format_value():
    assert format_value("area", 12.5, "ft") == "12.50 ft²"
    assert format_value("volume", 3, "m") == "3.00 m³"
    assert format_value("length", 3, "m") == "3.00 m"
    assert format_value("angle", 90, "deg") == "90.0°"
    assert format_value("count", 4, "ea") == "4 ea"


# ---------- regressions from independent review ----------
def test_save_in_place_keeps_encryption(pdf, tmp_path):
    out = str(tmp_path / "enc.pdf")
    Document(pdf).save_encrypted(out, "pw")
    d = Document(out, "pw")
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    d.save()
    assert fitz.open(out).needs_pass
    assert len(Document(out, "pw").markups()) == 1
    d.add_rect(0, fitz.Rect(60, 60, 90, 90)); d.save(str(tmp_path / "copy.pdf"))
    assert fitz.open(str(tmp_path / "copy.pdf")).needs_pass


def test_apply_to_all_pages_scale_is_undoable(pdf):
    d = Document(pdf)
    d.set_scale(Scale("m", 0.3), page=0)
    d.set_scale(Scale("ft", 0.5), reset_pages=True)
    assert d.page_scales == {}
    d.undo()
    assert d.page_scales[0].unit_per_pt == 0.3


def test_flatten_keeps_scales(pdf):
    d = Document(pdf)
    d.set_scale(Scale("m", 0.3), page=1)
    d.flatten()
    assert d.scale_for(1).unit_per_pt == 0.3


def test_redaction_removes_annotation_text(pdf):
    d = Document(pdf)
    d.add_note(0, (200, 200), "SECRET note")
    d.add_note(1, (300, 300), "harmless")
    assert d.mark_redaction_text("SECRET") == 4  # 3 text hits + the note
    d.apply_redactions()
    comments = [m.comment for m in d.markups()]
    assert "SECRET note" not in comments and "harmless" in comments
    assert not any("SECRET" in d.page_text(i) for i in range(3))


def test_covered_annotation_is_removed_by_redaction(pdf):
    d = Document(pdf)
    d.add_note(0, (200, 200), "private")
    d.mark_redaction(0, fitz.Rect(190, 190, 230, 230))
    d.apply_redactions()
    assert [m for m in d.markups() if m.comment == "private"] == []


def test_cli_encrypted_input(pdf, tmp_path):
    from openrevu.cli import main
    enc = str(tmp_path / "enc.pdf")
    assert main(["encrypt", pdf, enc, "--password", "pw"]) == 0
    assert main(["watermark", enc, str(tmp_path / "o.pdf"), "--text", "X"]) == 1
    assert main(["watermark", enc, str(tmp_path / "o.pdf"), "--text", "X", "--in-password", "pw"]) == 0
    assert main(["encrypt", enc, str(tmp_path / "re.pdf"), "--password", "new", "--in-password", "pw"]) == 0
    assert Document(str(tmp_path / "re.pdf"), "new").page_count == 3


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_watermark_image_orientation(pdf, tmp_path, rot):
    # left half black, right half white: after rendering the black half must still be on the left
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), False)
    pm.set_rect(pm.irect, (255, 255, 255)); pm.set_rect(fitz.IRect(0, 0, 20, 20), (0, 0, 0))
    img = tmp_path / "lr.png"; pm.save(str(img))
    d = Document(pdf)
    d.doc[0].set_rotation(rot)
    d.watermark_image(str(img), pages=[0], scale=0.6)
    pix = d.render(0, 1.0)
    w, h = pix.width, pix.height
    cy = h // 2
    dark = [x for x in range(w) if pix.pixel(x, cy)[0] < 60]
    assert dark, "image not drawn"
    cx = (min(dark) + max(dark)) / 2
    assert cx < w / 2 - 5, f"black half should be left of centre, got {cx} of {w}"


# ---------- closing the "known gaps" ----------
def test_text_markup_moves_but_does_not_resize(pdf):
    d = Document(pdf)
    d.add_highlight(0, fitz.Rect(70, 85, 250, 105)); d.add_underline(0, fitz.Rect(70, 85, 250, 105))
    for m in d.markups():
        r0 = fitz.Rect(m.rect)
        m.move(15, 25)
        r1 = [x for x in d.markups() if x.xref == m.xref][0].rect
        assert (r1.x0 - r0.x0, r1.y0 - r0.y0) == pytest.approx((15, 25), abs=0.5), m.kind
        with pytest.raises(ValueError, match="move"):
            m.resize(fitz.Rect(0, 0, 10, 10))


def test_resizing_measurements_remeasures(pdf):
    d = Document(pdf)
    d.set_scale(Scale("m", 0.1))
    d.add_length(0, [(100, 100), (200, 100)])
    d.add_perimeter(0, [(100, 200), (200, 200), (200, 300), (100, 300)])
    d.add_area(0, [(300, 200), (400, 200), (400, 300), (300, 300)], depth=2.0)
    d.add_ellipse_area(0, fitz.Rect(300, 400, 400, 500))
    d.add_diameter(0, (100, 400), (200, 400))
    before = {m.measurement()[0]: m.measurement()[1] for m in d.markups()}
    assert before["length"] == pytest.approx(10) and before["volume"] == pytest.approx(200)
    for m in list(d.markups()):
        r = m.rect
        m.resize(fitz.Rect(r.x0, r.y0, r.x0 + (r.width - 2) * 2 + 2, r.y0 + (r.height - 2) * 2 + 2))
    after = {m.measurement()[0]: m.measurement()[1] for m in d.markups()}
    assert after["length"] == pytest.approx(20, rel=0.02)
    assert after["perimeter"] == pytest.approx(80, rel=0.03)
    assert after["volume"] == pytest.approx(200 * 4, rel=0.06)
    assert after["diameter"] == pytest.approx(20, rel=0.06)
    assert after["area"] == pytest.approx(before["area"] * 4, rel=0.08)
    assert len(d.markups()) == 5 and len(d.markups(include_children=True)) == 10  # labels re-created, no leaks
    labels = {x.comment for x in d.markups(include_children=True) if x.subject == "Label"}
    assert any(l.startswith("20.0") or "20.0" in l or "20.00" in l for l in labels)
    d.add_count(0, (5, 5))
    c = [m for m in d.markups() if m.measurement() and m.measurement()[0] == "count"][0]
    with pytest.raises(ValueError, match="fixed size"):
        c.resize(fitz.Rect(0, 0, 30, 30))


def test_resizing_an_area_with_cutouts_scales_the_cutouts_too(pdf):
    d = Document(pdf)
    d.set_scale(Scale("m", 0.1))
    outer = [(100, 100), (300, 100), (300, 300), (100, 300)]           # 200 x 200 pt = 400 m2
    hole = [(150, 150), (200, 150), (200, 200), (150, 200)]            # 50 x 50 pt = 25 m2
    d.add_area(0, outer, cutouts=[hole])
    m = [x for x in d.markups() if x.subject == "Area"][0]
    assert m.measurement()[1] == pytest.approx(375.0)                  # (40000 - 2500) pt2 * 0.01 m2/pt2
    r = m.rect
    m.resize(fitz.Rect(r.x0, r.y0, r.x0 + (r.width - 3) * 2 + 3, r.y0 + (r.height - 3) * 2 + 3))
    m = [x for x in d.markups() if x.subject == "Area"][0]
    cuts = [c for c in m._children_markups() if c.subject == "Cutout"]
    assert len(cuts) == 1                                              # the cut-out survived the re-measure
    assert cuts[0].rect.width == pytest.approx(2 * 50, abs=6)          # and was scaled with its parent
    assert m.measurement()[1] == pytest.approx(4 * 375.0, rel=0.06)
    labels = [c for c in m._children_markups() if c.subject == "Label"]
    assert len(labels) == 1
    m.move(20, 30)                                                     # moving still drags the cut-out along
    cut = [c for c in [x for x in d.markups() if x.subject == "Area"][0]._children_markups() if c.subject == "Cutout"][0]
    assert cut.rect.x0 > 150 + 15


def test_resizing_an_angle_remeasures_it(pdf):
    d = Document(pdf)
    d.add_angle(0, (100, 0 + 100), (100, 200), (200, 200))             # a right angle at the vertex (100, 200)
    m = d.markups()[0]
    assert m.measurement()[1] == pytest.approx(90)
    r = m.rect
    m.resize(fitz.Rect(r.x0, r.y0, r.x0 + (r.width - 3) * 3 + 3, r.y0 + (r.height - 3) * 1 + 3))   # stretch sideways
    m = d.markups()[0]
    pts = m.points()
    from openrevu.core import angle_deg
    assert m.measurement()[1] == pytest.approx(angle_deg(*pts), abs=0.01) and m.measurement()[1] == pytest.approx(90)
    # a skewed angle changes its value when the shape is stretched in one direction only
    d2 = Document(pdf)
    d2.add_angle(0, (100, 100), (200, 200), (300, 100))               # 90 degrees, apex at the bottom
    m2 = d2.markups()[0]; r2 = m2.rect
    m2.resize(fitz.Rect(r2.x0, r2.y0, r2.x0 + (r2.width - 3) * 2 + 3, r2.y0 + (r2.height - 3) + 3))
    after = d2.markups()[0].measurement()[1]
    assert after == pytest.approx(angle_deg(*d2.markups()[0].points()), abs=0.01) and after > 100   # opened up


def room_pdf(path, rotation=0):
    d = fitz.open(); p = d.new_page(width=600, height=800)
    p.draw_rect(fitz.Rect(100, 100, 400, 300), width=3)           # outer walls: inner 294 x 194 pt
    p.draw_rect(fitz.Rect(200, 150, 240, 190), width=2, fill=(0.5, 0.5, 0.5))  # column
    p.draw_line((100, 500), (300, 500), width=3)                  # stray wall: not enclosed
    p.set_rotation(rotation)
    d.save(path); return str(path)


@pytest.mark.parametrize("rot", [0, 90])
def test_fill_area_measures_room_minus_columns(tmp_path, rot):
    pytest.importorskip("scipy")
    d = Document(room_pdf(tmp_path / "room.pdf", rot))
    d.set_scale(Scale("m", 0.01))  # 1 pt = 1 cm
    pt = (150, 250) if rot == 0 else d._tv(0, (150, 250))
    _, v = d.add_fill_area(0, pt)
    inner = 294 * 194 - 41 * 41   # room interior minus the column incl. its stroke
    assert v * 10000 == pytest.approx(inner, rel=0.04)
    m = [x for x in d.markups() if x.subject == "Fill Area"][0]
    assert m.measurement()[0] == "area"
    assert d.takeoff()[("area", "m")] == pytest.approx(v)
    outer, holes = d.region_polygon(0, pt)
    assert len(holes) == 1 and len(outer) >= 4


def test_fill_area_errors(tmp_path):
    pytest.importorskip("scipy")
    d = Document(room_pdf(tmp_path / "room.pdf"))
    with pytest.raises(ValueError, match="not enclosed"):
        d.add_fill_area(0, (400, 650))        # open space outside any room
    with pytest.raises(ValueError, match="open"):
        d.add_fill_area(0, (100, 200))        # exactly on a wall line


def test_undo_history_of_protected_documents_is_encrypted(pdf, tmp_path):
    enc = str(tmp_path / "enc.pdf")
    Document(pdf).save_encrypted(enc, "pw")
    d = Document(enc, "pw")
    d.add_rect(0, fitz.Rect(10, 10, 50, 50))
    assert d._undo and all(fitz.open("pdf", b).needs_pass for b in d._undo)
    assert d.undo() and d.markups() == [] and d.redo() and len(d.markups()) == 1
    assert all(fitz.open("pdf", b).needs_pass for b in d._undo + d._redo)
    plain = Document(pdf); plain.add_rect(0, fitz.Rect(1, 1, 5, 5))
    assert not fitz.open("pdf", plain._undo[0]).needs_pass


def test_ocr_makes_scans_searchable(tmp_path):
    from openrevu.core import Document as D
    if not D.ocr_available():
        pytest.skip("tesseract not installed")
    src = fitz.open(); p = src.new_page(); p.insert_text((72, 150), "INVOICE TOTAL 4500", fontsize=36)
    scan = fitz.open(); sp = scan.new_page(width=p.rect.width, height=p.rect.height)
    sp.insert_image(sp.rect, pixmap=p.get_pixmap(dpi=200)); path = str(tmp_path / "scan.pdf"); scan.save(path)
    d = Document(path)
    assert d.page_text(0).strip() == "" and d.search("INVOICE") == []
    assert d.ocr() == 1
    assert "INVOICE" in d.page_text(0) and d.search("4500")
    assert d.ocr() == 0  # already has text: skipped


def _selfsigned(tmp_path, cn="Test Signer"):
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(7).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    p12 = tmp_path / f"{cn}.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(b"id", key, cert, None, serialization.BestAvailableEncryption(b"pw")))
    pem = tmp_path / f"{cn}.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(p12), str(pem)


def test_signature_trust_validation(pdf, tmp_path):
    from openrevu.sign import sign_pdf, verify_signatures
    p12, pem = _selfsigned(tmp_path)
    _, other_pem = _selfsigned(tmp_path, "Someone Else")
    out = str(tmp_path / "s.pdf"); sign_pdf(pdf, out, p12, "pw")
    untrusted = verify_signatures(out)[0]
    assert untrusted["intact"] and untrusted["valid"] and not untrusted["trusted"] and untrusted["whole_file"]
    assert verify_signatures(out, [pem])[0]["trusted"]
    assert not verify_signatures(out, [other_pem])[0]["trusted"]
    with open(out, "ab") as f:
        f.write(b"\n% tamper")
    assert not verify_signatures(out, [pem])[0]["whole_file"]


def test_create_and_fill_form_fields(pdf, tmp_path):
    d = Document(pdf)
    d.add_form_field(0, fitz.Rect(50, 50, 200, 70), "name", "text", "Bob")
    d.add_form_field(0, fitz.Rect(50, 90, 70, 110), "agree", "checkbox")
    d.add_form_field(0, fitz.Rect(50, 130, 200, 150), "colour", "combo", "red", choices=["red", "green"])
    d.add_form_field(1, fitz.Rect(50, 50, 120, 80), "go", "button", "Submit")
    f = {x["name"]: x for x in d.form_fields()}
    assert set(f) == {"name", "agree", "colour", "go"} and f["name"]["value"] == "Bob" and f["colour"]["page"] == 0
    d.set_form_value("name", "Alice")
    out = str(tmp_path / "form.pdf"); d.save(out)
    assert {x["name"]: x["value"] for x in Document(out).form_fields()}["name"] == "Alice"
    for bad in (("name", "text"), ("", "text"), ("x", "bogus"), ("y", "combo")):
        with pytest.raises(ValueError):
            d.add_form_field(0, fitz.Rect(1, 1, 50, 20), *bad)
    assert len(d._undo) == 5  # four successful adds + the value change; failed attempts left no undo steps
    d.undo()  # undoes set_form_value
    assert {x["name"]: x["value"] for x in d.form_fields()}["name"] == "Bob"


def test_failed_operations_do_not_pollute_undo(pdf):
    d = Document(pdf)
    n = len(d._undo)
    for fn in (lambda: d.delete_pages([9]), lambda: d.rotate_pages([0], 45), lambda: d.reorder([0, 0, 1]),
               lambda: d.add_area(0, [(0, 0), (1, 0), (1, 1)], depth=-1)):
        with pytest.raises((ValueError, IndexError)):
            fn()
    assert len(d._undo) == n


# ---------- second-review regressions ----------
def test_signature_verdict_catches_edits_after_signing(pdf, tmp_path):
    from openrevu.sign import sign_pdf, verify_signatures
    p12, pem = _selfsigned(tmp_path)
    out = str(tmp_path / "s.pdf"); sign_pdf(pdf, out, p12, "pw")
    assert verify_signatures(out, [pem])[0]["verdict_ok"]
    assert not verify_signatures(out)[0]["verdict_ok"]            # untrusted
    ed = fitz.open(out); ed[0].insert_text((50, 300), "FORGED TEXT"); ed.saveIncr(); ed.close()
    r = verify_signatures(out, [pem])[0]
    assert r["intact"] and r["trusted"]                            # the old flags alone would have said "fine"
    assert not r["whole_file"] and not r["verdict_ok"]


def test_fill_without_scipy_is_a_clean_error(tmp_path, monkeypatch):
    import sys
    d = Document(room_pdf(tmp_path / "r.pdf"))
    monkeypatch.setitem(sys.modules, "scipy", None)
    monkeypatch.setitem(sys.modules, "scipy.ndimage", None)
    with pytest.raises(RuntimeError, match="scipy"):
        d.add_fill_area(0, (150, 250))


def test_fill_is_fast_with_many_small_obstacles():
    np = pytest.importorskip("numpy"); pytest.importorskip("scipy")
    import time
    from openrevu.fill import find_region
    free = np.ones((1500, 1500), bool)
    free[[0, -1], :] = False; free[:, [0, -1]] = False
    free[1:5, 1:] = False  # make sure the wall is closed with margin
    for y in range(20, 1480, 12):
        for x in range(20, 1480, 12):
            free[y:y + 3, x:x + 3] = False  # ~15k islands
    t = time.time()
    outer, holes = find_region(free, (10, 700), min_hole=4)
    assert time.time() - t < 5 and len(holes) > 10000


def test_resize_does_not_add_label_that_was_not_there(pdf):
    d = Document(pdf)
    d.add_perimeter(0, [(100, 100), (200, 100), (200, 200)], label=False)
    m = d.markups()[0]; r = m.rect
    m.resize(fitz.Rect(r.x0, r.y0, r.x1 + 50, r.y1 + 50))
    assert len(d.markups(include_children=True)) == 1


def test_failed_operation_restores_dirty_and_redo_state(pdf):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(1, 1, 9, 9)); d.undo()
    d.modified = False
    snapshot = (len(d._undo), len(d._redo))
    with pytest.raises(ValueError):
        d.add_form_field(0, fitz.Rect(1, 1, 50, 20), "", "text")
    assert not d.modified and (len(d._undo), len(d._redo)) == snapshot and d.redo()
    d2 = Document(pdf)
    for i in range(Document.MAX_UNDO):
        d2.add_rect(0, fitz.Rect(1, 1, 5 + i, 5))
    full = list(d2._undo)
    with pytest.raises(IndexError):
        d2.delete_pages([99])
    assert d2._undo == full  # a failure at the undo cap must not evict the oldest step


def test_fill_area_accuracy_within_half_percent(tmp_path):
    pytest.importorskip("scipy")
    d = fitz.open(); p = d.new_page(width=600, height=800)
    p.draw_rect(fitz.Rect(100, 100, 200, 200), width=1)   # 99 x 99 interior
    p.draw_rect(fitz.Rect(130, 130, 150, 150), width=1, fill=(0, 0, 0))   # 21 x 21 column incl. stroke
    path = str(tmp_path / "acc.pdf"); d.save(path)
    doc = Document(path)
    doc.set_scale(Scale("m", 1.0))
    _, v = doc.add_fill_area(0, (110, 110))
    assert v == pytest.approx(99 * 99 - 21 * 21, rel=0.005)


# ---------- viewports & sheet manager ----------
def test_viewport_scales(pdf, tmp_path):
    d = Document(pdf)
    d.set_scale(Scale("ft", 1.0))                       # page: 1 pt = 1 ft
    d.add_viewport(0, fitz.Rect(300, 300, 500, 500), Scale("m", 0.1))   # detail: 1 pt = 0.1 m
    d.add_viewport(0, fitz.Rect(350, 350, 400, 400), Scale("in", 2.0))  # nested: last wins
    assert d.scale_at(0, [(10, 10)]).unit == "ft"
    assert d.scale_at(0, [(320, 320), (330, 330)]).unit == "m"
    assert d.scale_at(0, [(370, 370)]).unit == "in"
    assert d.scale_at(1, [(370, 370)]).unit == "ft"       # other page unaffected
    _, a = d.add_length(0, [(10, 10), (20, 10)]); _, b = d.add_length(0, [(310, 310), (320, 310)])
    assert a == pytest.approx(10) and b == pytest.approx(1)
    assert {m.measurement()[2] for m in d.markups()} == {"ft", "m"}
    out = str(tmp_path / "v.pdf"); d.save(out)
    r = Document(out)
    assert len(r.viewports) == 2 and r.scale_at(0, [(320, 320)]).unit == "m"
    with pytest.raises(ValueError):
        d.add_viewport(0, fitz.Rect(5, 5, 5, 5), Scale())
    with pytest.raises(IndexError):
        d.add_viewport(9, fitz.Rect(0, 0, 5, 5), Scale())
    assert len(d.viewports) == 2  # failed adds changed nothing


def test_viewport_undo_and_remove(pdf):
    d = Document(pdf)
    d.add_viewport(0, fitz.Rect(0, 0, 100, 100), Scale("m", 0.5))
    assert len(d.viewports) == 1
    d.undo()
    assert d.viewports == []
    d.redo(); assert len(d.viewports) == 1
    d.remove_viewport(0); assert d.viewports == []
    d.undo(); assert len(d.viewports) == 1


def sheets_pdf(path):
    d = fitz.open()
    for i, n in enumerate(["A-101", "A-102", "S-201"]):
        p = d.new_page(width=612, height=792)
        p.insert_text((450, 740), n, fontsize=28)                         # title block number (largest text)
        p.insert_text((72, 100), f"Plan. See {n if i == 0 else 'A-101'} and S-201, not A-1011.", fontsize=10)
    d.save(path); return str(path)


def test_sheet_index_bookmarks_and_links(tmp_path):
    d = Document(sheets_pdf(tmp_path / "set.pdf"))
    assert d.sheet_index() == [(0, "A-101"), (1, "A-102"), (2, "S-201")]
    assert d.sheet_index(corner=fitz.Rect(0, 0, 612, 200)) == [(0, None), (1, None), (2, None)]
    assert d.auto_bookmarks() == 3
    assert [t[1] for t in d.toc()] == ["A-101", "A-102", "S-201"] and [t[2] for t in d.toc()] == [1, 2, 3]
    n = d.auto_hyperlinks()
    assert n == 4  # p1: S-201 | p2: A-101, S-201 | p3: A-101  (A-1011 never matches; a sheet's own number is skipped)
    pages_linked = sorted((p, l["page"]) for p in range(3) for l in d.links(p))
    assert (0, 2) in pages_linked and (1, 0) in pages_linked and (2, 0) in pages_linked
    assert all(l["page"] != p for p in range(3) for l in d.links(p))


def test_cli_sheets(tmp_path):
    from openrevu.cli import main
    out = str(tmp_path / "o.pdf")
    assert main(["sheets", sheets_pdf(tmp_path / "set.pdf"), out]) == 0
    d = Document(out)
    assert len(d.toc()) == 3 and len(d.links(0)) == 1


def test_cli_run_script(pdf, tmp_path):
    from openrevu.cli import main
    script = tmp_path / "s.py"
    script.write_text("doc.add_count(0, (10, 10), group='Door')\ndoc.add_count(1, (20, 20), group='Door')\ndoc.watermark('SCRIPTED')\n")
    out = str(tmp_path / "o.pdf")
    assert main(["run", pdf, out, str(script)]) == 0
    r = Document(out)
    assert r.takeoff()[("count", "ea")] == 2 and "SCRIPTED" in r.page_text(0)
    (tmp_path / "bad.py").write_text("raise ValueError('boom')\n")
    assert main(["run", pdf, out, str(tmp_path / "bad.py")]) == 1
    (tmp_path / "syn.py").write_text("def (\n")
    assert main(["run", pdf, out, str(tmp_path / "syn.py")]) == 1


# ---------- architectural units ----------
@pytest.mark.parametrize("inches,expected", [
    (150.5, "12' 6 1/2\""), (144, "12'"), (6.5, "6 1/2\""), (0, "0\""), (0.0625, "1/16\""), (11.99, "1'"),
    (143.99, "12'"), (-30.25, "-2' 6 1/4\""), (12.03, "1'"), (13, "1' 1\""), (35.9, "2' 11 7/8\""),
])
def test_feet_inches_formatting(inches, expected):
    from openrevu.core import feet_inches
    assert feet_inches(inches) == expected


def test_feet_inches_fractions_and_errors():
    from openrevu.core import feet_inches
    assert feet_inches(6.3, 2) == "6 1/2\"" and feet_inches(6.3, 64) == "6 19/64\"" and feet_inches(6.01, 4) == "6\""
    with pytest.raises(ValueError):
        feet_inches(1, 10)
    assert format_value("length", 12.5, "ft", "feet-inches") == "12' 6\""
    assert format_value("length", 6.25, "in", "feet-inches") == "6 1/4\""
    assert format_value("area", 12.5, "ft", "feet-inches") == "12.50 ft²"        # only lengths change
    assert format_value("length", 12.5, "m", "feet-inches") == "12.50 m"          # metric is untouched
    assert format_value("angle", 90, "deg", "feet-inches") == "90.0°"


def test_unit_style_changes_labels_and_persists(pdf, tmp_path):
    d = Document(pdf)
    d.set_scale(Scale("ft", 1 / 12))                         # 1 pt = 1 inch
    d.add_length(0, [(0, 100), (150.5, 100)]); d.add_perimeter(0, [(0, 200), (24, 200), (24, 236)], label=True)
    d.add_area(0, [(0, 300), (12, 300), (12, 312)])
    labels = lambda: sorted(x.comment for x in d.markups(include_children=True) if x.subject == "Label")  # noqa: E731
    assert "12.54 ft" in labels()
    assert d.set_unit_style("feet-inches", 16) == 3          # length, perimeter, area labels were redrawn
    assert "12' 6 1/2\"" in labels() and any("ft²" in l for l in labels())      # the area keeps decimals
    assert d.fmt("length", 12.5, "ft") == "12' 6\""
    d.add_length(0, [(0, 400), (12, 400)])                   # new labels follow the style
    assert "1'" in labels()
    out = str(tmp_path / "s.pdf"); d.save(out)
    r = Document(out)
    assert r.unit_style == "feet-inches" and r.fraction == 16 and r.fmt("length", 3, "ft") == "3'"
    d.undo()
    assert d.unit_style == "feet-inches"                      # undo of the last length keeps the style
    d.set_unit_style("decimal")
    assert "12.54 ft" in labels()
    with pytest.raises(ValueError):
        d.set_unit_style("roman")
    with pytest.raises(ValueError):
        d.set_unit_style("feet-inches", 10)
    assert d.unit_style == "decimal"                          # a rejected change leaves the style alone


def test_unit_style_survives_undo_of_the_style_change(pdf):
    d = Document(pdf)
    d.set_unit_style("feet-inches", 8)
    assert (d.unit_style, d.fraction) == ("feet-inches", 8)
    d.undo()
    assert (d.unit_style, d.fraction) == ("decimal", 16)
    d.redo()
    assert (d.unit_style, d.fraction) == ("feet-inches", 8)


# ---------- third-review regressions: measurements ----------
def test_display_style_never_changes_stored_values(pdf):
    d = Document(pdf)
    d.set_scale(Scale("ft", 0.1))                                           # page scale
    d.add_viewport(0, fitz.Rect(0, 0, 400, 400), Scale("ft", 1.0))           # a region with another scale
    tri = [(10, 10), (310, 10), (10, 190)]
    d.add_area(0, tri)
    before = [m.measurement() for m in d.markups()]
    d.set_unit_style("feet-inches"); d.set_unit_style("decimal")
    assert [m.measurement() for m in d.markups()] == before                  # the viewport scale still applies
    assert before[0][1] == pytest.approx(0.5 * 300 * 180)                    # 1 ft/pt inside the viewport


def test_scale_change_then_style_change_keeps_value_and_unit_together(pdf):
    d = Document(pdf)
    d.set_scale(Scale("ft", 0.1)); d.add_length(0, [(0, 100), (200, 100)])
    d.set_scale(Scale("m", 0.01))                                           # recalibrated later
    d.set_unit_style("feet-inches")
    kind, val, unit = d.markups()[0].measurement()
    assert (kind, round(val, 6), unit) == ("length", 20.0, "ft")             # still the value that was measured, in its own unit
    m = d.markups()[0]; r = m.rect
    m.resize(fitz.Rect(r.x0, r.y0, r.x0 + (r.width - 3) * 2 + 3, r.y1))      # a resize measures again, with the scale in force now
    kind, val, unit = d.markups()[0].measurement()
    assert unit == "m" and val == pytest.approx(4.0, rel=0.03)               # and says so


def test_labels_keep_the_colour_of_their_markup(pdf):
    d = Document(pdf)
    d.set_scale(Scale("m", 0.1)); d.add_area(0, [(0, 0), (100, 0), (100, 100), (0, 100)], color=(0.1, 0.6, 0.2))
    label = lambda: [x for x in d.markups(include_children=True) if x.subject == "Label"]    # noqa: E731
    colour = lambda: d.doc.xref_get_key(label()[0].xref, "DA")[1].split(" rg")[0]            # the text colour is in /DA  # noqa: E731
    assert colour() == "0.1 0.6 0.2"
    d.set_unit_style("feet-inches"); d.set_unit_style("decimal")
    assert len(label()) == 1 and colour() == "0.1 0.6 0.2"
    d.markups()[0].resize(fitz.Rect(0, 0, 150, 150))
    assert len(label()) == 1 and colour() == "0.1 0.6 0.2"


# ---------- third-review regressions: protected documents stay protected ----------
def test_everything_derived_from_a_protected_document_is_protected(pdf, tmp_path):
    from tests.test_sheets import SET_V1, make_set
    d0 = Document(pdf); enc = str(tmp_path / "enc.pdf"); d0.save_encrypted(enc, "pw")
    d = Document(enc, "pw")
    d.add_rect(0, fitz.Rect(10, 10, 60, 60)); d.markups()[0].set_comment("secret note")
    outs = {"optimize": str(tmp_path / "opt.pdf"), "extract": str(tmp_path / "ex.pdf"), "report": str(tmp_path / "rep.pdf")}
    d.optimize(outs["optimize"]); d.extract_pages([0, 1], outs["extract"]); d.export_markup_summary(outs["report"])
    parts = d.split(str(tmp_path / "sp"), every=1)
    for path in list(outs.values()) + parts:
        f = fitz.open(path)
        assert f.needs_pass, path                                           # not readable without the password
        assert f.authenticate("pw") and f.page_count >= 1
    other = make_set(tmp_path / "new.pdf", SET_V1)
    from openrevu.compare import compare_documents
    compare_documents(enc, other, str(tmp_path / "cmp.pdf"), old_password="pw")
    assert fitz.open(str(tmp_path / "cmp.pdf")).needs_pass
    # an ordinary document is still written in plain
    plain = Document(pdf); plain.optimize(str(tmp_path / "p1.pdf")); plain.extract_pages([0], str(tmp_path / "p2.pdf"))
    assert not fitz.open(str(tmp_path / "p1.pdf")).needs_pass and not fitz.open(str(tmp_path / "p2.pdf")).needs_pass


def test_pdfa_of_a_protected_document_says_it_is_not_protected(pdf, tmp_path):
    from openrevu import pdfa
    if not pdfa.available():
        pytest.skip("Ghostscript not installed")
    enc = str(tmp_path / "enc.pdf"); Document(pdf).save_encrypted(enc, "pw")
    rep = Document(enc, "pw").export_pdfa(str(tmp_path / "a.pdf"))
    assert any("not password protected" in w for w in rep.warnings) and not fitz.open(str(tmp_path / "a.pdf")).needs_pass


# ---------- third-review regressions: extract and split keep the document settings ----------
def test_extract_and_split_keep_scales_viewports_columns_and_style(pdf, tmp_path):
    d = Document(pdf)
    d.set_scale(Scale("ft", 0.2)); d.set_scale(Scale("m", 0.5), page=2); d.set_scale(Scale("in", 3.0), page=0)
    d.add_viewport(2, fitz.Rect(0, 0, 100, 100), Scale("mm", 9.0)); d.add_viewport(0, fitz.Rect(5, 5, 50, 50), Scale("cm", 4.0))
    d.add_column("Cost", "number"); d.set_unit_style("feet-inches", 8)
    d.add_rect(2, fitz.Rect(10, 10, 60, 60)); d.markups()[0].set_custom("Cost", 7.5)
    out = d.extract_pages([2, 1], str(tmp_path / "e.pdf"))
    e = Document(out)
    assert e.scale_for(0).unit == "m" and e.scale_for(0).unit_per_pt == 0.5          # old page 2 is now page 0
    assert e.scale_for(1).unit_per_pt == 0.2                                         # old page 1 had no own scale: the default
    assert [(p, sc.unit) for p, _r, sc in e.viewports] == [(0, "mm")]                # the page-0 viewport was left behind
    assert e.default_scale.unit_per_pt == 0.2 and e.unit_style == "feet-inches" and e.fraction == 8
    assert [c["name"] for c in e.columns] == ["Cost"] and e.markups()[0].custom() == {"Cost": 7.5}   # the value is not orphaned
    parts = d.split(str(tmp_path / "sp"), every=1)
    firsts = [Document(p) for p in parts]
    assert [x.scale_for(0).unit for x in firsts] == ["in", "ft", "m"]                # each part knows its own page scale
    assert [len(x.viewports) for x in firsts] == [1, 0, 1] and firsts[0].viewports[0][2].unit == "cm"
    # the source document was not changed by writing the extract
    assert d.scale_for(2).unit == "m" and len(d.viewports) == 2 and len(d._undo) >= 1
