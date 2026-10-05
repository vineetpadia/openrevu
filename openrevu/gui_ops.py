"""Document-level operations (pages, headers, redaction, OCR, signing...) used by the main window."""
from __future__ import annotations

import fitz
from PyQt5 import QtCore, QtWidgets as W

from .core import Scale
from .pages import compare_pdfs

ERRORS = (ValueError, IndexError, RuntimeError, PermissionError, OSError, KeyError)


def parse_pages(text: str, n: int) -> list[int]:
    """'1-3,5' -> [0,1,2,4] (1-based input, validated against n)."""
    out: list[int] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(part)
        if not 1 <= lo <= hi <= n:
            raise ValueError(f"page range {part!r} outside 1-{n}")
        out += range(lo - 1, hi)
    if not out:
        raise ValueError("no pages given")
    return list(dict.fromkeys(out))


class DocumentOps:
    """Mixed into Main. Expects: self.cv, self.doc, self._run, self._need_doc, refresh_* and page widgets."""

    # ---------- document operations ----------
    def _ask_pages(self, title, default=""):
        t, ok = W.QInputDialog.getText(self, title, f"Pages (e.g. 1-3,5) of {self.doc.page_count}:", text=default)
        if not ok:
            return None
        try:
            return parse_pages(t, self.doc.page_count)
        except ValueError as e:
            W.QMessageBox.warning(self, "OpenRevu", str(e))
            return None

    def _structural(self, fn, *a, **kw):
        if self._need_doc() and self._run(fn, *a, structural=True, **kw):
            self.refresh_thumbs()
            self.refresh_toc()
            self.page_spin.setMaximum(max(1, self.doc.page_count))
            self.page_lbl.setText(f" / {self.doc.page_count} ")

    def insert_blank(self):
        if self._need_doc():
            at, ok = W.QInputDialog.getInt(self, "Insert blank", "Insert before page:", self.cv.current_page() + 1, 1, self.doc.page_count + 1)
            if ok:
                self._structural(self.doc.insert_blank, at - 1)

    def insert_pdf(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getOpenFileName(self, "Insert PDF", "", "PDF (*.pdf)")
            if p:
                at, ok = W.QInputDialog.getInt(self, "Insert", "Insert before page:", self.doc.page_count + 1, 1, self.doc.page_count + 1)
                if ok:
                    self._structural(self.doc.insert_pdf, p, at - 1)

    def insert_image(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getOpenFileName(self, "Insert image", "", "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")
            if p:
                self._structural(self.doc.insert_image_page, p)

    def delete_pages(self):
        if self._need_doc():
            pg = self._ask_pages("Delete pages", str(self.cv.current_page() + 1))
            if pg:
                self._structural(self.doc.delete_pages, pg)

    def rotate_pages(self):
        if self._need_doc():
            pg = self._ask_pages("Rotate pages", f"1-{self.doc.page_count}")
            if pg:
                deg, ok = W.QInputDialog.getItem(self, "Rotate", "Degrees clockwise:", ["90", "180", "270"], 0, False)
                if ok:
                    self._structural(self.doc.rotate_pages, pg, int(deg))

    def move_page(self):
        if self._need_doc():
            n = self.doc.page_count
            a, ok = W.QInputDialog.getInt(self, "Move page", "Move page:", self.cv.current_page() + 1, 1, n)
            if ok:
                b, ok = W.QInputDialog.getInt(self, "Move page", "To position:", 1, 1, n)
                if ok:
                    self._structural(self.doc.move_page, a - 1, b - 1)

    def extract_pages(self):
        if self._need_doc():
            pg = self._ask_pages("Extract pages")
            if pg:
                p, _ = W.QFileDialog.getSaveFileName(self, "Save extracted pages", "", "PDF (*.pdf)")
                if p:
                    self._run(self.doc.extract_pages, pg, p, msg=f"Extracted {len(pg)} page(s)")

    def split_doc(self):
        if self._need_doc():
            n, ok = W.QInputDialog.getInt(self, "Split", "Pages per file:", 1, 1, self.doc.page_count)
            if ok:
                d = W.QFileDialog.getExistingDirectory(self, "Output folder")
                if d:
                    r = self._run(self.doc.split, d, every=n)
                    if r:
                        self.statusBar().showMessage(f"Wrote {len(r)} files")

    def crop_page(self):
        if self._need_doc():
            pno = self.cv.current_page()
            r = self.doc.doc[pno].rect
            t, ok = W.QInputDialog.getText(self, "Crop", "x0,y0,x1,y1 in points:", text=f"{r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f}")
            if ok:
                try:
                    box = [float(v) for v in t.split(",")]
                    assert len(box) == 4
                except (ValueError, AssertionError):
                    return W.QMessageBox.warning(self, "OpenRevu", "Enter four numbers separated by commas")
                self._structural(self.doc.crop_page, pno, fitz.Rect(*box))

    def page_labels(self):
        if self._need_doc():
            t, ok = W.QInputDialog.getText(self, "Page labels", "Prefix (e.g. A-):")
            if ok:
                self._run(self.doc.set_page_labels, [{"startpage": 0, "prefix": t, "style": "D", "firstpagenum": 1}])
                self.refresh_thumbs()

    def _form_dialog(self, title, fields):
        dlg = W.QDialog(self)
        dlg.setWindowTitle(title)
        form = W.QFormLayout(dlg)
        edits = {}
        for name, default in fields:
            edits[name] = W.QLineEdit(default)
            form.addRow(name, edits[name])
        bb = W.QDialogButtonBox(W.QDialogButtonBox.Ok | W.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        return {k: e.text() for k, e in edits.items()} if dlg.exec_() == W.QDialog.Accepted else None

    def header_footer(self):
        if not self._need_doc():
            return
        v = self._form_dialog("Header / footer — tokens {page} {pages} {date}", [
            ("Header left", ""), ("Header centre", ""), ("Header right", ""),
            ("Footer left", ""), ("Footer centre", "Page {page} of {pages}"), ("Footer right", "")])
        if v:
            self._run(self.doc.header_footer, header=(v["Header left"], v["Header centre"], v["Header right"]),
                      footer=(v["Footer left"], v["Footer centre"], v["Footer right"]), name=self.doc.author)

    def watermark(self):
        if self._need_doc():
            t, ok = W.QInputDialog.getText(self, "Watermark", "Text:", text="DRAFT")
            if ok and t:
                self._run(self.doc.watermark, t)

    def watermark_image(self):
        if self._need_doc():
            p, _ = W.QFileDialog.getOpenFileName(self, "Watermark image", "", "Images (*.png *.jpg *.jpeg)")
            if p:
                self._run(self.doc.watermark_image, p)

    def bates(self):
        if self._need_doc():
            v = self._form_dialog("Bates numbering", [("Prefix", ""), ("Start", "1"), ("Digits", "6")])
            if v:
                try:
                    self._run(self.doc.bates, v["Prefix"], int(v["Start"]), int(v["Digits"]))
                except ValueError:
                    W.QMessageBox.warning(self, "OpenRevu", "Start and digits must be whole numbers")

    def flatten(self):
        if self._need_doc():
            if W.QMessageBox.question(self, "Flatten", "Bake all markups into the pages? (Undo is available.)") == W.QMessageBox.Yes:
                self._run(self.doc.flatten, structural=True)

    def apply_redactions(self):
        if self._need_doc():
            if W.QMessageBox.question(self, "Apply redactions", "Permanently remove content under all redaction marks?") == W.QMessageBox.Yes:
                n = self._run(self.doc.apply_redactions, structural=True)
                self.statusBar().showMessage(f"{n or 0} redaction(s) applied")

    def redact_text(self):
        if self._need_doc():
            t, ok = W.QInputDialog.getText(self, "Redact text", "Mark every match of:")
            if ok and t:
                n = self._run(self.doc.mark_redaction_text, t)
                self.statusBar().showMessage(f"{n or 0} match(es) marked — apply with Document ▸ Apply redactions")

    def ocr(self):
        if self._need_doc():
            n = self._run(self.doc.ocr, structural=True)
            if n is not None:
                self.statusBar().showMessage(f"OCR added a text layer to {n} page(s)")

    def auto_bookmarks(self):
        if self._need_doc():
            n = self._run(self.doc.auto_bookmarks)
            if n is not None:
                self.refresh_toc()
                self.statusBar().showMessage(f"{n} sheet bookmark(s) created" if n else "No sheet numbers found")

    def auto_links(self):
        if self._need_doc():
            n = self._run(self.doc.auto_hyperlinks)
            if n is not None:
                self.statusBar().showMessage(f"{n} sheet reference link(s) created")

    def metadata(self):
        if self._need_doc():
            md = self.doc.doc.metadata or {}
            v = self._form_dialog("Document properties", [(k.title(), md.get(k) or "") for k in ("title", "author", "subject", "keywords")])
            if v:
                keep = (md.get("keywords") or "").split(";")
                hidden = [p for p in keep if p.startswith("OpenRevu-scale")]
                kw = v["Keywords"]
                self._run(self.doc.set_metadata, title=v["Title"], author=v["Author"], subject=v["Subject"],
                          keywords=";".join(([kw] if kw and "OpenRevu-scale" not in kw else []) + hidden))

    def compare(self):
        if not self._need_doc():
            return
        if not self.doc.path:
            return W.QMessageBox.information(self, "Compare", "Save the document first.")
        other, _ = W.QFileDialog.getOpenFileName(self, "Compare with (new version)", "", "PDF (*.pdf)")
        if not other:
            return
        out, _ = W.QFileDialog.getSaveFileName(self, "Save comparison as", "comparison.pdf", "PDF (*.pdf)")
        if out:
            try:
                stats = compare_pdfs(self.doc.path, other, out)
            except ERRORS as e:
                return W.QMessageBox.warning(self, "Compare", str(e))
            self.open(out)
            self.statusBar().showMessage("Changed: " + ", ".join(f"p{i + 1} {s:.1%}" for i, s in enumerate(stats) if s))

    def fill_form(self):
        if not self._need_doc():
            return
        fields = self.doc.form_fields()
        if not fields:
            return self.statusBar().showMessage("No form fields in this PDF")
        names = sorted({f["name"] for f in fields})
        n, ok = W.QInputDialog.getItem(self, "Form field", "Field:", names, 0, False)
        if ok:
            cur = next(f["value"] for f in fields if f["name"] == n)
            v, ok = W.QInputDialog.getText(self, n, "Value:", text=str(cur or ""))
            if ok:
                self._run(self.doc.set_form_value, n, v)

    def layers_dialog(self):
        if not self._need_doc():
            return
        ls = self.doc.layers()
        if not ls:
            return self.statusBar().showMessage("No layers in this PDF")
        dlg = W.QDialog(self)
        dlg.setWindowTitle("Layers")
        lay = W.QVBoxLayout(dlg)
        boxes = []
        for l in ls:
            cb = W.QCheckBox(l["text"] or f"Layer {l['number']}")
            cb.setChecked(l["on"])
            lay.addWidget(cb)
            boxes.append((l["number"], cb))
        bb = W.QDialogButtonBox(W.QDialogButtonBox.Ok | W.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec_() == W.QDialog.Accepted:
            for num, cb in boxes:
                self._run(self.doc.set_layer, num, cb.isChecked(), structural=True)

    # ---------- measure / sign ----------
    def scale_ratio(self):
        if not self._need_doc():
            return
        v = self._form_dialog("Scale ratio (paper : real)", [("Paper inches", "0.25"), ("Real length", "1"), ("Unit", "ft")])
        if not v:
            return
        scope, ok = W.QInputDialog.getItem(self, "Scale", "Apply to:", ["This page", "All pages"], 0, False)
        if not ok:
            return
        try:
            sc = Scale.from_ratio(float(v["Paper inches"]), float(v["Real length"]), v["Unit"])
        except ValueError as e:
            return W.QMessageBox.warning(self, "OpenRevu", str(e) or "Invalid numbers")
        if scope == "All pages":
            self.doc.set_scale(sc, reset_pages=True)
        else:
            self.doc.set_scale(sc, page=self.cv.current_page())
        self.statusBar().showMessage("Scale set")

    def show_scales(self):
        if self._need_doc():
            d = self.doc
            lines = [f"Default: 1 pt = {d.default_scale.unit_per_pt:.6g} {d.default_scale.unit}"]
            lines += [f"Page {p + 1}: 1 pt = {s.unit_per_pt:.6g} {s.unit}" for p, s in sorted(d.page_scales.items())]
            W.QMessageBox.information(self, "Scales", "\n".join(lines))

    def show_summary(self):
        if not self._need_doc():
            return
        rows = sorted(self.doc.takeoff_by_subject().items())
        dlg = W.QDialog(self)
        dlg.setWindowTitle("Measurement summary")
        dlg.resize(560, 360)
        lay = W.QVBoxLayout(dlg)
        t = W.QTableWidget(len(rows), 5)
        t.setHorizontalHeaderLabels(["Subject", "Measure", "Items", "Total", "Unit"])
        for i, ((s, k, u), (n, tot)) in enumerate(rows):
            for c, v in enumerate((s, k, str(n), f"{tot:.3f}", u)):
                t.setItem(i, c, W.QTableWidgetItem(v))
        lay.addWidget(t)
        b = W.QPushButton("Export CSV…")
        b.clicked.connect(lambda: self._export("summary"))
        lay.addWidget(b)
        dlg.exec_()

    def digital_sign(self):
        if not self._need_doc():
            return
        if not self.doc.path or self.doc.modified:
            return W.QMessageBox.information(self, "Sign", "Save the document first; signing works on the saved file.")
        p12, _ = W.QFileDialog.getOpenFileName(self, "Certificate (.p12/.pfx)", "", "PKCS#12 (*.p12 *.pfx)")
        if not p12:
            return
        pw, ok = W.QInputDialog.getText(self, "Certificate", "Passphrase:", W.QLineEdit.Password)
        if not ok:
            return
        out, _ = W.QFileDialog.getSaveFileName(self, "Signed copy", "signed.pdf", "PDF (*.pdf)")
        if out:
            from .sign import sign_pdf
            try:
                sign_pdf(self.doc.path, out, p12, pw, page=self.cv.current_page())
            except Exception as e:  # pyhanko raises many types
                return W.QMessageBox.warning(self, "Sign", str(e))
            self.open(out)

    def verify_signatures(self):
        if not self._need_doc() or not self.doc.path:
            return
        from .sign import verify_signatures
        roots, _ = W.QFileDialog.getOpenFileNames(self, "Trusted certificates (optional — Cancel for none)", "",
                                                  "Certificates (*.pem *.crt *.cer *.der)")
        try:
            sigs = verify_signatures(self.doc.path, roots)
        except Exception as e:
            return W.QMessageBox.warning(self, "Signatures", str(e))
        W.QMessageBox.information(self, "Signatures", "\n".join(
            f"{s['field']}: {s['signer']} — " + ("VALID and TRUSTED" if s["verdict_ok"] else
            f"NOT VERIFIED ({'intact' if s['intact'] else 'MODIFIED'}, {'trusted' if s['trusted'] else 'untrusted'}, "
            f"{'unchanged since signing' if s['whole_file'] else 'EDITED AFTER SIGNING'})") for s in sigs) or "No signatures")
