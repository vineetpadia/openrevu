import fitz
import pytest

from openrevu import pdfa
from openrevu.core import Document

needs_gs = pytest.mark.skipif(not pdfa.available(), reason="Ghostscript not installed")
needs_vera = pytest.mark.skipif(pdfa.verapdf() is None, reason="veraPDF not installed")


@pytest.fixture
def doc(tmp_path):
    d = Document()
    p = d.doc.new_page()
    p.insert_text((72, 100), "PDF/A test é ü", fontsize=14)
    p.insert_text((72, 140), "Helvetica text", fontname="helv")
    d.add_rect(0, fitz.Rect(50, 200, 200, 260), color=(1, 0, 0), fill=(1, 1, 0), opacity=0.5)
    d.add_length(0, [(60, 300), (200, 300)])
    d.add_stamp(0, fitz.Rect(300, 50, 480, 100), "REVIEWED")
    return d


def test_plain_pdf_is_not_pdfa_and_report_says_so(tmp_path):
    if pdfa.verapdf() is None:
        pytest.skip("veraPDF not installed")
    d = fitz.open(); d.new_page().insert_text((72, 100), "hi"); path = str(tmp_path / "plain.pdf"); d.save(path)
    rep = pdfa.validate(path, "2b")
    assert rep.validated and rep.compliant is False and rep.failed_rules > 0 and rep.messages
    assert "NOT compliant" in rep.summary()


@needs_gs
@needs_vera
@pytest.mark.parametrize("level", ["1b", "2b", "3b"])
def test_export_is_checked_compliant_by_verapdf(doc, tmp_path, level):
    out = str(tmp_path / f"a{level}.pdf")
    rep = doc.export_pdfa(out, level)
    assert rep.validated and rep.compliant, rep.messages
    assert f"compliant with PDF/A-{level}" in rep.summary()
    # the output is a normal PDF with the same text and the markups baked in
    r = fitz.open(out)
    assert "PDF/A test" in r[0].get_text() and r.page_count == 1
    assert list(r[0].annots() or []) == []                      # flattened
    px = r[0].get_pixmap(dpi=50)
    assert any(px.pixel(x, y) != (255, 255, 255) for x in range(0, px.width, 4) for y in range(px.height))


@needs_gs
@needs_vera
def test_export_without_flatten_still_valid_or_clearly_reported(doc, tmp_path):
    rep = doc.export_pdfa(str(tmp_path / "keep.pdf"), "2b", flatten=False)
    assert rep.validated                       # either way veraPDF gives a verdict; we never claim more than it says
    assert rep.compliant == (rep.failed_rules == 0)


@needs_gs
def test_export_without_verapdf_says_not_validated(doc, tmp_path, monkeypatch):
    monkeypatch.setattr(pdfa, "verapdf", lambda: None)
    rep = doc.export_pdfa(str(tmp_path / "x.pdf"))
    assert not rep.validated and rep.compliant is None and "Not validated" in rep.summary()


def test_errors(doc, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="unknown PDF/A level"):
        pdfa.convert("a.pdf", str(tmp_path / "b.pdf"), "9z")
    with pytest.raises(ValueError, match="unknown PDF/A level"):
        pdfa.validate("x.pdf", "9z")
    with pytest.raises(ValueError, match="must differ"):
        monkeypatch.setattr(pdfa, "ghostscript", lambda: "gs")
        pdfa.convert(str(tmp_path / "a.pdf"), str(tmp_path / "a.pdf"))
    monkeypatch.setattr(pdfa, "ghostscript", lambda: None)
    with pytest.raises(RuntimeError, match="Ghostscript"):
        doc.export_pdfa(str(tmp_path / "x.pdf"))
    monkeypatch.setattr(pdfa, "verapdf", lambda: None)
    with pytest.raises(RuntimeError, match="veraPDF"):
        pdfa.validate("x.pdf")


@needs_gs
def test_ghostscript_failure_is_reported_not_swallowed(tmp_path):
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"this is not a pdf")
    with pytest.raises(RuntimeError, match="Ghostscript failed"):
        pdfa.convert(str(bad), str(tmp_path / "o.pdf"), check=False)


@needs_gs
def test_export_of_a_document_opened_with_a_password(doc, tmp_path):
    enc = str(tmp_path / "e.pdf"); doc.save_encrypted(enc, "pw")
    out = str(tmp_path / "o.pdf")
    rep = Document(enc, "pw").export_pdfa(out)
    assert not fitz.open(out).needs_pass            # an archive copy is not password protected
    assert "PDF/A test" in fitz.open(out)[0].get_text() or rep.warnings


@needs_gs
def test_cli_pdfa(doc, tmp_path):
    from openrevu.cli import main
    src = str(tmp_path / "s.pdf"); doc.save(src)
    assert main(["pdfa", src, str(tmp_path / "o.pdf"), "--level", "2b"]) == 0
    assert main(["pdfa", str(tmp_path / "missing.pdf"), str(tmp_path / "o2.pdf")]) == 1


@needs_gs
@needs_vera
def test_level_1b_keeps_text_when_markups_are_transparent(doc, tmp_path):
    rep = doc.export_pdfa(str(tmp_path / "a1b.pdf"), "1b")
    assert rep.compliant and rep.warnings == []
    assert "PDF/A test" in fitz.open(str(tmp_path / "a1b.pdf"))[0].get_text()


