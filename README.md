# OpenRevu

OpenRevu is a free PDF tool. You use it to mark up drawings, measure quantities, and review documents.
It runs on Linux, macOS, and Windows. Its workflow follows Bluebeam Revu.

OpenRevu is not connected to Bluebeam, Inc.

![OpenRevu main window with markups and measurements](docs/main-window.png)

## Status

OpenRevu is a beta. It is not a full replacement for Bluebeam Revu.
Read the section "Known limits" before you use it for important work.

- The automatic tests pass on Linux, macOS, and Windows.
- Nobody has tested the window interactively on macOS or Windows.
- Test the software on your own files before you depend on it.

## Install

You need Python 3.9 or later.

```
pip install -e .[test]
```

This command installs PyMuPDF and PyQt5. It also installs the optional packages for signatures and room fill.

Some functions need extra software:

| Function | What you need |
|---|---|
| OCR | The `tesseract` program on your PATH. Tested with version 5.5. |
| Room fill | `numpy` and `scipy`. Install them with `pip install openrevu[fill]`. |
| Digital signatures | `pyhanko`. Install it with `pip install openrevu[sign]`. |

## Start

Open the window:

```
openrevu drawing.pdf
```

You can open more than one file. Each file opens in its own tab.
You can also drag a PDF onto the window.

Run a batch command without the window:

```
openrevu-cli --help
```

Run the tests:

```
pytest
```

The tests do not open a visible window.

## Basic use

1. Open a PDF.
2. Pick a tool from the toolbar.
3. Click or drag on the page.
4. Pick the **Select** tool to change a markup. Click a markup to select it. Drag it to move it.
5. Press **Ctrl+S** to save.

Press **Ctrl+Z** to undo and **Ctrl+Y** to redo. OpenRevu keeps the last 30 steps.

### Measure a drawing

1. Pick **Calibrate**. Drag across a dimension that you know.
2. Enter the real length and the unit.
3. Pick **Length**, **Area**, or another measure tool.
4. For a polyline or a polygon, click each point. Double-click to finish.
5. Open **Measurement summary** to see the totals.

You can also set the scale as a ratio. Use the menu **Measure > Set scale by ratio**.

To measure a room, pick **Fill** and click inside the room. OpenRevu finds the walls.
It removes columns and other solid objects from the area.
The walls must form a closed shape. If there is a gap, OpenRevu shows an error.

![Markups, bookmarks, and the page list](docs/bookmarks-and-page2.png)

![Measurement summary](docs/measurement-summary.png)

## Features

### View

- Tabs for many documents
- Continuous scroll, zoom, and fit to width
- Page thumbnails and bookmarks
- Text search with highlighted results
- Password-protected files
- Printing

### Markup

- Rectangle, ellipse, line, arrow, polyline, and revision cloud
- Freehand pen
- Highlight, underline, strikeout, and squiggly underline
- Text box, callout, and note
- Stamps with name and date, and image stamps
- Visual signature
- Redaction marks

A markup is a standard PDF annotation. Other PDF viewers can show it.
OpenRevu stores its own data (measurement values, scales, and review status) in custom keys.
Other viewers ignore these keys.

### Edit

- Select, move, resize, and delete a markup
- Copy, paste, and duplicate
- Change color, fill, width, opacity, subject, and comment in the **Properties** panel
- Undo and redo

### Review

- Set a status: Accepted, Rejected, Cancelled, Completed, or Reviewed
- Add replies to a markup
- Sort and filter the **Markups List**
- Export the list to a CSV file

### Measure

- Scale by calibration or by ratio
- A separate scale for each page
- A separate scale for one area of a page (a viewport)
- Length, polyline length, perimeter, and area
- Area with cut-outs, rectangle area, and ellipse area
- Room area with the **Fill** tool
- Volume (area multiplied by depth)
- Diameter and angle
- Counts, grouped by name
- Resize a measurement. OpenRevu measures it again.
- Measurement summary and CSV export

### Tool Chest

Save a markup as a preset. Place the preset on any page later. OpenRevu stores presets in a JSON file.

### Sheets

OpenRevu finds the sheet number on each page. It can then:

- Make a bookmark for each sheet
- Make a link from each sheet reference (for example "see A-102") to the correct page

The command `openrevu-cli sheets` does the same in a batch.

### Pages

- Insert a blank page, a PDF, or an image
- Delete, rotate, move, and crop pages
- Extract pages, split a document, and merge documents
- Set page labels

### Document

- Header and footer. You can use `{page}`, `{pages}`, and `{date}`.
- Text watermark and image watermark
- Bates numbers
- Flatten (put markups into the page)
- Permanent redaction of text, images, and line art
- OCR text layer
- Document properties
- Form fields: create, list, and fill
- Show and hide layers
- Overlay comparison of two PDFs
- File size optimization
- AES-256 encryption

### Signatures

- Sign a PDF with a PKCS#12 certificate
- Check a signature against the trusted certificates that you supply

Use the field `verdict_ok` to decide if a signature is good.
It is true only when the signed data is unchanged, the signature is correct,
the certificate is trusted, and nobody changed the file after signing.

### Batch commands

`openrevu-cli` has these commands:

`merge`, `split`, `watermark`, `flatten`, `optimize`, `redact`, `ocr`, `encrypt`, `csv`, `summary`,
`compare`, `numbering`, `footer`, `sheets`, `run`

Use `--in-password` when the input file has a password.

### Scripts

You can write your own automation in Python. The command `run` opens the input file, runs your script, and saves the result to the output file.
The script gets a variable named `doc`.

```
openrevu-cli run input.pdf output.pdf add_counts.py
```

Example script `add_counts.py`:

```python
doc.add_count(0, (100, 200), group="Door")
doc.watermark("DRAFT")
```

You can also import the library:

```python
from openrevu.core import Document

doc = Document("input.pdf")
doc.add_length(0, [(0, 0), (100, 0)])
doc.save("output.pdf")
```

All coordinates are in points (1/72 inch). The origin is the top-left corner of the page as you see it.
This is also true for rotated and cropped pages.

## Known limits

OpenRevu does not have these functions:

- **Real-time collaboration.** Studio Sessions and Projects need a server. OpenRevu works on local files.
  To share a review, send the PDF. Use the status, the replies, and the CSV export.
- **Import of Bluebeam tool files** (`.bpx` and `.bax`). Tool Chest uses JSON.
- **PDF/A conversion.**
- **A sheet-set database or revision tracking.** The Sheets function only finds sheet numbers by pattern.

OpenRevu has these limits:

- Comparison works on pixels. It does not compare text. It is slow on large sheets.
- The **Fill** tool needs a closed boundary. A curved wall becomes many short straight segments.
- You cannot resize some measurements. These are angles, counts, and areas with cut-outs.
  Delete the measurement and measure again.
- OpenRevu keeps the undo history in memory. The history of a password-protected file is encrypted.
  When you close a tab, the history is lost.
- Signature checks use only the certificates that you supply. OpenRevu does not use the system trust store.
  It does not check for revoked certificates.
- Nobody has done a usability study of the window. Some parts can be rough.

## Contribute

Run `pytest` before you send a change. Add a test for each fix or new function.
To make the screenshots again, run `python docs/make_screenshots.py`.

## Licence

OpenRevu uses the AGPL-3.0-or-later licence. The PyMuPDF library requires this licence.
