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
        src = fitz.open(path)
        if src.needs_pass and not src.authenticate(password or ""):
            raise PermissionError("source PDF is encrypted")
        at = len(self.doc) if at is None else at
        n, k = len(self.doc), (len(pages) if pages is not None else len(src))
        kw = {"start_at": at}
        if pages is not None:
            for p in pages:
                self.doc.insert_pdf(src, from_page=p, to_page=p, start_at=at)
                at += 1
        else:
            self.doc.insert_pdf(src, **kw)
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
        out = fitz.open()
        for p in pnos:
            out.insert_pdf(self.doc, from_page=p, to_page=p)
        out.save(out_path, garbage=3, deflate=True)
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
        return fitz.open("pdf", self.doc.tobytes())[pno].get_links()

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
        return shutil.which("tesseract") is not None

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

    def optimize(self, path):
        """Save with maximum structural compression; returns (before, after) byte sizes."""
        self._store_scales()
        before = len(self.doc.tobytes())
        self.doc.save(path, garbage=4, clean=True, deflate=True, deflate_images=True, deflate_fonts=True)
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
    """Overlay comparison: pixels only in old = red, only in new = blue, shared = grey.
    Returns [changed_fraction per page]; writes a PDF of the overlays."""
    a, b = fitz.open(old_path), fitz.open(new_path)
    out, stats = fitz.open(), []
    for i in range(max(len(a), len(b))):
        pa = a[i].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY) if i < len(a) else None
        pb = b[i].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY) if i < len(b) else None
        w = max(p.width for p in (pa, pb) if p)
        h = max(p.height for p in (pa, pb) if p)

        def grid(p):
            g = bytearray(b"\xff" * (w * h))
            if p:
                for y in range(p.height):
                    g[y * w:y * w + p.width] = p.samples[y * p.width:(y + 1) * p.width]
            return g

        ga, gb = grid(pa), grid(pb)
        rgb = bytearray(w * h * 3)
        changed = 0
        for k in range(w * h):
            da, db = ga[k] < 128, gb[k] < 128
            if da and db:
                c = (110, 110, 110)
            elif da:
                c, changed = (230, 40, 40), changed + 1
            elif db:
                c, changed = (40, 80, 230), changed + 1
            else:
                c = (255, 255, 255)
            rgb[3 * k:3 * k + 3] = bytes(c)
        pix = fitz.Pixmap(fitz.csRGB, w, h, bytes(rgb), False)
        pg = out.new_page(width=w * 72 / dpi, height=h * 72 / dpi)
        pg.insert_image(pg.rect, pixmap=pix)
        stats.append(changed / (w * h))
    out.save(out_path, garbage=3, deflate=True)
    return stats
