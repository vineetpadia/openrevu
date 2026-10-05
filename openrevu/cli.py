"""Batch / headless command line: openrevu-cli <command> ..."""
from __future__ import annotations

import argparse
import sys

from .core import Document
from .pages import compare_pdfs


def _doc(a):
    return Document(a.input, a.in_password)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="openrevu-cli", description="Batch PDF operations")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_, *args):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("input")
        sp.add_argument("--in-password", default=None, help="password of the input PDF, if encrypted")
        for a in args:
            sp.add_argument(*a[0], **a[1])
        return sp

    out = (("output",), {})
    add("merge", "merge PDFs", out).add_argument("more", nargs="+")
    add("split", "split into N-page files", (("output",), {}), (("--every",), {"type": int, "default": 1}))
    add("watermark", "text watermark", out, (("--text",), {"required": True}))
    add("flatten", "bake annotations into page content", out)
    add("optimize", "compress", out)
    add("redact", "permanently redact text matches", out, (("--text",), {"required": True}))
    add("ocr", "add text layer (needs tesseract)", out, (("--lang",), {"default": "eng"}))
    add("encrypt", "AES-256 password", out, (("--password",), {"required": True}))
    add("csv", "export markups CSV", (("output",), {}))
    add("summary", "export measurement summary CSV", (("output",), {}))
    add("compare", "compare two PDFs (overlay PDF, text changes)", (("new",), {}), out, (("--match",), {"default": "sheet", "choices": ["sheet", "page"]}), (("--csv",), {"default": None}), (("--new-password",), {"default": None}))
    add("numbering", "Bates numbering", out, (("--prefix",), {"default": ""}), (("--start",), {"type": int, "default": 1}))
    add("run", "run a Python script with `doc` (an openrevu Document) preloaded; saves to output", out) \
        .add_argument("script")
    add("pdfa", "export a PDF/A copy (needs Ghostscript; checked with veraPDF if installed)", out, (("--level",), {"default": "2b", "choices": ["1b", "2b", "3b"]}))
    add("sheets", "auto-bookmark sheet numbers and link sheet references", out)
    add("footer", "page x of y footer", out)
    a = p.parse_args(argv)
    try:
        if a.cmd == "compare":
            from .compare import compare_documents, summarize, write_csv
            res = compare_documents(a.input, a.new, a.output, match=a.match, old_password=a.in_password, new_password=a.new_password)
            for r in res:
                extra = f", {r.changed_fraction:.1%} of the drawing, +{r.words_added}/-{r.words_removed} words" if r.status == "changed" else ""
                print(f"{r.number}: {r.status}{extra}")
            print(summarize(res))
            if a.csv:
                write_csv(res, a.csv)
            return 0
        d = _doc(a)
        if a.cmd == "merge":
            for m in a.more:
                d.insert_pdf(m)
            d.save(a.output)
        elif a.cmd == "split":
            print("\n".join(d.split(a.output, every=a.every)))
        elif a.cmd == "watermark":
            d.watermark(a.text); d.save(a.output)
        elif a.cmd == "flatten":
            d.flatten(); d.save(a.output)
        elif a.cmd == "optimize":
            print("%d -> %d bytes" % d.optimize(a.output))
        elif a.cmd == "redact":
            n = d.mark_redaction_text(a.text); d.apply_redactions(); d.save(a.output); print(f"{n} redactions")
        elif a.cmd == "ocr":
            print(f"{d.ocr(a.lang)} pages OCRed"); d.save(a.output)
        elif a.cmd == "encrypt":
            d.save_encrypted(a.output, a.password)
        elif a.cmd == "csv":
            d.export_csv(a.output)
        elif a.cmd == "summary":
            d.export_summary_csv(a.output)
        elif a.cmd == "numbering":
            d.bates(a.prefix, a.start); d.save(a.output)
        elif a.cmd == "pdfa":
            rep = d.export_pdfa(a.output, a.level)
            print(rep.summary())
            if rep.validated and not rep.compliant:
                return 2
        elif a.cmd == "run":
            with open(a.script) as fh:
                code = compile(fh.read(), a.script, "exec")
            exec(code, {"doc": d, "__name__": "__openrevu_script__"})  # noqa: S102 - user's own script, by design
            d.save(a.output)
        elif a.cmd == "sheets":
            print(f"{d.auto_bookmarks()} bookmarks, {d.auto_hyperlinks()} links"); d.save(a.output)
        elif a.cmd == "footer":
            d.header_footer(footer=("", "Page {page} of {pages}", "")); d.save(a.output)
    except (RuntimeError, OSError, ValueError, PermissionError, IndexError, KeyError, SyntaxError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
