# OpenRevu

A free, open-source PDF markup, measurement (takeoff) and review tool for Linux, macOS and Windows,
modelled on the everyday workflow of Bluebeam Revu. Markups are standard PDF annotations, so files
round-trip with other viewers; OpenRevu-specific data (measurement values, scales, review status)
is stored in custom keys that other viewers simply ignore.

Not affiliated with or endorsed by Bluebeam, Inc.

## Install & run
```
pip install -e .[test]     # PyMuPDF + PyQt5 (+ pyhanko for digital signatures)
openrevu file.pdf [more.pdf]
openrevu-cli --help        # headless batch operations
pytest                     # headless; sets QT_QPA_PLATFORM=offscreen
```
OCR additionally needs the `tesseract` binary on PATH.

## What's implemented
| Area | Features |
|---|---|
| Viewing | tabs, continuous scroll, zoom/fit-width, thumbnails, bookmarks, text search with highlights, drag-and-drop open, password-protected files, print |
| Markup | rectangle, ellipse, line, arrow, polyline, revision cloud, freehand, highlight / underline / strikeout / squiggly, text box, callout, note, custom stamps (name + date), image stamps, visual signature, redaction marks |
| Editing | select, move, resize, delete, copy/paste/duplicate, properties panel (colour, fill, width, opacity, subject, comment), undo/redo (30 levels) |
| Review | status (Accepted/Rejected/…), threaded replies, author, sortable/filterable Markups List, CSV export |
| Measurement | scale by calibration or ratio, per-page scales (persisted in the PDF), length, polylength, perimeter, area (with cut-outs), rectangle/ellipse area, volume (area × depth), diameter, angle, counts by group, measurement summary + CSV |
| Tool Chest | save any markup as a reusable preset (JSON), place it anywhere |
| Pages | insert blank/PDF/image, delete, rotate, move, extract, split, crop, page labels, merge |
| Document | header/footer with `{page}` `{pages}` `{date}`, text & image watermark, Bates numbering, flatten, permanent redaction (text, images, line art), OCR text layer, metadata, forms (list/fill), layers (toggle), overlay comparison, optimize, AES-256 encryption |
| Signing | cryptographic PKCS#12 signatures + integrity verification (pyhanko) |
| Batch | `openrevu-cli`: merge, split, watermark, flatten, optimize, redact, ocr, encrypt, csv, summary, compare, numbering, footer |

## Known gaps vs. Bluebeam Revu (be honest before depending on it)
- No real-time collaboration (Studio Sessions / Projects) or cloud storage.
- No Dynamic Fill, viewport-based scales, or Revu's `.bpx` tool-chest / `.bax` formats (OpenRevu uses JSON).
- Highlight/underline-type text markups can't be moved (re-create them instead); measurements can't be resized (re-measure).
- No scripted/automatic text-flow editing of PDF content, no form *creation*, no PDF/A conversion, no VBA-style scripting.
- Overlay compare is pixel-based (no semantic/text diff) and slow on large sheets.
- Cryptographic signatures are verified for integrity only, not against a trust store.
- Undo history is kept in memory as unencrypted copies of the document; closing the tab discards it.
- Tested on Linux only; Windows/macOS should work (Qt + PyMuPDF are cross-platform) but are unverified.

## Licence
AGPL-3.0-or-later (PyMuPDF is AGPL). Contributions welcome.
