"""Compare two versions of a drawing set: by sheet number or by page, with a pixel overlay and a text diff."""
from __future__ import annotations

import csv
import difflib
from dataclasses import dataclass, field

import fitz

try:
    import numpy as np
except ImportError:  # the slow pure-Python path is used instead
    np = None

OLD_RGB, NEW_RGB, SAME_RGB = (230, 40, 40), (40, 80, 230), (110, 110, 110)


@dataclass
class SheetCompare:
    number: str                      # sheet number, or "page N" when the page has none
    status: str                      # changed, unchanged, added, removed
    old_page: int | None = None      # 0-based
    new_page: int | None = None
    changed_fraction: float = 0.0    # share of the drawn pixels that differ
    area_fraction: float = 0.0       # share of the whole page area that differs
    words_added: int = 0
    words_removed: int = 0
    text_changes: list = field(default_factory=list)
    size_changed: bool = False


def _dilate(mask, t):
    """Grow a boolean mask by t pixels in every direction (a square window)."""
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-t, t + 1):
        for dx in range(-t, t + 1):
            if dx == 0 and dy == 0:
                continue
            out[max(dy, 0):h + min(dy, 0), max(dx, 0):w + min(dx, 0)] |= mask[max(-dy, 0):h + min(-dy, 0), max(-dx, 0):w + min(-dx, 0)]
    return out


def _gray(page, dpi, annots):
    if page is None:
        return None
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, annots=annots)
    return pix.width, pix.height, pix.samples


def diff_pages(pa, pb, dpi=100, tolerance=1, annots=False):
    """Overlay of two pages. Returns (rgb bytes, width, height, changed_pixels, drawn_pixels).
    Red = only in the old page, blue = only in the new page, grey = in both. A missing page counts as blank."""
    ga, gb = _gray(pa, dpi, annots), _gray(pb, dpi, annots)
    w = max(g[0] for g in (ga, gb) if g)
    h = max(g[1] for g in (ga, gb) if g)
    if np is not None:
        def grid(g):
            out = np.full((h, w), 255, np.uint8)
            if g:
                out[:g[1], :g[0]] = np.frombuffer(g[2], np.uint8).reshape(g[1], g[0])
            return out
        da, db = grid(ga) < 128, grid(gb) < 128
        near_a, near_b = (_dilate(da, tolerance), _dilate(db, tolerance)) if tolerance > 0 else (da, db)
        only_old, only_new = da & ~near_b, db & ~near_a
        both = (da | db) & ~only_old & ~only_new
        rgb = np.full((h, w, 3), 255, np.uint8)
        rgb[both], rgb[only_old], rgb[only_new] = SAME_RGB, OLD_RGB, NEW_RGB
        return rgb.tobytes(), w, h, int(only_old.sum() + only_new.sum()), int((da | db).sum())

    def grid(g):  # pure Python: no tolerance
        out = bytearray(b"\xff" * (w * h))
        if g:
            for y in range(g[1]):
                out[y * w:y * w + g[0]] = g[2][y * g[0]:(y + 1) * g[0]]
        return out
    a, b = grid(ga), grid(gb)
    rgb, changed, drawn = bytearray(w * h * 3), 0, 0
    for k in range(w * h):
        x, y = a[k] < 128, b[k] < 128
        if x and y:
            c, drawn = SAME_RGB, drawn + 1
        elif x:
            c, changed, drawn = OLD_RGB, changed + 1, drawn + 1
        elif y:
            c, changed, drawn = NEW_RGB, changed + 1, drawn + 1
        else:
            c = (255, 255, 255)
        rgb[3 * k:3 * k + 3] = bytes(c)
    return bytes(rgb), w, h, changed, drawn


def diff_text(pa, pb, limit=8):
    """Word-level difference. Returns (words_added, words_removed, [short descriptions])."""
    ta = [w[4] for w in pa.get_text("words", sort=True)] if pa is not None else []
    tb = [w[4] for w in pb.get_text("words", sort=True)] if pb is not None else []
    added = removed = 0
    notes = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        old, new = " ".join(ta[i1:i2]), " ".join(tb[j1:j2])
        removed += i2 - i1
        added += j2 - j1
        if len(notes) < limit:
            notes.append({"replace": f"{old[:60]} -> {new[:60]}", "delete": f"- {old[:80]}", "insert": f"+ {new[:80]}"}[tag])
    return added, removed, notes


