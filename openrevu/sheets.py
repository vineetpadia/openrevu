"""Sheet database: sheet number, title, discipline and revision history for each page.

The record sits in a custom key of the page dictionary. It follows the page when you reorder, delete, or save.
(PyMuPDF does not copy custom page keys when pages move between files, so merge, extract and split copy them here.)"""
from __future__ import annotations

import base64
import csv
import datetime
import json
import re
from dataclasses import dataclass, field

import fitz

from ._util import mutates

KEY = "OR_Sheet"
DEFAULT_PATTERN = r"\b[A-Z]{1,3}[-.]?\d{1,3}(?:\.\d{1,2})?\b"
FIELDS = ("number", "title", "discipline")


def _encode(rec: dict) -> str:
    return "(" + base64.b64encode(json.dumps(rec, ensure_ascii=False).encode("utf-8")).decode("ascii") + ")"


def _decode(value: str) -> dict:
    try:
        return json.loads(base64.b64decode(value).decode("utf-8"))
    except (ValueError, TypeError):
        return {}


def read_record(doc: fitz.Document, pno: int) -> dict:
    t, v = doc.xref_get_key(doc.page_xref(pno), KEY)
    return {} if t == "null" else _decode(v)


def write_record(doc: fitz.Document, pno: int, rec: dict) -> None:
    key_value = _encode(rec) if rec else "null"
    doc.xref_set_key(doc.page_xref(pno), KEY, key_value)


def copy_records(src: fitz.Document, src_pages, dst: fitz.Document, dst_first: int) -> None:
    """Copy sheet records for src_pages (in order) onto consecutive pages of dst starting at dst_first."""
    for k, sp in enumerate(src_pages):
        rec = read_record(src, sp)
        if rec:
            write_record(dst, dst_first + k, rec)


@dataclass
class SlipReport:
    replaced: list = field(default_factory=list)       # (sheet number, page index)
    added: list = field(default_factory=list)          # sheet numbers appended at the end
    not_in_new: list = field(default_factory=list)     # old sheets that the new file does not contain (kept)
    size_changed: list = field(default_factory=list)   # (sheet number, old size, new size): markups may not line up
    rotation_changed: list = field(default_factory=list)   # (sheet number, old rotation, new rotation): markups may not line up
    duplicate_numbers: list = field(default_factory=list)  # numbers of added sheets that were already in use (stored without a number)
    markups_moved: int = 0

    def summary(self) -> str:
        parts = [f"{len(self.replaced)} sheet(s) replaced", f"{len(self.added)} added",
                 f"{len(self.not_in_new)} not in the new file (kept)"]
        if self.size_changed:
            parts.append(f"{len(self.size_changed)} with a different page size (check the markups)")
        if self.rotation_changed:
            parts.append(f"{len(self.rotation_changed)} with a different page rotation (check the markups)")
        if self.duplicate_numbers:
            parts.append(f"{len(self.duplicate_numbers)} added without a number because it was already used "
                         f"({', '.join(self.duplicate_numbers)})")
        return ", ".join(parts) + f"; {self.markups_moved} markup(s) carried over."