@needs_gs
@needs_vera
def test_a_page_that_still_gets_rasterised_is_reported(tmp_path):
    d = Document(); p = d.doc.new_page(); p.insert_text((72, 100), "Keep me", fontsize=14)
    d.watermark("DRAFT", opacity=0.3)       # real page-level transparency: not fixable by blending annotations
    rep = d.export_pdfa(str(tmp_path / "w.pdf"), "1b")
    out = fitz.open(str(tmp_path / "w.pdf"))[0].get_text()
    if "Keep me" not in out:                # whenever text is lost, the user is told
        assert any("lost its selectable text" in w for w in rep.warnings) and "Warning:" in rep.summary()
    ok2b = d.export_pdfa(str(tmp_path / "w2.pdf"), "2b")
    assert ok2b.compliant and "Keep me" in fitz.open(str(tmp_path / "w2.pdf"))[0].get_text()


@needs_gs
def test_dropped_links_and_turned_pages_are_reported(tmp_path):
    d = Document(); p = d.doc.new_page(width=612, height=792); p.insert_text((72, 100), "Hello", fontsize=14)
    d.doc.new_page(width=612, height=792); d.doc[1].set_rotation(90); d.doc[1].insert_text((72, 100), "Rotated", fontsize=14)
    d.add_link_uri(0, fitz.Rect(72, 90, 120, 110), "https://example.org")
    d.add_link_goto(0, fitz.Rect(72, 200, 120, 220), 1)
    rep = d.export_pdfa(str(tmp_path / "a.pdf"), "2b")
    out = fitz.open(str(tmp_path / "a.pdf"))
    kept = sum(len(pg.get_links()) for pg in out)
    if kept < 2:
        assert any("link(s) were removed" in w for w in rep.warnings)          # never silent
    assert "Warning:" in rep.summary() or kept == 2 or not rep.warnings
    if abs(out[1].rect.width - d.doc[1].rect.width) > 1:
        assert any("page 2 was turned" in w for w in rep.warnings)


# ---------- the colour profile and Ghostscript's safe mode ----------
def test_the_generated_srgb_profile_is_a_well_formed_icc_profile():
    import struct
    from openrevu.srgb import srgb_profile
    p = srgb_profile()
    assert int.from_bytes(p[:4], "big") == len(p) and len(p) % 4 == 0
    assert p[12:16] == b"mntr" and p[16:20] == b"RGB " and p[20:24] == b"XYZ " and p[36:40] == b"acsp"
    assert p[8:12] == bytes([2, 0x10, 0, 0])                                    # version 2.1
    n = struct.unpack(">I", p[128:132])[0]
    tags = {p[132 + 12 * i:136 + 12 * i]: struct.unpack(">II", p[136 + 12 * i:144 + 12 * i]) for i in range(n)}
    assert set(tags) == {b"desc", b"cprt", b"wtpt", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC"}
    for sig, (off, size) in tags.items():
        assert off % 4 == 0 and off + size <= len(p), sig                         # every tag lies inside the file, aligned
    off, size = tags[b"rTRC"]
    count = struct.unpack(">I", p[off + 8:off + 12])[0]
    vals = struct.unpack(f">{count}H", p[off + 12:off + 12 + 2 * count])
    assert vals[0] == 0 and vals[-1] == 65535 and all(a <= b for a, b in zip(vals, vals[1:]))   # a rising curve from 0 to 1
    assert tags[b"gTRC"] == tags[b"rTRC"] == tags[b"bTRC"]                      # the three curves share one block
    x = struct.unpack(">i", p[tags[b"wtpt"][0] + 8:tags[b"wtpt"][0] + 12])[0] / 65536
    assert abs(x - 0.9642) < 1e-3                                                # the D50 white point


@needs_gs
@needs_vera
def test_our_profile_is_embedded_and_conversion_needs_no_file_access(doc, tmp_path):
    out = str(tmp_path / "a.pdf")
    rep = doc.export_pdfa(out, "2b")
    assert rep.compliant
    f = fitz.open(out)
    streams = b"".join(f.xref_stream(x) or b"" for x in range(1, f.xref_length()) if f.xref_is_stream(x))
    assert b"sRGB (OpenRevu)" in streams                                            # the profile of this program, not Ghostscript's
    assert "OutputIntents" in f.xref_object(f.pdf_catalog())


@needs_gs
def test_the_postscript_never_reads_a_file():
    from openrevu.pdfa import _def_ps
    ps = _def_ps()
    assert "(r) file" not in ps and "%rom%" not in ps and ps.startswith("%!")        # safe mode would refuse a file read
    assert all(ord(c) < 128 for c in ps)


@needs_gs
def test_a_ghostscript_failure_shows_the_cause(tmp_path):
    with pytest.raises(RuntimeError) as e:
        pdfa.convert(str(tmp_path / "missing.pdf"), str(tmp_path / "o.pdf"), check=False)
    assert "exit code" in str(e.value) and "missing.pdf" in str(e.value)       # the cause is in the message, not just "failed"
    assert not (tmp_path / "o.pdf").exists()                                    # and no partial output is left behind
