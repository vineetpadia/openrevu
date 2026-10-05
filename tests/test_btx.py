import zlib

import fitz
import pytest

from openrevu import btx
from openrevu.btx import decode_raw, parse_annotation, read_btx, tool_from_annotation
from openrevu.core import Document
from openrevu.toolchest import ToolChest


def raw(pdf_text: str, compress=True) -> str:
    data = pdf_text.encode("latin-1")
    return (zlib.compress(data) if compress else data).hex()


def item(name, pdf_text, typ="Bluebeam.PDF.Annotations.Test", compress=True):
    return (f"<ToolChestItem Version=\"1\"><Name>{name}</Name><Type>{typ}</Type><Raw>{raw(pdf_text, compress)}</Raw>"
            "<X>0</X><Y>0</Y><Index>0</Index><Mode>0</Mode></ToolChestItem>")


def write(tmp_path, items, title="My set", name="set.btx"):
    p = tmp_path / name
    p.write_text(f'<?xml version="1.0" encoding="utf-8"?><BluebeamRevuToolSet Version="1"><Title>{title}</Title>'
                 + "".join(items) + "</BluebeamRevuToolSet>", encoding="utf-8")
    return str(p)


RECT = "<</Subtype/Square/C[1 0 0]/IC[1 1 0]/CA 0.5/BS<</W 3/S/S>>/Subj(Door tag)/Contents(Check \\(this\\))/Rect[10 10 90 50]>>"
ARROW = "<</Subtype/Line/L[0 0 100 50]/LE[/None/OpenArrow]/C[0 0 1]/BS<</W 2>>/Subj(Arrow)>>"
CLOUD = "<</Subtype/Polygon/IT/PolygonCloud/Vertices[0 0 100 0 100 60 0 60]/C[1 0 0]/BS<</W 1.5>>>>"
CALLOUT = "<</Subtype/FreeText/IT/FreeTextCallout/DA(0 0.5 0 rg /Helv 10 Tf)/Contents(See detail)/Rect[0 0 150 40]>>"
PEN = "<</Subtype/Ink/InkList[[0 0 10 10 20 0]]/C[0 0 0]/BS<</W 4>>>>"


def test_parser_reads_pdf_syntax_including_escapes_unicode_and_nesting():
    d = parse_annotation(b"junk <</A 1/B -2.5/C(a\\(b\\)\\n\\101)/D<4142>/E[1 2 [3 4] <</K/V>>]/F true/G null/H 12 0 R/I/N#20m>> tail")
    assert d["A"] == 1 and d["B"] == -2.5 and d["C"] == "a(b)\nA" and d["D"] == "AB"
    assert d["E"] == [1, 2, [3, 4], {"K": "V"}] and d["F"] is True and d["G"] is None and d["H"] is None and d["I"] == "N m"
    assert parse_annotation(b"<</S(\xfe\xff\x00\xe9\x00t\x00\xe9)>>")["S"] == "été"            # UTF-16 text string
    assert parse_annotation("<</S(Café)>>".encode("utf-8"))["S"] == "Café"
    assert parse_annotation(b"<</S(a % not a comment)>> % real comment\n")["S"] == "a % not a comment"
    for bad in (b"no dictionary here", b"<</A 1", b"<</A (open", b"<<1 2>>", b""):
        with pytest.raises(ValueError):
            parse_annotation(bad)


def test_decode_raw_forms():
    assert decode_raw(raw("<</A 1>>")) == b"<</A 1>>"
    spaced = " ".join(raw("<</A 1>>")[i:i + 4] for i in range(0, len(raw("<</A 1>>")), 4))
    assert decode_raw(spaced + "\n") == b"<</A 1>>"                       # whitespace in the hex is fine
    assert decode_raw(raw("<</A 1>>", compress=False)) == b"<</A 1>>"       # hex of uncompressed data
    assert decode_raw("<</A 1>>") == b"<</A 1>>"                           # plain text
    with pytest.raises(ValueError, match="no data"):
        decode_raw("  ")
    with pytest.raises(ValueError, match="damaged"):
        decode_raw("789c" + "deadbeef")