class SheetOps:
    # ---------- records ----------
    def sheet_record(self, pno: int) -> dict:
        """The stored record only (empty if none)."""
        self._check([pno])
        return read_record(self.doc, pno)

    def _detect(self, pno: int, pattern=None, corner=None):
        """(number, title) found by text size. Number: the largest text that matches the pattern.
        Title: the largest other text line with 3 or more letters, in the title-block corner if one is given."""
        rx = re.compile(pattern or DEFAULT_PATTERN)
        pg = self.doc[pno]
        spans = []
        for b in pg.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for sp in ln["spans"]:
                    t = sp["text"].strip()
                    if t:
                        spans.append((sp["size"], t, fitz.Rect(sp["bbox"])))
        if corner is not None:
            box = fitz.Rect(corner)
            spans = [s for s in spans if box.intersects(self._rv(pno, s[2]))]
        number = max(((s, t) for s, t, _ in spans if rx.fullmatch(t)), default=(0, None))[1]
        titles = [(s, t) for s, t, _ in spans if t != number and sum(c.isalpha() for c in t) >= 3 and not rx.fullmatch(t)]
        title = max(titles, default=(0, ""))[1]
        if number and title.startswith(number):            # "A-101 Ground floor plan" -> "Ground floor plan"
            title = title[len(number):].lstrip(" -\u2013\u2014:.\t")
        return number, title

    def sheet_info(self, pno: int, detect: bool = True) -> dict:
        """number, title, discipline, revisions for a page. A stored value wins over a detected one."""
        rec = self.sheet_record(pno)
        number, title = rec.get("number"), rec.get("title", "")
        if detect and not number and not rec.get("unnumbered"):      # "unnumbered": the number was deliberately left out
            number, dtitle = self._detect(pno)
            title = title or dtitle
        return {"number": number or "", "title": title or "", "discipline": rec.get("discipline", ""),
                "revisions": list(rec.get("revisions", [])), "stored": bool(rec)}

    @mutates
    def set_sheet(self, pno: int, number=None, title=None, discipline=None):
        self._check([pno])
        rec = read_record(self.doc, pno)
        for name, value in (("number", number), ("title", title), ("discipline", discipline)):
            if value is not None:
                if name == "number" and value.strip():
                    rec.pop("unnumbered", None)
                    clash = [i for i in range(len(self.doc)) if i != pno and self.sheet_info(i)["number"] == value.strip()]
                    if clash:
                        raise ValueError(f"sheet number {value.strip()!r} is already used on page {clash[0] + 1}")
                rec[name] = value.strip()
        write_record(self.doc, pno, {k: v for k, v in rec.items() if v not in ("", None, [])})

    @mutates
    def detect_sheets(self, pattern=None, corner=None, overwrite=False) -> int:
        """Store the detected number and title of each page. Existing values stay unless overwrite is true."""
        done, seen = 0, {}
        found = [self._detect(i, pattern, corner) for i in range(len(self.doc))]   # read everything first: a failure changes nothing
        for i in range(len(self.doc)):
            rec = read_record(self.doc, i)
            number, title = found[i]
            if (overwrite or not (rec.get("number") or rec.get("unnumbered"))) and number:
                if number in seen:       # two pages with the same number: do not store the second one
                    continue
                rec["number"] = number
                done += 1
            if (overwrite or not rec.get("title")) and title:
                rec["title"] = title
            if rec.get("number"):
                seen[rec["number"]] = i
            write_record(self.doc, i, rec)
        return done

    @mutates
    def add_revision(self, pno: int, rev: str, description: str = "", date: str | None = None):
        self._check([pno])
        rev = (rev or "").strip()
        if not rev:
            raise ValueError("the revision needs a name, for example 'B' or '3'")
        rec = read_record(self.doc, pno)
        revs = rec.get("revisions", [])
        if any(r["rev"] == rev for r in revs):
            raise ValueError(f"revision {rev!r} already exists for this sheet")
        if date is not None:
            datetime.date.fromisoformat(date)  # raises ValueError for a bad date
        revs.append({"rev": rev, "date": date or datetime.date.today().isoformat(), "description": description.strip()})
        rec["revisions"] = revs
        if not rec.get("number"):
            rec["number"] = self.sheet_info(pno)["number"]
        write_record(self.doc, pno, {k: v for k, v in rec.items() if v not in ("", None)})

    @mutates
    def remove_revision(self, pno: int, rev: str):
        rec = read_record(self.doc, pno)
        revs = [r for r in rec.get("revisions", []) if r["rev"] != rev]
        if len(revs) == len(rec.get("revisions", [])):
            raise KeyError(rev)
        rec["revisions"] = revs
        write_record(self.doc, pno, {k: v for k, v in rec.items() if v not in ("", None, [])})

    def current_revision(self, pno: int) -> str:
        revs = self.sheet_record(pno).get("revisions", [])
        return revs[-1]["rev"] if revs else ""

    def sheet_table(self) -> list[dict]:
        out = []
        for i in range(len(self.doc)):
            info = self.sheet_info(i)
            revs = info["revisions"]
            out.append({"page": i + 1, "number": info["number"], "title": info["title"],
                        "discipline": info["discipline"], "revision": revs[-1]["rev"] if revs else "",
                        "revision_date": revs[-1]["date"] if revs else "", "revisions": len(revs)})
        return out

    def export_sheet_index_csv(self, path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["page", "number", "title", "discipline", "revision", "revision date", "revision count"])
            for r in self.sheet_table():
                w.writerow([r["page"], r["number"], r["title"], r["discipline"], r["revision"], r["revision_date"], r["revisions"]])

    @mutates
    def insert_sheet_index(self, at: int = 0, title: str = "Sheet Index", rows_per_page: int = 38) -> int:
        """Insert a table of all sheets (with links to each sheet) as new page(s). Returns the number of pages added."""
        table = self.sheet_table()
        if not table:
            raise ValueError("the document has no pages")
        at = max(0, min(at, len(self.doc)))
        n_idx = (len(table) + rows_per_page - 1) // rows_per_page
        base = self.doc[0].rect
        w, h = (base.width, base.height) if base.width > 0 else (612, 792)
        for k in range(n_idx):
            self.insert_blank(at + k, width=w, height=h)
        font = fitz.Font("helv")
        cols = [("Sheet", 50), ("Title", 130), ("Discipline", 380), ("Rev", 470), ("Date", 505)]
        for k in range(n_idx):
            pg = self.doc[at + k]
            pg.insert_text((50, 60), title + (f" ({k + 1}/{n_idx})" if n_idx > 1 else ""), fontsize=18, fontname="hebo")
            y = 95
            for name, x in cols:
                pg.insert_text((x, y), name, fontsize=9, fontname="hebo")
            pg.draw_line((50, y + 4), (w - 50, y + 4), width=0.8)
            y += 20
            for r in table[k * rows_per_page:(k + 1) * rows_per_page]:
                target = r["page"] - 1 + (n_idx if r["page"] - 1 >= at else 0)
                vals = [r["number"] or "-", r["title"][:48], r["discipline"][:14], r["revision"], r["revision_date"]]
                for (name, x), v in zip(cols, vals):
                    pg.insert_text((x, y), v, fontsize=9)
                link_rect = fitz.Rect(48, y - 10, w - 48, y + 3)
                pg.insert_link({"kind": fitz.LINK_GOTO, "from": link_rect, "page": target})
                y += 16
        return n_idx

    # ---------- slip sheet: replace sheets with a new version and keep the markups ----------
    @mutates
    def slip_sheet(self, new_path: str, match: str = "number", keep_markups: bool = True,
                   revision: str | None = None, description: str = "", password: str | None = None) -> SlipReport:
        """Replace sheets of this document with the sheets of another PDF.

        match="number": pair sheets by sheet number. match="page": pair by page position.
        Markups, hyperlinks, bookmarks, scales and the sheet record stay with the new page.
        Sheets that are only in the new file are added at the end. Sheets that are only here are kept."""
        if match not in ("number", "page"):
            raise ValueError("match must be 'number' or 'page'")
        new = type(self)(new_path, password)  # read the new file with the same tools (also gives its sheet numbers)
        rep = SlipReport()
        n_old = len(self.doc)
        old_by_num: dict = {}
        if match == "number":
            for i in range(n_old):
                num = self.sheet_info(i)["number"]
                if num:
                    if num in old_by_num:
                        raise ValueError(f"sheet number {num!r} is on pages {old_by_num[num] + 1} and {i + 1}; "
                                         "fix the numbers first, or match by page")
                    old_by_num[num] = i
        pairs, added, used_old = [], [], set()
        for j in range(len(new.doc)):
            if match == "page":
                (pairs.append((j, j)) if j < n_old else added.append((j, new.sheet_info(j)["number"] or f"page {j + 1}")))
                continue
            num = new.sheet_info(j)["number"]
            if num and num in old_by_num and old_by_num[num] not in used_old:
                pairs.append((old_by_num[num], j))
                used_old.add(old_by_num[num])
            else:
                added.append((j, num or f"page {j + 1}"))
        if match == "number" and any(not new.sheet_info(j)["number"] for j in range(len(new.doc))) and not pairs:
            raise ValueError("no sheet numbers were found in the new file; match by page, or set the numbers first")
        if revision:  # check everything before the first change
            for i, _j in pairs:
                if any(r["rev"] == revision for r in read_record(self.doc, i).get("revisions", [])):
                    raise ValueError(f"revision {revision!r} already exists on sheet {self.sheet_info(i)['number']}")
        toc = self.doc.get_toc()
        # step 1, from the last pair to the first (earlier page numbers stay valid): put each new page after its old page
        mapping = {}
        for i, j in sorted(pairs, reverse=True):
            num = self.sheet_info(i)["number"] or new.sheet_info(j)["number"] or f"page {i + 1}"
            old_rect, new_rect = self.doc[i].rect, new.doc[j].rect
            if abs(old_rect.width - new_rect.width) > 1 or abs(old_rect.height - new_rect.height) > 1:
                rep.size_changed.append((num, (round(old_rect.width), round(old_rect.height)),
                                         (round(new_rect.width), round(new_rect.height))))
            if self.doc[i].rotation != new.doc[j].rotation:   # markups are stored in the unrotated page space
                rep.rotation_changed.append((num, self.doc[i].rotation, new.doc[j].rotation))
            self.doc.insert_pdf(new.doc, from_page=j, to_page=j, start_at=i + 1)
            old_x, new_x = self.doc.page_xref(i), self.doc.page_xref(i + 1)
            rec = read_record(self.doc, i) or {}
            new_rec = read_record(new.doc, j)
            rec.setdefault("number", num if num and not num.startswith("page ") else new_rec.get("number", ""))
            rec.setdefault("title", new_rec.get("title") or new.sheet_info(j)["title"])
            if revision:
                revs = rec.get("revisions", [])
                revs.append({"rev": revision, "date": datetime.date.today().isoformat(), "description": description})
                rec["revisions"] = revs
            write_record(self.doc, i + 1, {k: v for k, v in rec.items() if v not in ("", None, [])})
            if keep_markups:
                rep.markups_moved += self._move_annotations(i, i + 1, old_x, new_x)
            mapping[old_x] = new_x
            rep.replaced.append((num, i))
        # step 2: links and bookmarks that pointed at an old page now point at its replacement (one pass over the file)
        self._retarget_destinations(mapping)
        # step 3: remove the old pages, last first. Each earlier pair added one page in front of this old page.
        asc = sorted(pairs)
        for pos in range(len(asc) - 1, -1, -1):
            self.doc.delete_page(asc[pos][0] + pos)
        rep.replaced.reverse()
        in_use = {self.sheet_info(i)["number"] for i in range(len(self.doc)) if self.sheet_info(i)["number"]}
        for j, num in added:
            at = len(self.doc)
            self.doc.insert_pdf(new.doc, from_page=j, to_page=j, start_at=at)
            info = new.sheet_info(j)
            rec = read_record(new.doc, j)
            rec.setdefault("number", info["number"])
            rec.setdefault("title", info["title"])
            if rec.get("number") in in_use:       # two sheets with one number would make the next slip sheet fail
                rep.duplicate_numbers.append(rec["number"])
                num = rec.pop("number")
                rec["unnumbered"] = True              # keep it from being detected again from its text
            elif rec.get("number"):
                in_use.add(rec["number"])
            write_record(self.doc, at, {k: v for k, v in rec.items() if v not in ("", None, [])})
            rep.added.append(num)
        rep.not_in_new = [self.sheet_info(i)["number"] or f"page {i + 1}" for i in range(n_old) if i not in {p[0] for p in pairs}]
        if toc:
            self.doc.set_toc(toc)
        new.doc.close()
        self._invalidate_pages()
        return rep

    def _move_annotations(self, old_pno: int, new_pno: int, old_x: int, new_x: int) -> int:
        """Re-attach every annotation object of the old page (markups, links, popups) to the new page.
        Returns the number of top-level markups (links, popups, measurement labels and replies are not counted)."""
        moved = [(x, t) for x, t, _id in self.doc.page_annot_xrefs(old_pno)]
        if not moved:
            return 0
        existing = [x for x, _t, _id in self.doc.page_annot_xrefs(new_pno)]
        self.doc.xref_set_key(new_x, "Annots", "[" + " ".join(f"{x} 0 R" for x in existing + [m[0] for m in moved]) + "]")
        for x, _t in moved:
            self.doc.xref_set_key(x, "P", f"{new_x} 0 R")
        self.doc.xref_set_key(old_x, "Annots", "null")
        def top_level(x, t):
            return (t not in (fitz.PDF_ANNOT_LINK, fitz.PDF_ANNOT_POPUP)
                    and self.doc.xref_get_key(x, "OR_Parent")[0] == "null" and self.doc.xref_get_key(x, "IRT")[0] == "null")
        return sum(1 for x, t in moved if top_level(x, t))

    def _retarget_destinations(self, mapping: dict) -> None:
        """Rewrite every destination array that starts with an old page reference: /Dest, /D, named destinations,
        and /OpenAction. A destination has the shape [page /XYZ ...] or [page /Fit...]; the page tree's /Kids array
        does not, so it is left alone."""
        if not mapping:
            return
        olds = "|".join(str(k) for k in mapping)
        pat = re.compile(r"(\[\s*)(%s)( 0 R\s*/(?:XYZ|Fit\w*))" % olds)
        for x in range(1, self.doc.xref_length()):
            try:
                if self.doc.xref_is_stream(x):
                    continue
                obj = self.doc.xref_object(x, compressed=False)
            except Exception:
                continue
            if " 0 R" in obj and "/XYZ" in obj or "/Fit" in obj:
                new = pat.sub(lambda m: f"{m.group(1)}{mapping[int(m.group(2))]}{m.group(3)}", obj)
                if new != obj:
                    self.doc.update_object(x, new)
