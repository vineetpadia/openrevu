"""Page- and document-level operations mixed into Document."""
from __future__ import annotations

import datetime
import os
import shutil

import fitz

from ._util import mutates


class PageOps:
    # ---- helpers ----
    def _remap_scales(self, mapping: dict):
        """mapping: old page index -> new index (missing = page removed)."""
        self.page_scales = {mapping[k]: v for k, v in self.page_scales.items() if k in mapping}
        self._invalidate_pages()

    def _check(self, pnos):
        for p in pnos:
            if not 0 <= p < len(self.doc):
                raise IndexError(f"page {p} out of range")

    # ---- page management ----
    @mutates
    def insert_blank(self, at=None, width=612, height=792):
        at = len(self.doc) if at is None else at
        n = len(self.doc)
        self.doc.new_page(pno=at, width=width, height=height)
        self._remap_scales({i: i + (i >= at) for i in range(n)})

    @mutates
    def delete_pages(self, pnos):
        pnos = sorted(set(pnos))
        self._check(pnos)
        if len(pnos) >= len(self.doc):
            raise ValueError("cannot delete every page")
        n = len(self.doc)
        self.doc.delete_pages(pnos)
        keep = [i for i in range(n) if i not in pnos]
        self._remap_scales({old: new for new, old in enumerate(keep)})

    @mutates
    def rotate_pages(self, pnos, degrees):
        if degrees % 90:
            raise ValueError("rotation must be a multiple of 90")
        self._check(pnos)
        for p in pnos:
            pg = self.doc[p]
            pg.set_rotation((pg.rotation + degrees) % 360)

    @mutates
    def move_page(self, src, dst):
        self._check([src, dst])
        n = len(self.doc)
        order = [i for i in range(n) if i != src]
        order.insert(dst, src)
        self.doc.select(order)
        self._remap_scales({old: new for new, old in enumerate(order)})

    @mutates
    def reorder(self, order):
        if sorted(order) != list(range(len(self.doc))):
            raise ValueError("order must be a permutation of all pages")
        self.doc.select(list(order))
        self._remap_scales({old: new for new, old in enumerate(order)})

    @mutates
    def crop_page(self, pno, rect):
        """Crop to rect given in visual page coordinates (relative to the currently visible area)."""
        self._check([pno])
        pg = self._page(pno)
        r = self._ru(pno, fitz.Rect(rect) & pg.rect)
        if r.is_empty or r.width < 1 or r.height < 1:
            raise ValueError("crop rectangle outside page")
        cb = pg.cropbox
        pg.set_cropbox(fitz.Rect(r.x0 + cb.x0, r.y0 + cb.y0, r.x1 + cb.x0, r.y1 + cb.y0))

    @mutates
    def insert_pdf(self, path, at=None, pages=None, password=None):
        src = self._open(path)
        if src.needs_pass and not src.authenticate(password or ""):
            raise PermissionError("source PDF is encrypted")
        if pages is not None and any(not 0 <= q < len(src) for q in pages):
            raise IndexError(f"the source PDF has {len(src)} pages; asked for {sorted(pages)}")
        at = len(self.doc) if at is None else at
        n, k = len(self.doc), (len(pages) if pages is not None else len(src))
        kw = {"start_at": at}
        from .sheets import copy_records
        if pages is not None:
            for p in pages:
                self.doc.insert_pdf(src, from_page=p, to_page=p, start_at=at)
                copy_records(src, [p], self.doc, at)
                at += 1
        else:
            self.doc.insert_pdf(src, **kw)
            copy_records(src, range(len(src)), self.doc, kw["start_at"])
        src.close()
        self._remap_scales({i: i + (k if i >= kw["start_at"] else 0) for i in range(n)})
        return k

    @mutates
    def insert_image_page(self, image_path, at=None):
        at = len(self.doc) if at is None else at
        img = fitz.open(image_path)
        pdfbytes = img.convert_to_pdf()
        src = fitz.open("pdf", pdfbytes)
        n = len(self.doc)
        self.doc.insert_pdf(src, start_at=at)
        self._remap_scales({i: i + (i >= at) for i in range(n)})

    def extract_pages(self, pnos, out_path):
        self._check(pnos)
        from .sheets import copy_records
        out = fitz.open()
        for p in pnos:
            out.insert_pdf(self.doc, from_page=p, to_page=p)
        copy_records(self.doc, pnos, out, 0)
        self._write_settings(out, list(pnos))      # scales, viewports, custom columns, and the unit style go with the pages
        out.save(out_path, garbage=3, deflate=True, **self._encryption_kwargs())   # a protected document stays protected
        return out_path

    def split(self, out_dir, every=None, ranges=None, prefix="part"):
        """Split into files: every N pages, or explicit inclusive ranges [(first,last), ...]."""
        n = len(self.doc)
        if ranges is None:
            if not every or every < 1:
                raise ValueError("give every>=1 or ranges")
            ranges = [(i, min(i + every, n) - 1) for i in range(0, n, every)]
        os.makedirs(out_dir, exist_ok=True)
        outs = []
        for i, (a, b) in enumerate(ranges, 1):
            if not 0 <= a <= b < n:
                raise IndexError(f"bad range {(a, b)}")
            outs.append(self.extract_pages(list(range(a, b + 1)), os.path.join(out_dir, f"{prefix}_{i:03d}.pdf")))
        return outs

    @classmethod
    def from_images(cls, paths):
        d = cls()
        for p in paths:
            d.doc.insert_pdf(fitz.open("pdf", fitz.open(p).convert_to_pdf()))
        return d

    # ---- navigation: bookmarks, links, labels ----
    def toc(self):
        return self.doc.get_toc()

    @mutates
    def set_toc(self, toc):
        self.doc.set_toc(toc)

    @mutates
    def add_bookmark(self, title, pno, level=1):
        self._check([pno])
        t = self.doc.get_toc()
        t.append([level if t else 1, title, pno + 1])
        self.doc.set_toc(t)

    @mutates
    def add_link_uri(self, pno, rect, uri):
        self._page(pno).insert_link({"kind": fitz.LINK_URI, "from": self._ru(pno, rect), "uri": uri})

    @mutates
    def add_link_goto(self, pno, rect, target_page):
        self._check([target_page])
        self._page(pno).insert_link({"kind": fitz.LINK_GOTO, "from": self._ru(pno, rect), "page": target_page})

    def links(self, pno):
        # PyMuPDF's in-memory link list lags behind insert_link on a modified page; a serialized copy is exact.
        # The copy is rebuilt only when the document has changed.
        if self._link_doc is None or self._link_rev != self.revision:
            self._link_doc, self._link_rev = fitz.open("pdf", self.doc.tobytes()), self.revision
        return self._link_doc[pno].get_links()

    @mutates
    def set_page_labels(self, rules):
        """rules: [{'startpage':0,'prefix':'A-','style':'D','firstpagenum':1}, ...]"""
        self.doc.set_page_labels(rules)

    def page_label(self, pno):
        return self._page(pno).get_label()

    # ---- text: search / OCR ----
    def search(self, text, pages=None):
        out = []
        for p in (range(len(self.doc)) if pages is None else pages):
            out += [(p, self._rv(p, r)) for r in self.doc[p].search_for(text)]
        return out

    def page_text(self, pno):
        return self._page(pno).get_text()

    @staticmethod
    def ocr_available() -> bool:
        """True if the tesseract binary is on PATH; also locates its language data when TESSDATA_PREFIX is unset."""
        exe = shutil.which("tesseract")
        if exe and not os.environ.get("TESSDATA_PREFIX"):
            root = os.path.dirname(os.path.dirname(os.path.realpath(exe)))
            for cand in (os.path.join(root, "share", "tessdata"), "/usr/share/tesseract-ocr/5/tessdata",
                         "/usr/share/tesseract-ocr/4.00/tessdata", "/usr/share/tessdata"):
                if os.path.isdir(cand):
                    os.environ["TESSDATA_PREFIX"] = cand
                    break
        return exe is not None

    @mutates
    def ocr(self, language="eng", dpi=300, pages=None):
        """Add an invisible text layer to scanned pages (needs the tesseract binary)."""
        if not self.ocr_available():
            raise RuntimeError("OCR needs the 'tesseract' binary installed and on PATH")
        done = 0
        for p in (range(len(self.doc)) if pages is None else pages):
            pg = self.doc[p]
            if pg.get_text().strip():
                continue
            tp = pg.get_textpage_ocr(language=language, dpi=dpi, full=True)
            for x0, y0, x1, y1, w, *_ in pg.get_text("words", textpage=tp):
                pg.insert_text((x0, y1), w, fontsize=max(1, y1 - y0), render_mode=3)
            done += 1
        return done

    # ---- stamping the whole document ----
    @mutates
    def watermark(self, text, pages=None, color=(0.7, 0.7, 0.7), opacity=0.3, fontsize=64, angle=45):
        for p in (range(len(self.doc)) if pages is None else pages):
            pg = self.doc[p]
            vr = pg.rect
            c = self._tu(p, (vr.width / 2, vr.height / 2))  # centre in API (unrotated) space
            tw = fitz.TextWriter(pg.mediabox, opacity=opacity, color=color)
            font = fitz.Font("helv")
            w = font.text_length(text, fontsize)
            tw.append((c.x - w / 2, c.y + fontsize / 3), text, font=font, fontsize=fontsize)
            tw.write_text(pg, morph=(c, fitz.Matrix(-(angle + pg.rotation))), overlay=True)

    @mutates
    def watermark_image(self, image_path, pages=None, opacity=0.3, scale=0.6):
        for p in (range(len(self.doc)) if pages is None else pages):
            pg = self.doc[p]
            w, h = pg.rect.width * scale, pg.rect.height * scale
            c = fitz.Point(pg.rect.width / 2, pg.rect.height / 2)
            box = self._ru(p, fitz.Rect(c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2))
            pg.insert_image(box, filename=image_path, overlay=True, keep_proportion=True, rotate=pg.rotation)

    def _put_text(self, pno, x, y, text, fontsize, fontname="helv"):
        """Insert upright text with its baseline start at visual point (x, y)."""
        pg = self._page(pno)
        pg.insert_text(self._tu(pno, (x, y)), text, fontsize=fontsize, fontname=fontname, rotate=pg.rotation)

    @mutates
    def header_footer(self, header=("", "", ""), footer=("", "", ""), fontsize=9, margin=24, pages=None, name=""):
        """Tokens: {page} {pages} {date} {name}. Triples are (left, centre, right)."""
        n, today = len(self.doc), datetime.date.today().isoformat()
        font = fitz.Font("helv")
        for p in (range(n) if pages is None else pages):
            r = self.doc[p].rect

            def put(texts, y):
                for t, align in zip(texts, (0, 1, 2)):
                    t = t.replace("{page}", str(p + 1)).replace("{pages}", str(n)).replace("{date}", today).replace("{name}", name)
                    if not t:
                        continue
                    w = font.text_length(t, fontsize)
                    x = (margin, (r.width - w) / 2, r.width - margin - w)[align]
                    self._put_text(p, x, y, t, fontsize)

            put(header, margin)
            put(footer, r.height - margin + fontsize)

    @mutates
    def bates(self, prefix="", start=1, digits=6, pages=None, position="br"):
        n = len(self.doc)
        for i, p in enumerate(range(n) if pages is None else pages):
            r = self.doc[p].rect
            t = f"{prefix}{start + i:0{digits}d}"
            w = fitz.Font("helv").text_length(t, 10)
            x = r.width - 24 - w if "r" in position else 24
            y = r.height - 18 if "b" in position else 28
            self._put_text(p, x, y, t, 10)

    # ---- redaction / flatten / security ----
    @mutates
    def mark_redaction(self, pno, rect, fill=(0, 0, 0)):
        self._page(pno).add_redact_annot(self._ru(pno, rect), fill=fill)

    @mutates
    def mark_redaction_text(self, text, fill=(0, 0, 0)):
        """Mark page text matches, and any annotation whose comment/subject contains the text."""
        n = 0
        for p, r in self.search(text):
            self.doc[p].add_redact_annot(self._ru(p, r), fill=fill)
            n += 1
        needle = text.lower()
        for pg in self.doc:
            hits = [a.rect for a in pg.annots() or []
                    if a.type[1] != "Redact" and needle in (a.info.get("content", "") + a.info.get("subject", "")).lower()]
            for r in hits:
                pg.add_redact_annot(r, fill=fill)
                n += 1
        return n

    @mutates
    def apply_redactions(self):
        """Permanently remove content under marked areas: page text, images, line art, and any annotation
        (notes, comments, stamps...) overlapping a mark."""
        n = 0
        for pg in self.doc:
            marks = [a for a in pg.annots() or [] if a.type[1] == "Redact"]
            if not marks:
                continue
            areas = [m.rect for m in marks]
            for a in list(pg.annots() or []):
                if a.type[1] != "Redact" and any(a.rect.intersects(r) for r in areas):
                    pg.delete_annot(a)
            pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)
            n += len(marks)
        self._invalidate_pages()
        return n

    @mutates
    def flatten(self, annots=True, widgets=True):
        self.doc.bake(annots=annots, widgets=widgets)
        self._invalidate_pages()

    def save_encrypted(self, path, user_pw, owner_pw=None, allow_print=True, allow_copy=False, allow_modify=False):
        perm = fitz.PDF_PERM_ACCESSIBILITY
        if allow_print:
            perm |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ
        if allow_copy:
            perm |= fitz.PDF_PERM_COPY
        if allow_modify:
            perm |= fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_FORM
        self._store_scales()
        self.doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=user_pw,
                      owner_pw=owner_pw or user_pw + "-owner", permissions=perm, garbage=3, deflate=True)

    @mutates
    def set_metadata(self, **fields):
        md = dict(self.doc.metadata or {})
        md.update({k: v for k, v in fields.items() if k in ("title", "author", "subject", "keywords")})
        self.doc.set_metadata(md)

    def export_pdfa(self, path, level="2b", flatten=True):
        """Save a PDF/A copy (Ghostscript) and check it with veraPDF if available. Returns a PdfaReport.
        By default markups are flattened first, because conversion may not keep every annotation."""
        import tempfile, os
        from . import pdfa
        if not pdfa.available():
            raise RuntimeError("PDF/A export needs Ghostscript (https://ghostscript.com) on your PATH.")
        self._store_scales()
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.pdf")
            data = self.doc.tobytes()
            tmpdoc = fitz.open("pdf", data)
            if tmpdoc.needs_pass:
                raise PermissionError("decrypt the document before you export it as PDF/A")
            if level == "1b":
                self._blend_transparency(tmpdoc)   # PDF/A-1 has no transparency
            if flatten:
                tmpdoc.bake(annots=True, widgets=True)
            tmpdoc.save(src)
            before = [pg.get_text().strip() for pg in tmpdoc]
            tmpdoc.close()
            rep = pdfa.convert(src, path, level)
            if self._password:
                rep.warnings.append("the PDF/A copy is not password protected, because PDF/A does not allow encryption. "
                                    "Keep the file in a safe place.")
            out = fitz.open(path)
            links_before = sum(len(self.links(i)) for i in range(len(self.doc)))
            links_after = sum(len(pg.get_links()) for pg in out)
            if links_after < links_before:
                rep.warnings.append(f"{links_before - links_after} link(s) were removed: Ghostscript does not keep links in "
                                    "PDF/A output.")
            for i in range(min(len(out), len(self.doc))):
                a_, b_ = self.doc[i].rect, out[i].rect
                if abs(a_.width - b_.width) > 1 or abs(a_.height - b_.height) > 1:
                    rep.warnings.append(f"page {i + 1} was turned: it is {b_.width:.0f} x {b_.height:.0f} points in the copy "
                                        f"and was {a_.width:.0f} x {a_.height:.0f}.")
            for i, text in enumerate(before):
                if text and i < len(out) and not out[i].get_text().strip():
                    rep.warnings.append(f"page {i + 1} lost its selectable text (it was drawn as an image). "
                                        "It uses transparency, which this PDF/A level does not allow. Try level 2b.")
            out.close()
            return rep

    @staticmethod
    def _blend_transparency(doc):
        """Replace each partly transparent markup colour by the same colour mixed with white, at full opacity."""
        for pg in doc:
            for a in list(pg.annots() or []):
                op = a.opacity
                if op is None or not (0 <= op < 1):
                    continue
                c = a.colors
                mix = lambda col: tuple(v * op + (1 - op) for v in col) if col else None  # noqa: E731
                try:
                    a.set_colors(stroke=mix(c.get("stroke")), fill=mix(c.get("fill")))
                    a.set_opacity(1)
                    a.update()
                except Exception:
                    pass

    def export_markup_summary(self, path, include_images=True, statuses=None):
        """Write a PDF report of the markups (page, subject, author, status, comment, replies, measurement,
        custom columns, and a picture). statuses limits the report, for example ["Accepted"]. Returns the count."""
        from .report import markup_summary_pdf
        return markup_summary_pdf(self, path, include_images, statuses, encryption=self._encryption_kwargs())

    def optimize(self, path):
        """Save with maximum structural compression; returns (before, after) byte sizes."""
        self._store_scales()
        before = len(self.doc.tobytes())
        self.doc.save(path, garbage=4, clean=True, deflate=True, deflate_images=True, deflate_fonts=True,
                      **self._encryption_kwargs())
        return before, os.path.getsize(path)

    # ---- forms ----
    def form_fields(self):
        out = []
        for p, pg in enumerate(self.doc):
            for w in pg.widgets() or []:
                out.append({"page": p, "name": w.field_name, "type": w.field_type_string, "value": w.field_value})
        return out

    @mutates
    def set_form_value(self, name, value):
        hit = 0
        for pg in self.doc:
            for w in pg.widgets() or []:
                if w.field_name == name:
                    w.field_value = value
                    w.update()
                    hit += 1
        if not hit:
            raise KeyError(name)
        return hit

    @mutates
    def add_form_field(self, pno, rect, name, kind="text", value="", choices=()):
        """Create a fillable field: kind is text | checkbox | combo | list | button (rect in visual coords)."""
        types = {"text": fitz.PDF_WIDGET_TYPE_TEXT, "checkbox": fitz.PDF_WIDGET_TYPE_CHECKBOX,
                 "combo": fitz.PDF_WIDGET_TYPE_COMBOBOX, "list": fitz.PDF_WIDGET_TYPE_LISTBOX,
                 "button": fitz.PDF_WIDGET_TYPE_BUTTON}
        if kind not in types:
            raise ValueError(f"unknown field kind {kind!r}")
        if not name or any(f["name"] == name for f in self.form_fields()):
            raise ValueError(f"field name {name!r} is empty or already used")
        if kind in ("combo", "list") and not choices:
            raise ValueError("combo/list fields need choices")
        w = fitz.Widget()
        w.field_name, w.field_type = name, types[kind]
        w.rect = self._ru(pno, rect)
        w.field_value = value
        if choices:
            w.choice_values = list(choices)
        if kind == "button":
            w.field_label = value or name
        w.border_color, w.fill_color = (0, 0, 0), (0.95, 0.95, 1)
        self._page(pno).add_widget(w)

    # ---- sheet manager ----
    SHEET_RE = r"\b[A-Z]{1,3}[-.]?\d{1,3}(?:\.\d{1,2})?\b"

    def sheet_index(self, pattern=None, corner=None):
        """Detect each page's sheet number (largest text matching `pattern`; `corner` optionally restricts the search
        to a visual-coordinate rect such as the title block). Returns [(page, number or None)]."""
        import re
        rx = re.compile(pattern or self.SHEET_RE)
        out = []
        for i, pg in enumerate(self.doc):
            best = (0.0, None)
            for b in pg.get_text("dict")["blocks"]:
                for ln in b.get("lines", []):
                    for sp in ln["spans"]:
                        t = sp["text"].strip()
                        if corner is not None and not fitz.Rect(corner).intersects(self._rv(i, fitz.Rect(sp["bbox"]))):
                            continue
                        if rx.fullmatch(t) and sp["size"] > best[0]:
                            best = (sp["size"], t)
            out.append((i, best[1]))
        return out

    @mutates
    def auto_bookmarks(self, pattern=None, corner=None):
        """Replace bookmarks with one per detected sheet number. Returns how many were created."""
        toc = [[1, num, i + 1] for i, num in self.sheet_index(pattern, corner) if num]
        self.doc.set_toc(toc)
        return len(toc)

    @mutates
    def auto_hyperlinks(self, pattern=None, corner=None):
        """Turn every whole-word mention of another sheet's number (e.g. 'see A-102') into a link to that sheet."""
        sheets = {num: i for i, num in self.sheet_index(pattern, corner) if num}
        made = 0
        for i in range(len(self.doc)):
            pg = self._page(i)
            for x0, y0, x1, y1, word, *_ in pg.get_text("words"):
                num = word.strip(".,;:()[]")
                target = sheets.get(num)
                if target is not None and target != i:
                    pg.insert_link({"kind": fitz.LINK_GOTO, "from": fitz.Rect(x0, y0, x1, y1), "page": target})
                    made += 1
        return made

    # ---- layers ----
    def layers(self):
        return [{**c, "on": bool(c["on"])} for c in self.doc.layer_ui_configs()]

    @mutates
    def set_layer(self, number, on):
        self.doc.set_layer_ui_config(number, action=0 if on else 1)

    # ---- images / thumbnails ----
    def thumbnail(self, pno, width=120):
        pg = self._page(pno)
        z = width / pg.rect.width
        return pg.get_pixmap(matrix=fitz.Matrix(z, z), annots=True)

    def export_png(self, pno, path, dpi=150, clip=None):
        self._page(pno).get_pixmap(dpi=dpi, annots=True, clip=clip).save(path)
        return path

    # ---- signatures (visual) ----
    @mutates
    def add_signature_text(self, pno, rect, name=None):
        name = name or self.author
        sig = fitz.open()
        sp = sig.new_page(width=300, height=90)
        sp.insert_textbox(fitz.Rect(5, 5, 295, 50), name, fontsize=30, fontname="heit", color=(0, 0, 0.6))
        sp.insert_textbox(fitz.Rect(5, 55, 295, 88), f"Digitally placed {datetime.date.today().isoformat()}",
                          fontsize=11, color=(0.3, 0.3, 0.3))
        sp.set_rotation((360 - self._page(pno).rotation) % 360)
        a = self._page(pno).add_stamp_annot(self._ru(pno, rect), stamp=sp.get_pixmap(dpi=144))
        a.set_info(title=self.author, subject="Signature", content=name)
        a.update()
        return a


def compare_pdfs(old_path, new_path, out_path, dpi=100):
    """Overlay comparison by page position. Returns the share of each page's area that differs.
    (For sheet matching, tolerance, and text differences use openrevu.compare.compare_documents.)"""
    from .compare import compare_documents
    return [r.area_fraction for r in compare_documents(old_path, new_path, out_path, match="page", dpi=dpi, tolerance=0)]