def test_style_mapping_for_every_supported_kind():
    r = tool_from_annotation(parse_annotation(RECT.encode()))
    assert r["kind"] == "Square" and r["stroke"] == (1.0, 0.0, 0.0) and r["fill"] == (1.0, 1.0, 0.0)
    assert r["opacity"] == 0.5 and r["width"] == 3.0 and r["subject"] == "Door tag" and r["comment"] == "Check (this)"
    assert r["rect"] == [0.0, 0.0, 80.0, 40.0]
    a = tool_from_annotation(parse_annotation(ARROW.encode()))
    assert a["kind"] == "Line" and a["arrow"] is True and a["vertices"] == [[0.0, 50.0], [100.0, 0.0]]   # y flipped to y-down
    assert tool_from_annotation(parse_annotation(b"<</Subtype/Line/L[0 0 5 5]/LE[/None/None]>>"))["arrow"] is False
    c = tool_from_annotation(parse_annotation(CLOUD.encode()))
    assert c["kind"] == "Polygon" and c["subject"] == "Cloud" and len(c["vertices"]) == 4 and c["rect"] == [0.0, 0.0, 100.0, 60.0]
    t = tool_from_annotation(parse_annotation(CALLOUT.encode()))
    assert t["kind"] == "FreeText" and t["stroke"] == (0.0, 0.5, 0.0) and t["subject"] == "Callout" and t["comment"] == "See detail"
    p = tool_from_annotation(parse_annotation(PEN.encode()))
    assert p["kind"] == "Ink" and p["width"] == 4.0 and p["vertices"] == [[[0.0, 10.0], [10.0, 0.0], [20.0, 10.0]]]
    g = tool_from_annotation(parse_annotation(b"<</Subtype/Circle/C[0.5]/Rect[0 0 5 5]>>"))
    assert g["stroke"] == (0.5, 0.5, 0.5) and g["rect"][2:] == [10.0, 10.0]      # grey colour; a tiny box is enlarged
    assert tool_from_annotation(parse_annotation(b"<</Subtype/Square>>"))["rect"] == [0.0, 0.0, 120.0, 60.0]   # default size
    assert tool_from_annotation(parse_annotation(b"<</Subtype/Square/Border[0 0 2.5]>>"))["width"] == 2.5


@pytest.mark.parametrize("pdf_text,reason", [
    ("<</Subtype/Stamp/Subj(Approved)>>", "Stamp type cannot be a tool"),
    ("<</Subtype/Highlight/QuadPoints[0 0 1 1 0 0 1 1]>>", "Highlight type cannot be a tool"),
    ("<</C[1 0 0]>>", "unknown type cannot be a tool"),
    ("<</Subtype/Polygon/Vertices[0 0 1]>>", "no usable points"),
    ("<</Subtype/Ink/InkList[[1 2]]>>", "no points"),
])
def test_unsupported_or_incomplete_items_give_a_reason(pdf_text, reason):
    with pytest.raises(ValueError, match=reason):
        tool_from_annotation(parse_annotation(pdf_text.encode()))


def test_read_btx_returns_tools_and_reasons_without_stopping(tmp_path):
    p = write(tmp_path, [item("Rect", RECT), item("Stamp", "<</Subtype/Stamp>>"), item("Broken", "<</Subtype/Square", compress=True),
                         '<ToolChestItem Version="1"><Name>NoRaw</Name></ToolChestItem>', item("Arrow", ARROW),
                         '<ToolChestItem><Raw>zz</Raw></ToolChestItem>'])
    title, tools, skipped = read_btx(p)
    assert title == "My set" and [n for n, _ in tools] == ["Rect", "Arrow"]
    reasons = dict(skipped)
    assert "Stamp type" in reasons["Stamp"] and "damaged annotation" in reasons["Broken"] and "no <Raw>" in reasons["NoRaw"]
    assert "Tool 6" in reasons                                                  # an unnamed item still gets listed


def test_read_btx_errors(tmp_path):
    bad = tmp_path / "bad.btx"; bad.write_text("this is not xml")
    with pytest.raises(ValueError, match="not a readable tool set"):
        read_btx(str(bad))
    empty = tmp_path / "empty.btx"; empty.write_text("<BluebeamRevuToolSet><Title>x</Title></BluebeamRevuToolSet>")
    with pytest.raises(ValueError, match="no tools were found"):
        read_btx(str(empty))
    ns = open(write(tmp_path, [item("R", RECT)]), encoding="utf-8").read().replace("<BluebeamRevuToolSet ", '<BluebeamRevuToolSet xmlns="urn:x" ')
    (tmp_path / "ns.btx").write_text(ns, encoding="utf-8")
    assert [n for n, _ in read_btx(str(tmp_path / "ns.btx"))[1]] == ["R"]       # an XML namespace is fine


def test_import_into_tool_chest_and_place_every_tool(tmp_path):
    p = write(tmp_path, [item("Rect", RECT), item("Arrow", ARROW), item("Cloud", CLOUD), item("Callout", CALLOUT), item("Pen", PEN),
                         item("Note", "<</Subtype/Text/Contents(Remember)>>"), item("Stamp", "<</Subtype/Stamp>>")])
    chest = ToolChest(str(tmp_path / "tc.json"))
    res = chest.import_btx(p)
    assert res.imported == ["Rect", "Arrow", "Cloud", "Callout", "Pen", "Note"] and [n for n, _ in res.skipped] == ["Stamp"]
    assert res.summary() == "6 tool(s) imported, 1 skipped." and ToolChest(str(tmp_path / "tc.json")).items.keys() == chest.items.keys()
    d = fitz.open(); d.new_page(); d.save(str(tmp_path / "t.pdf")); doc = Document(str(tmp_path / "t.pdf"))
    for i, name in enumerate(res.imported):
        chest.place(doc, name, 0, (50, 50 + i * 90))
    by = {m.kind: m for m in doc.markups()}
    assert set(by) == {"Square", "Line", "Polygon", "FreeText", "Ink", "Text"}
    sq = by["Square"]
    assert tuple(sq.annot.colors["stroke"]) == (1.0, 0.0, 0.0) and tuple(sq.annot.colors["fill"]) == (1.0, 1.0, 0.0)
    assert sq.annot.border["width"] == 3.0 and sq.annot.opacity == pytest.approx(0.5) and sq.subject == "Door tag"
    assert sq.rect.width == pytest.approx(80, abs=3) and sq.rect.x0 == pytest.approx(50, abs=3)
    assert by["Line"].to_dict()["arrow"] is True and by["FreeText"].comment == "See detail"
    doc.render(0, 1.0)


