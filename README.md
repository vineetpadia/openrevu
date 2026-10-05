# OpenRevu

A free, open-source PDF markup and quantity-takeoff tool, inspired by Bluebeam Revu.
Built with PyMuPDF and PyQt5. Markups are standard PDF annotations, so they open in other viewers.

## Features
- Markup tools: rectangle, ellipse, line, arrow, revision cloud, freehand pen, text-box highlight, text box, sticky note
- Scale calibration (drag across a known dimension; stored in the PDF and restored on reopen)
- Takeoff: polyline length, polygon area and counts, with per-type running totals
- Markups list panel (jump to, delete) and CSV export
- Zoom (Ctrl+wheel), page navigation, colour picker, save / save-as

## Run
```
pip install -e .[test]
openrevu file.pdf        # or: python -m openrevu file.pdf
pytest                   # tests run headless (QT_QPA_PLATFORM=offscreen is set automatically)
```
Length/Area: click vertices, double-click to finish. Calibrate first for real-world units.

## Not (yet) implemented
Studio-style batch/OCR tools, tool chest, layers, digital signatures, real-time collaboration, markup editing/moving after placement.

## License
AGPL-3.0-or-later (required by the PyMuPDF dependency). Not affiliated with Bluebeam.
