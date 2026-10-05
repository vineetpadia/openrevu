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
OCR needs the `tesseract` binary on PATH (tested with 5.5); room fill needs `numpy` + `scipy` (`pip install openrevu[fill]`).

## What's implemented
| Area | Features |
|---|---|
| Viewing | tabs, continuous scroll, zoom/fit-width, thumbnails, bookmarks, text search with highlights, drag-and-drop open, password-protected files, print |
| Markup | rectangle, ellipse, line, arrow, polyline, revision cloud, freehand, highlight / underline / strikeout / squiggly, text box, callout, note, custom stamps (name + date), image stamps, visual signature, redaction marks |
| Editing | select, move, resize, delete, copy/paste/duplicate, properties panel (colour, fill, width, opacity, subject, comment), undo/redo (30 levels) |
| Review | status (Accepted/Rejected/…), threaded replies, author, sortable/filterable Markups List, CSV export |
| Measurement | scale by calibration or ratio, per-page scales (persisted in the PDF), length, polylength, perimeter, area (with cut-outs), **Dynamic-Fill-style room area (click inside a room; columns become cut-outs)**, rectangle/ellipse area, volume (area × depth), diameter, angle, counts by group, resize-and-re-measure, measurement summary + CSV |
| Tool Chest | save any markup as a reusable preset (JSON), place it anywhere |
| Pages | insert blank/PDF/image, delete, rotate, move, extract, split, crop, page labels, merge |
| Document | header/footer with `{page}` `{pages}` `{date}`, text & image watermark, Bates numbering, flatten, permanent redaction (text, images, line art), OCR text layer, metadata, forms (create / list / fill), layers (toggle), overlay comparison, optimize, AES-256 encryption |
| Signing | cryptographic PKCS#12 signatures + verification with your own trust roots (pyhanko) |
| Batch | `openrevu-cli`: merge, split, watermark, flatten, optimize, redact, ocr, encrypt, csv, summary, compare, numbering, footer |

## Known gaps vs. Bluebeam Revu (be honest before depending on it)
- **No real-time collaboration** (Studio Sessions / Projects) or cloud storage. This needs a server and is out of scope
  for a local app; share files and use the Markups List CSV / status / replies for review rounds.
- No viewport-based scales (one scale per page), no `.bpx`/`.bax` import (Tool Chest uses JSON).
- Revu's proprietary features not replicated: scripting, PDF/A conversion, Sheet Manager / hyperlink auto-generation.
- Overlay compare is pixel-based (no semantic/text diff) and slow on large sheets.
- Dynamic Fill needs a closed boundary at the render resolution (gaps leak and are reported); it traces the room outline,
  so curved walls become many short segments.
- Measurements that depend on extra geometry (angle, count, areas with cut-outs) can't be resized; delete and re-measure.
- Undo history is kept in memory (encrypted for password-protected documents); closing the tab discards it.
- Cryptographic signatures are validated against trust roots you supply (no OS trust store, no revocation checks).
- Developed and tested on Linux only; Windows/macOS should work (Qt + PyMuPDF are cross-platform) but are unverified.
- No human-factors testing yet — expect rough edges in the GUI.

## Licence
AGPL-3.0-or-later (PyMuPDF is AGPL). Contributions welcome.