def test_importing_twice_keeps_both_and_a_failed_import_changes_nothing(tmp_path):
    p = write(tmp_path, [item("Rect", RECT)])
    chest = ToolChest(str(tmp_path / "tc.json"))
    chest.import_btx(p); res = chest.import_btx(p)
    assert res.imported == ["Rect (2)"] and sorted(chest.items) == ["Rect", "Rect (2)"]
    bad = tmp_path / "bad.btx"; bad.write_text("nope")
    with pytest.raises(ValueError):
        chest.import_btx(str(bad))
    assert sorted(chest.items) == ["Rect", "Rect (2)"]
    only_bad = write(tmp_path, [item("S", "<</Subtype/Stamp>>")], name="s.btx")
    r = chest.import_btx(only_bad)
    assert r.imported == [] and len(r.skipped) == 1 and sorted(chest.items) == ["Rect", "Rect (2)"]


# ---------- third-review regressions: hostile or damaged files ----------
def test_a_zlib_bomb_is_refused_without_using_the_memory(tmp_path):
    bomb = zlib.compress(b"A" * 200_000_000, 9).hex()                       # 200 MB of data in about 200 KB
    p = tmp_path / "bomb.btx"
    p.write_text(f"<BluebeamRevuToolSet><ToolChestItem><Name>Bomb</Name><Raw>{bomb}</Raw></ToolChestItem>"
                 + item("Ok", RECT) + "</BluebeamRevuToolSet>")
    import tracemalloc
    tracemalloc.start()
    _title, tools, skipped = read_btx(str(p))
    peak = tracemalloc.get_traced_memory()[1]; tracemalloc.stop()
    assert [n for n, _ in tools] == ["Ok"] and "far larger" in dict(skipped)["Bomb"]
    assert peak < 60_000_000                                                  # nothing like 200 MB was built
    with pytest.raises(ValueError, match="too large"):
        decode_raw("00" * (btx.MAX_DATA + 1))


def test_deep_nesting_is_one_skipped_item_not_a_crash(tmp_path):
    deep = "<</Subtype/Square/X" + "[" * 5000 + "]" * 5000 + ">>"
    p = write(tmp_path, [item("Deep", deep), item("Ok", RECT), item("DeepDict", "<</Subtype/Square" + "/K<<" * 3000 + ">>" * 3000 + ">>")])
    _t, tools, skipped = read_btx(p)
    assert [n for n, _ in tools] == ["Ok"] and len(skipped) == 2 and all("nested too deeply" in why for _n, why in skipped)
    assert parse_annotation(b"<</A [[[[1]]]]>>")["A"] == [[[[1]]]]            # ordinary nesting is fine


@pytest.mark.parametrize("pdf_text", [
    "<</Subtype/Square/Rect[0 0 1e999 5]>>", "<</Subtype/Square/Rect[0 0 5000000 5]>>",
    "<</Subtype/Line/L[0 0 1e9 5]>>", "<</Subtype/Polygon/Vertices[0 0 5 5 -9e9 0]>>", "<</Subtype/Ink/InkList[[0 0 1e300 5]]>>",
])
def test_absurd_coordinates_are_refused(pdf_text, tmp_path):
    _t, tools, skipped = read_btx(write(tmp_path, [item("X", pdf_text)]))
    assert tools == [] and "range" in skipped[0][1]


def test_xml_entity_bomb_is_rejected(tmp_path):
    p = tmp_path / "xxe.btx"
    p.write_text('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
                 '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;"><!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">'
                 '<!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;"><!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;"><!ENTITY g "&f;&f;&f;&f;&f;&f;&f;&f;&f;&f;"><!ENTITY h "&g;&g;&g;&g;&g;&g;&g;&g;&g;&g;">]>'
                 '<BluebeamRevuToolSet><ToolChestItem><Name>&h;</Name></ToolChestItem></BluebeamRevuToolSet>')
    try:
        _t, tools, skipped = read_btx(str(p))
        assert len(skipped[0][0]) < 10_000_000                                    # if it is read at all, it stays small
    except ValueError:
        pass                                                                      # or it is refused: both are fine
