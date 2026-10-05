import math
import os

import fitz
import pytest

from openrevu.core import STATUSES, Document, Scale, angle_deg
from openrevu.pages import compare_pdfs
from openrevu.toolchest import ToolChest


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


def test_move_resize_all_geometry_types(pdf):
    d = Document(pdf)
    d.add_rect(0, fitz.Rect(50, 50, 150, 100))
    d.add_line(0, (10, 10), (60, 60))
    d.add_freehand(0, [(10, 10), (30, 40), (50, 10)])
    d.add_polygon(0, [(0, 0), (40, 0), (40, 40)])
    d.add_cloud(0, fitz.Rect(200, 200, 300, 260))
    d.add_highlight(0, fitz.Rect(60, 85, 200, 105))
    for m in d.markups():
        before = fitz.Rect(m.rect)
        if m.kind == "Highlight":
            with pytest.raises(ValueError):
                m.move(10, 20)
            continue
        m.move(10, 20)
        after = d.markups()[[x.xref for x in d.markups()].index(m.xref)].rect
        assert after.x0 == pytest.approx(before.x0 + 10, abs=1.5) and after.y0 == pytest.approx(before.y0 + 20, abs=1.5), m.kind
    r = [m for m in d.markups() if m.kind == "Square"][0]
    r.resize(fitz.Rect(0, 0, 40, 20))
    nr = [m for m in d.markups() if m.kind == "Square"][0].rect
    assert nr.width == pytest.approx(40, abs=2.5) and nr.height == pytest.approx(20, abs=2.5)


def test_move_on_rotated_page(pdf):
    d = Document(pdf)
    d.rotate_pages([0], 90)
    a = d.add_line(0, (10, 10), (60, 40))
    m = d.markups()[0]
    m.move(30, 5)
    v = d.markups()[0].annot.vertices
    assert v[0] == pytest.approx((40, 15), abs=1) and v[1] == pytest.approx((90, 45), abs=1)


def test_measurement_label_moves_and_deletes_with_parent(pdf):
    d = Document(pdf)
    d.add_length(0, [(0, 100), (100, 100)])
    assert len(d.markups()) == 1 and len(d.markups(include_children=True)) == 2
    m = d.markups()[0]
    lab_before = [x for x in d.markups(include_children=True) if x.subject == "Label"][0].rect
    m.move(10, 10)
    lab_after = [x for x in d.markups(include_children=True) if x.subject == "Label"][0].rect
    assert lab_after.x0 == pytest.approx(lab_before.x0 + 10, abs=1)
    with pytest.raises(ValueError):
        d.markups()[0].resize(fitz.Rect(0, 0, 5, 5))
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
