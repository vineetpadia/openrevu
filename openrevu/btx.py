"""Import Bluebeam tool sets (.btx) into the Tool Chest.

What is known about the format comes from the Bluebeam community, not from Bluebeam: a .btx file is XML.
Each <ToolChestItem> has a <Name>, a <Type>, and a <Raw> field. <Raw> holds hexadecimal text of zlib-compressed
data (it starts with "789c"). That data is a PDF annotation dictionary with the style of the tool.

This importer was written from that description and tested with files built to match it. It has not been
checked against files from every Bluebeam version. Items that it cannot read are listed in the result, never
silently dropped, and a damaged file never stops the import of the other items."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field

DEFAULT_SIZE = (120.0, 60.0)
_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"


@dataclass
class ImportResult:
    imported: list = field(default_factory=list)   # names of tools added to the Tool Chest
    skipped: list = field(default_factory=list)    # (name, reason)
    title: str = ""

    def summary(self) -> str:
        s = f"{len(self.imported)} tool(s) imported"
        if self.skipped:
            s += f", {len(self.skipped)} skipped"
        return s + "."


class _Reader:
    """A small reader for PDF object syntax: dictionaries, arrays, names, numbers, strings."""

    def __init__(self, data: bytes):
        self.d, self.i = data, 0

    def _skip(self):
        d = self.d
        while self.i < len(d):
            if d[self.i] in _WS:
                self.i += 1
            elif d[self.i:self.i + 1] == b"%":
                while self.i < len(d) and d[self.i] not in b"\r\n":
                    self.i += 1
            else:
                break

    def parse(self):
        self._skip()
        if self.i >= len(self.d):
            raise ValueError("unexpected end of data")
        c = self.d[self.i:self.i + 1]
        if self.d[self.i:self.i + 2] == b"<<":
            self.i += 2
            out: dict = {}
            while True:
                self._skip()
                if self.d[self.i:self.i + 2] == b">>":
                    self.i += 2
                    return out
                key = self.parse()
                if not isinstance(key, Name):
                    raise ValueError("a dictionary key must be a name")
                out[str(key)] = self.parse()
        if c == b"[":
            self.i += 1
            arr = []
            while True:
                self._skip()
                if self.d[self.i:self.i + 1] == b"]":
                    self.i += 1
                    return arr
                arr.append(self.parse())
        if c == b"/":
            self.i += 1
            j = self.i
            while j < len(self.d) and self.d[j] not in _WS and self.d[j] not in _DELIM:
                j += 1
            raw = self.d[self.i:j]
            self.i = j
            return Name(re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw).decode("latin-1"))
        if c == b"(":
            return self._string()
        if c == b"<":
            j = self.d.index(b">", self.i)
            hexs = re.sub(rb"\s", b"", self.d[self.i + 1:j])
            self.i = j + 1
            if len(hexs) % 2:
                hexs += b"0"
            return _decode_text(bytes.fromhex(hexs.decode("ascii")))
        j = self.i
        while j < len(self.d) and self.d[j] not in _WS and self.d[j] not in _DELIM:
            j += 1
        tok = self.d[self.i:j].decode("latin-1")
        if not tok:
            raise ValueError(f"unexpected character {c!r}")
        self.i = j
        if tok in ("true", "false"):
            return tok == "true"
        if tok == "null":
            return None
        try:
            num = float(tok) if "." in tok else int(tok)
        except ValueError:
            return Name(tok)  # an operator or unknown word: keep it as a name, never fail
        # an indirect reference looks like "12 0 R": skip it
        m = re.match(rb"\s+\d+\s+R\b", self.d[self.i:self.i + 20])
        if isinstance(num, int) and m:
            self.i += m.end()
            return None
        return num

    def _string(self):
        self.i += 1
        depth, buf = 1, bytearray()
        d = self.d
        while self.i < len(d):
            ch = d[self.i]
            self.i += 1
            if ch == 0x5C and self.i < len(d):          # backslash
                nxt = d[self.i]
                self.i += 1
                esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
                if nxt in esc:
                    buf.append(esc[nxt])
                elif 0x30 <= nxt <= 0x37:
                    octal = bytes([nxt])
                    while len(octal) < 3 and self.i < len(d) and 0x30 <= d[self.i] <= 0x37:
                        octal += bytes([d[self.i]]); self.i += 1
                    buf.append(int(octal, 8) & 0xFF)
                elif nxt not in b"\r\n":
                    buf.append(nxt)
            elif ch == 0x28:
                depth += 1; buf.append(ch)
            elif ch == 0x29:
                depth -= 1
                if depth == 0:
                    return _decode_text(bytes(buf))
                buf.append(ch)
            else:
                buf.append(ch)
        raise ValueError("a string is not closed")


class Name(str):
    """A PDF name such as /Square."""


def _decode_text(b: bytes) -> str:
    if b[:2] == b"\xfe\xff":
        return b[2:].decode("utf-16-be", "replace")
    if b[:2] == b"\xff\xfe":
        return b[2:].decode("utf-16-le", "replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def parse_annotation(data: bytes) -> dict:
    """Read the first dictionary in the data. Raises ValueError if there is none or it is damaged."""
    start = data.find(b"<<")
    if start < 0:
        raise ValueError("no annotation dictionary found")
    r = _Reader(data)
    r.i = start
    try:
        d = r.parse()
    except (ValueError, IndexError) as e:
        raise ValueError(f"damaged annotation data ({e})") from None
    if not isinstance(d, dict):
        raise ValueError("no annotation dictionary found")
    return d


def decode_raw(text: str) -> bytes:
    """<Raw> text -> bytes. Hex text of zlib data is the documented form; plain PDF text is accepted too."""
    s = re.sub(r"\s+", "", text or "")
    if not s:
        raise ValueError("the item has no data")
    if re.fullmatch(r"(?:[0-9A-Fa-f]{2})+", s):
        raw = bytes.fromhex(s)
        if raw[:1] == b"\x78":                      # zlib header
            try:
                return zlib.decompress(raw)
            except zlib.error as e:
                raise ValueError(f"the compressed data is damaged ({e})") from None
        return raw
    return (text or "").encode("latin-1", "replace")


def _rgb(v):
    return tuple(float(x) for x in v) if isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v) else None


def _gray_or_rgb(v):
    if isinstance(v, list) and len(v) == 1 and isinstance(v[0], (int, float)):
        return (float(v[0]),) * 3
    return _rgb(v)


def tool_from_annotation(d: dict) -> dict:
    """Map a Bluebeam annotation dictionary to a Tool Chest preset (the form Document.add_from_dict reads).
    Raises ValueError with the reason if the kind of markup cannot be recreated."""
    sub = str(d.get("Subtype", ""))
    it = str(d.get("IT", ""))
    kind = {"Square": "Square", "Circle": "Circle", "Line": "Line", "PolyLine": "PolyLine", "Polygon": "Polygon",
            "Ink": "Ink", "FreeText": "FreeText", "Text": "Text"}.get(sub)
    if kind is None:
        raise ValueError(f"the {sub or 'unknown'} type cannot be a tool in OpenRevu yet")
    rect = d.get("Rect")
    if isinstance(rect, list) and len(rect) == 4 and all(isinstance(x, (int, float)) for x in rect):
        w, h = abs(rect[2] - rect[0]), abs(rect[3] - rect[1])
    else:
        w, h = DEFAULT_SIZE
    w, h = max(w, 10.0), max(h, 10.0)
    width = 1.5
    bs = d.get("BS")
    if isinstance(bs, dict) and isinstance(bs.get("W"), (int, float)):
        width = float(bs["W"])
    elif isinstance(d.get("Border"), list) and len(d["Border"]) >= 3 and isinstance(d["Border"][2], (int, float)):
        width = float(d["Border"][2])
    tool = {"kind": kind, "subject": str(d.get("Subj", "")), "comment": str(d.get("Contents", "")),
            "stroke": _gray_or_rgb(d.get("C")) or (1.0, 0.0, 0.0), "fill": _gray_or_rgb(d.get("IC")),
            "width": width, "opacity": float(d["CA"]) if isinstance(d.get("CA"), (int, float)) else 1.0,
            "rect": [0.0, 0.0, w, h], "vertices": None, "arrow": False}
    if kind == "Line":
        pts = d.get("L")
        if not (isinstance(pts, list) and len(pts) == 4):
            pts = [0, 0, w, h]
        tool["vertices"] = _normalise([(pts[0], pts[1]), (pts[2], pts[3])])
        le = d.get("LE")
        tool["arrow"] = isinstance(le, list) and len(le) == 2 and str(le[1]) not in ("None", "")
        tool["rect"] = _bbox(tool["vertices"])
    elif kind in ("PolyLine", "Polygon"):
        v = d.get("Vertices")
        if not (isinstance(v, list) and len(v) >= 6 and len(v) % 2 == 0 and all(isinstance(x, (int, float)) for x in v)):
            raise ValueError("the shape has no usable points")
        tool["vertices"] = _normalise([(v[i], v[i + 1]) for i in range(0, len(v), 2)])
        tool["rect"] = _bbox(tool["vertices"])
        if it == "PolygonCloud":
            tool["subject"] = tool["subject"] or "Cloud"
    elif kind == "Ink":
        strokes = d.get("InkList")
        if not (isinstance(strokes, list) and strokes and all(isinstance(s, list) and len(s) >= 4 for s in strokes)):
            raise ValueError("the pen stroke has no points")
        flat = [p for s in strokes for p in [(s[i], s[i + 1]) for i in range(0, len(s) - 1, 2)]]
        minx, maxy = min(p[0] for p in flat), max(p[1] for p in flat)
        tool["vertices"] = [[[p[0] - minx, maxy - p[1]] for p in [(s[i], s[i + 1]) for i in range(0, len(s) - 1, 2)]] for s in strokes]
        tool["rect"] = [0.0, 0.0, max(p[0] for p in flat) - minx or 1.0, maxy - min(p[1] for p in flat) or 1.0]
    elif kind == "FreeText":
        da = str(d.get("DA", ""))
        m = re.match(r"\s*([\d.\s]+)rg", da)
        if m:
            parts = m.group(1).split()
            if len(parts) == 3:
                tool["stroke"] = tuple(float(x) for x in parts)
        tool["subject"] = tool["subject"] or ("Callout" if it == "FreeTextCallout" else "Text")
        tool["comment"] = tool["comment"] or "Text"
    elif kind == "Text":
        tool["comment"] = tool["comment"] or "Note"
    return tool


def _normalise(pts):
    """PDF points (y up) -> points relative to the top-left of their box (y down)."""
    minx, maxy = min(p[0] for p in pts), max(p[1] for p in pts)
    return [[float(p[0] - minx), float(maxy - p[1])] for p in pts]


def _bbox(v):
    xs, ys = [p[0] for p in v], [p[1] for p in v]
    return [min(xs), min(ys), max(xs) or 1.0, max(ys) or 1.0]


def _child_text(elem, tag):
    """Text of the first child with this tag name, whatever its XML namespace. None if there is none."""
    for c in elem:
        if c.tag.split("}")[-1] == tag:
            return c.text or ""
    return None


def read_btx(path: str):
    """Parse a .btx file. Returns (title, [(name, tool_dict)], [(name, reason)]). Raises ValueError if it is not XML."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise ValueError(f"this is not a readable tool set file ({e})") from None
    root = tree.getroot()
    title = (_child_text(root, "Title") or root.get("Title") or "").strip()
    tools, skipped = [], []
    items = [e for e in root.iter() if e.tag.split("}")[-1] == "ToolChestItem"]
    if not items:
        raise ValueError("no tools were found in this file")
    for n, item in enumerate(items, 1):
        name = (_child_text(item, "Name") or "").strip() or f"Tool {n}"
        try:
            raw = _child_text(item, "Raw")
            if raw is None:
                raise ValueError("the item has no <Raw> data")
            tools.append((name, tool_from_annotation(parse_annotation(decode_raw(raw)))))
        except ValueError as e:
            skipped.append((name, str(e)))
    return title, tools, skipped