def _label(doc, pno):
    n = doc.sheet_info(pno)["number"]
    return n or f"page {pno + 1}"


def _pair(old, new, match):
    """[(old_index or None, new_index or None)] in the order of the new document, then removed old pages."""
    if match == "page":
        n = max(len(old.doc), len(new.doc))
        return [(i if i < len(old.doc) else None, i if i < len(new.doc) else None) for i in range(n)]
    on = {}
    for i in range(len(old.doc)):
        num = old.sheet_info(i)["number"]
        if num and num not in on:
            on[num] = i
    pairs, used = [], set()
    for j in range(len(new.doc)):
        num = new.sheet_info(j)["number"]
        i = on.get(num) if num else None
        if i is None and not num and j < len(old.doc) and not old.sheet_info(j)["number"] and j not in used:
            i = j                       # an unnumbered page pairs with an unnumbered page at the same position
        if i is not None and i not in used:
            used.add(i)
            pairs.append((i, j))
        else:
            pairs.append((None, j))
    pairs += [(i, None) for i in range(len(old.doc)) if i not in used]
    return pairs


def compare_documents(old_path, new_path, out_path=None, match="sheet", dpi=100, tolerance=1,
                      old_password=None, new_password=None, annots=False):
    """Compare two PDFs. match="sheet" pairs sheets by sheet number, match="page" pairs by position.
    Writes an overlay PDF to out_path (if given) and returns one SheetCompare per sheet."""
    from .core import Document
    if match not in ("sheet", "page"):
        raise ValueError("match must be 'sheet' or 'page'")
    old, new = Document(old_path, old_password), Document(new_path, new_password)
    out = fitz.open() if out_path else None
    results = []
    for i, j in _pair(old, new, match):
        pa = old.doc[i] if i is not None else None
        pb = new.doc[j] if j is not None else None
        label = _label(new, j) if j is not None else _label(old, i)
        rgb, w, h, changed, drawn = diff_pages(pa, pb, dpi, tolerance, annots)
        wa, wr, notes = diff_text(pa, pb)
        status = "added" if pa is None else "removed" if pb is None else (
            "changed" if (changed > 0 or wa or wr) else "unchanged")
        r = SheetCompare(label, status, i, j, changed / drawn if drawn else 0.0, changed / (w * h), wa, wr, notes,
                         bool(pa is not None and pb is not None and (abs(pa.rect.width - pb.rect.width) > 1 or abs(pa.rect.height - pb.rect.height) > 1)))
        results.append(r)
        if out is not None:
            pix = fitz.Pixmap(fitz.csRGB, w, h, rgb, False)
            pg = out.new_page(width=w * 72 / dpi, height=h * 72 / dpi)
            pg.insert_image(pg.rect, pixmap=pix)
            note = {"added": "only in the new set", "removed": "only in the old set"}.get(status, f"{r.changed_fraction:.1%} of the drawing changed")
            pg.insert_text((18, 20), f"{label}: {status} ({note}).  Red = only in old, blue = only in new.", fontsize=9, color=(0.1, 0.1, 0.1))
    if out is not None:
        if not len(out):
            out.new_page()
        out.save(out_path, garbage=3, deflate=True)
        out.close()
    old.doc.close(); new.doc.close()
    return results


def write_csv(results, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sheet", "status", "old page", "new page", "drawing changed", "words added", "words removed", "size changed", "text changes"])
        for r in results:
            w.writerow([r.number, r.status, "" if r.old_page is None else r.old_page + 1, "" if r.new_page is None else r.new_page + 1,
                        f"{r.changed_fraction:.4f}", r.words_added, r.words_removed, "yes" if r.size_changed else "", " | ".join(r.text_changes)])


def summarize(results) -> str:
    n = lambda s: sum(1 for r in results if r.status == s)  # noqa: E731
    return f"{n('changed')} changed, {n('unchanged')} unchanged, {n('added')} added, {n('removed')} removed"
