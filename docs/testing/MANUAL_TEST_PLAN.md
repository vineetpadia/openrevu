# Manual test plan for OpenRevu (for a computer-use agent)

You are testing a desktop program, OpenRevu, by using its window like a person does:
look at the screen, click, drag, type, and judge what you see. The automatic tests (301 of them) already pass,
but **nobody has ever used the window by hand on a real screen**. That is your job. Find what feels wrong,
breaks, looks bad, or confuses a user.

OpenRevu is a free PDF markup, measurement, and review program, modelled on Bluebeam Revu.
Repository: https://github.com/vineetpadia/openrevu

## Ground rules

- **Do not change the source code.** Do not push to the repository. You test; the maintainer fixes.
- Test one thing at a time. After each step, look at the screen. Take a screenshot when something is wrong.
- Judge three things for every test: **Does it work?** (the result is correct) **Does it look right?**
  (nothing overlaps, is cut off, or is unreadable) **Is it clear?** (a new user would understand what to do).
- Write down exactly what you did and what you saw. "It did not work" is not enough.
- If a test needs something that is not installed (OCR, PDF/A), mark it **BLOCKED**, not FAIL.
- Do not report the items in "Known limits" (end of this file) as bugs.

## 1. Setup

You need Python 3.9 or later.

```
git clone https://github.com/vineetpadia/openrevu
cd openrevu
pip install -e .[test]            # installs PyMuPDF, PyQt5, numpy, scipy, pyhanko
python docs/testing/make_samples.py     # makes the sample files in ~/openrevu-test-files
openrevu                              # starts the program (or: python -m openrevu)
```

Check that it works without a window first: `openrevu --version` prints `OpenRevu 0.4.0`,
and `openrevu --selftest` prints `self-test passed: window, markup, save, reopen`.
If these fail, stop and report that. Nothing else will work.

Optional programs (tests that need them say so):

| For | Program | Check with |
|---|---|---|
| OCR (T40) | Tesseract | `tesseract --version` |
| PDF/A (T42) | Ghostscript, and veraPDF to check the result | `gs --version`, `verapdf --version` |

**Make the window large** (at least 1400 x 900). Several panels share the screen.

## 2. The sample files

`python docs/testing/make_samples.py` makes these in `~/openrevu-test-files`. `expected.json` lists the right answers.

| File | What it is |
|---|---|
| `plan_v1.pdf` | 3 sheets: **A-101** and **A-102** (two rooms and a corridor, a 2 ft x 2 ft column, a dimension line) and **S-201** (text only) |
| `plan_v2.pdf` | A later issue: S-201, A-101 (changed), A-103 (new). A-102 is gone. |
| `protected.pdf` | A copy of plan_v1. The password is `test123`. |
| `scanned.pdf` | A page that is only a picture of the text `INVOICE TOTAL 4500` |
| `rotated.pdf` | A sheet with a 90 degree page rotation |
| `many_pages.pdf` | 60 sheets, for speed |
| `form.pdf` | A page with two empty text fields |
| `tools.btx` | A Bluebeam tool set (made to the community description) with 3 usable tools and 1 stamp |

**The scale of the drawings:** 18 points = 1 foot. Every sheet has a dimension line labelled `20'-0"`. It is
360 points long, so you can calibrate the scale with it. Room 1 is about 19.9 ft x 14.9 ft = **296 sq ft**.
The column in it is 2 ft x 2 ft, so the room measured with the Fill tool is **about 291 sq ft** (accept 282 to 300).

## 3. How the window is laid out

| Part | Where | What it has |
|---|---|---|
| Menu bar | top | File, Edit, View, Document, Measure, Sign, Help |
| Main toolbar | first row | Open, Save, Undo, Redo, **Select**, **Pan**, five tool groups, zoom, page buttons |
| Tool groups | in the main toolbar | **Shapes**, **Text & Review**, **Stamp & Sign**, **Insert**, **Measure**. Each is a button with a small arrow. The button shows the last tool you used. The arrow opens the list. |
| Properties toolbar | second row | line colour, fill colour, Width, Opacity, a pin ("Keep tool"). Extra boxes appear for Stamp, Count, and Volume. |
| Left panel | left | five icon tabs (point at one to see its name): **Thumbnails, Bookmarks, Search, Layers, Sheets** |
| Right panel | right | three icon tabs: **Properties, Tool Chest, Measurements** |
| Markups List | bottom | a table of all markups |
| Status bar | bottom | a message, then the page scale, the page number, and the zoom |
| Start page | centre, when no file is open | Open, New from images, recent files |

Tool keys (when the page has focus): **V** select, **H** pan, **R** rectangle, **E** ellipse, **L** line, **A** arrow,
**Y** polyline, **C** cloud, **P** pen, **T** text, **O** callout, **N** note, **I** highlight, **S** stamp,
**M** length, **K** count, **F** room fill.
Other keys: Ctrl+O open, Ctrl+S save, Ctrl+Z undo, Ctrl+Y redo, Ctrl+C / Ctrl+V / Ctrl+D copy / paste / duplicate,
Delete, Ctrl+F search, F3 next result, Ctrl+= / Ctrl+- / Ctrl+0 / Ctrl+9 zoom in / out / fit width / fit page.

**A tool is used once.** After you place a markup, the program goes back to Select. The pin (Keep tool) keeps the tool.
**Count** always stays on.

## 4. The tests

For each test, record **PASS**, **FAIL**, or **BLOCKED**, and notes. "Expect" says what a correct result looks like.

### A. Start, open, close

**T01 Start page.** Start the program with no file.
Expect: a start page with *OpenRevu*, **Open PDF…**, **New from images…**, a recent-files list, and quick-start text.
The side panels are hidden. The Properties toolbar is greyed out.

**T02 Open a file.** Click **Open PDF…** and open `plan_v1.pdf`.
Expect: the file opens in a tab, fitted to the window width. The panels appear. The status bar shows
`Scale: …`, `Page 1 of 3`, and a zoom. Close the file (tab × or Ctrl+W), then check the start page lists `plan_v1.pdf` under
*Recent files*. Double-click it: it opens again.

**T03 Two files.** Open `plan_v1.pdf` and `plan_v2.pdf`. Expect two tabs. Open `plan_v1.pdf` again: the program switches to
its tab and does not make a third. Drag tabs to reorder them.

**T04 Password.** Open `protected.pdf`. Expect a password question. Type a wrong password. Expect it to ask again, or refuse
clearly. Type `test123`. Expect it to open.

**T05 Unsaved changes.** Draw one rectangle. Expect the tab title to start with `*`. Close the tab. Expect a question with
**Save / Discard / Cancel**. Test all three. Cancel must keep the tab. Discard must close it without saving.

### B. Looking at a drawing

**T06 Zoom.** Use the zoom box (type `150`, press Enter), the **+** and **−** buttons, Ctrl+mouse wheel, **Fit width**, and
**Fit page**. Expect the zoom box and the status bar to agree. The page must stay sharp, not blurry.
Type nonsense in the zoom box: expect the old value to come back.

**T07 Pages.** Scroll through the pages. Use the page arrows, the page box (type `3`, Enter), PgUp, PgDown.
Expect the page number in the toolbar and the status bar to follow what you see.

**T08 Thumbnails.** Open the **Thumbnails** tab. Expect a small picture of each page. Click one: the view goes to it.
Right-click a thumbnail: expect rotate, delete, insert-blank, and move options. Try **Rotate clockwise** on page 2,
then Ctrl+Z.

**T09 Search.** Press Ctrl+F. Search for `Rev one`. Expect 3 results listed, with yellow marks on the page.
Click a result: the view goes there. Press F3 for the next result. Search for something that is not there: expect `0 match(es)`.

**T10 Links and bookmarks.** Open `plan_v1.pdf`. In the **Bookmarks** tab click **Auto**: expect bookmarks `A-101`, `A-102`, `S-201`.
Click one: the view goes to that page. Choose the **Link** tool (Insert group), drag a region on page 1, type `3`. Press H (Pan), then click inside that region.
Expect the view to go to page 3 and the status bar to say so. Make another link with the address `https://example.org`:
clicking it must open your browser.

### C. Marking up

**T11 Every shape.** Use each tool in the **Shapes** group: Rectangle, Ellipse, Line, Arrow, Polyline (click points, double-click to finish),
Cloud, Pen (drag freely). Expect a live outline while you drag, and the markup on release.
Expect the tool to go back to Select after each. Expect the toolbar button to show the tool you last used.

**T12 Select, move, resize, delete.** Place a rectangle. With **Select** click it: expect a dashed box and a small square handle at the corner.
Drag it to move it. Drag the handle to resize it. Press Delete. Press Ctrl+Z: it comes back.
Click empty space: the selection goes. Drag empty space with Select: the page pans.

**T13 Properties toolbar.** With nothing selected, change the line colour to blue, the fill to yellow, Width to 5, Opacity to 0.5.
Draw a rectangle: expect these values. Now select an existing rectangle and change the colour: only that rectangle changes, and
the next new rectangle still uses the earlier defaults. Pick **No fill** from the fill menu.

**T14 Text tools.** Use **Text** (drag a box, type text), **Callout** (a box with a leader line), and **Note** (click, type).
Expect your text on the page. Double-click a text box with Select: expect an edit box.

**T15 Text markups.** Use **Highlight**, **Underline**, **Strikeout**, and **Squiggly** over the words `Rev one` on A-101.
Expect the markup to sit exactly on the words. Select one and move it with the mouse: it must move. It cannot be resized (the status bar says why).

**T16 Stamps.** Choose **Stamp**. A box with the stamp text appears in the toolbar: pick `REVIEWED`. Click the page: expect a green
stamp with the text, your user name, and today's date. Drag to make a bigger one. Try the **Signature** tool.
In the **Tool Chest** panel, open the *Stamps* group and click `REJECTED`: the Stamp tool starts with that text.

**T17 Undo and redo.** Place three markups. Press Ctrl+Z three times, then Ctrl+Y twice. Expect each step to be correct.
The Undo button and the Edit menu must agree.

**T18 Copy and paste.** Select a rectangle. Ctrl+C, Ctrl+V: a copy appears slightly offset. Ctrl+D duplicates.
Right-click empty space and choose **Paste here**: the copy lands where you clicked.
Select a **Stamp** and press Ctrl+C: expect a message that it cannot be copied (not an error window).

**T19 Right-click menu.** Right-click a markup. Expect: Properties, Copy, Duplicate, Delete, Set status, Reply…, Add to Tool Chest….
Try **Set status** > Accepted. Try **Reply…**.

**T20 Review.** Select a markup. In the **Properties** tab set Subject, Comment, Status, and press Enter in the reply box.
Expect the **Markups List** (bottom) to show them. Click a row: the markup is selected and the view moves to it.
Use the filter box and the status filter. Click column headers to sort. Drag a column border to widen it.
Point at a long comment: expect a tooltip with the whole text.

**T21 Keep tool.** Click the pin in the Properties toolbar. Choose Rectangle and place three rectangles in a row.
Expect the tool to stay on. Click the pin again: it goes back to single use.

**T22 A straight drag.** Choose Rectangle and drag exactly horizontally. Expect a message in the status bar
("Drag a region with some width and some height…"), not an error window.

### D. Measuring

Use `plan_v1.pdf`, page 1.

**T23 Calibrate.** Open the **Measure** group, choose **Calibrate scale**. Drag along the dimension line from its left end (about x=100) to its
right end (about x=460). Type `20` and choose `ft`. Choose "This page". Expect the status bar to show `Scale: 1 in = 4 ft`.

**T24 Length, perimeter, area, angle, count.** Use **Length** (click points, double-click to finish) along the dimension line:
expect about **20 ft** and a label on the page. Try **Perimeter**, **Area** (draw round room 1), **Rectangle area**, **Ellipse area**,
**Volume** (set *Depth* in the toolbar first), **Diameter**, **Angle** (3 clicks), and **Count** (click 5 times; type a group name such as
`Door` in the box first). Expect a label for each and correct values. Count stays on until you pick Select (press V).

**T25 Room fill.** Choose **Fill (room area)**. Click inside room 1 but away from the column (for example at 150, 400).
Expect an area of **about 291 sq ft** and a shaded polygon with the column cut out. Click on a wall line: expect a message that you must click an
open area. Click outside every room: expect "region is not enclosed".

**T26 Viewport scale.** Choose **Viewport** and drag a box around room 2. The program asks for the scale as
`paper inches : real length : unit`. Type `0.5:1:ft`. Then measure a length inside and outside the box: they must use different scales.

**T27 Feet and inches.** With a length on the page, open **Measure > Show feet and inches**. Expect the label to read like `20'` or `19' 11 1/2"`
(the exact value depends on where you clicked; it must not be a decimal). The Markups List follows. Switch it off: decimals again. Ctrl+Z toggles it back.

**T28 Measurements panel.** Open the **Measurements** tab. Expect the scale of the page, and a table of totals by subject.
Click **Export summary (CSV)…** and open the file in a spreadsheet.

**T29 Resize a measurement.** Select an area and drag its corner handle. Expect the label and value to change. Resize an angle.
A **Count** marker must refuse to resize and say why.

### E. Sheets and revisions

**T30 Sheets panel.** Open `plan_v1.pdf`. Open the **Sheets** tab (fifth icon on the left).
Expect three rows: `A-101`, `A-102`, `S-201`, with titles. Edit a title and a discipline (double-click the cell). Try to give a sheet a number
that another sheet already has: expect a clear message and the old value back.

**T31 Revisions.** Select two rows (Ctrl+click). Click **Revision**. Enter `A`, a description, and a date such as `2026-01-15`.
Expect `A` in the *Rev* column of both. Point at the Rev cell: a tooltip lists the history. Try the same revision name again:
expect a refusal. Try a bad date.

**T32 Index.** Click **Index**. Expect a new first page "Sheet Index" with a table. Click a row on that page with the Pan tool:
it goes to that sheet. Ctrl+Z removes the page.

**T33 Slip sheet.** In `plan_v1.pdf` draw a rectangle on A-101 and add a reply to it. Save. Click **Slip** and choose `plan_v2.pdf`,
**By sheet number**, revision name `B`. Expect: a message like "2 sheet(s) replaced, 1 added, 1 not in the new file (kept)".
A-101 now shows `Rev TWO` text **and still has your rectangle and its reply**. A-102 is unchanged. A-103 is added at the end. The Sheets
table shows revision `B` on A-101. Ctrl+Z restores the old set.

**T34 Compare.** Open `plan_v1.pdf` (saved). **Document > Compare with another PDF…**, choose `plan_v2.pdf`, **By sheet number**.
Expect a result table: A-101 changed, S-201 changed, A-103 added, A-102 removed, with percentages and word counts. **Open overlay** opens
a PDF with red and blue marks. **Save report (CSV)…** works. Repeat with **By page position**: the results are different (everything looks changed).

### F. Documents and pages

**T35 Page operations.** **Document** menu: insert a blank page, delete a page, rotate pages (`1-2`, 90), move a page, crop a page
(type four numbers), extract pages, split. Check each result. The thumbnails and the page count must follow. Ctrl+Z after each.

**T36 Header, watermark, numbers.** Add a header/footer with `Page {page} of {pages}`, a text watermark `DRAFT`, and Bates numbers.
Expect them on every page, upright, also on `rotated.pdf`.

**T37 Redaction.** Choose **Redact** (Stamp & Sign group), drag over the words `Rev one` on A-101. Then **Document > Apply redactions** and confirm.
Search for `Rev one` on that page: it must be gone. Save and reopen: still gone.

**T38 Flatten and optimize.** **Document > Flatten markups**: the markups become part of the page and leave the list.
**File > Optimize / compress copy…** makes a smaller or equal file.

**T39 Forms.** Open `form.pdf`. **Document > Fill form field…**: choose `name`, type a value. Expect it shown in the field.

**T40 OCR** (needs Tesseract). Open `scanned.pdf`. Search for `INVOICE`: no results. **Document > OCR**. Search again: expect a result.

**T41 Layers.** Open the **Layers** tab on a file with layers (none are provided; if you have one, toggle a layer and check). Otherwise expect
"This PDF has no layers."

**T42 PDF/A** (needs Ghostscript). **File > Export as PDF/A…**, level `2b`. Expect a result message. If veraPDF is installed it says
"compliant with PDF/A-2b". If not, it says "Not validated". Open the result: it must look like the original, with the markups flattened.

**T43 Markup Summary.** Place four markups with different statuses and comments. **File > Export Markup Summary (PDF)…** (with pictures).
Expect a report: status counts, a heading per page with the sheet number, and for each markup a picture, comment, reply, and status.
Check that nothing is cut off at the page edges. Try a very long comment (paste 100 lines).

**T44 Custom columns.** Right-click the Markups List header, **Add column…**: name `Cost`, type `number`. Add another: `Trade`, type `choice`, choices `Electrical, Plumbing`.
Select a markup: expect two new fields in the **Properties** tab. Type `1234567` in Cost and press Tab: expect `1234567` (not `1.23457e+06`).
Type `abc`: expect a message and the old value back. Check the list columns and **File > Export markups list (CSV)…**.

**T45 Tool Chest.** Open the **Tool Chest** tab. Expect groups (Shapes, Text & Review, Stamp & Sign, Insert, Measure, Stamps, My Tools).
Click *Cloud*: that tool starts. Select a rectangle, click **Add selected**, name it: it appears in *My Tools*. Click it, then click the page: it is placed there.
**File > Import Bluebeam tool set (.btx)…** `tools.btx`: expect "3 tool(s) imported, 1 skipped" and a details text that explains the stamp.
The three tools appear in *My Tools* and can be placed.

**T46 Insert tools.** **Image**: choose a picture, drag a box (or click). **Snapshot**: drag a region, then paste in another program: a picture
arrives. **Copy text**: drag over text, paste in a text editor: the text arrives.

### G. Saving, safety, and speed

**T47 Save and reopen.** Add markups, a measurement scale, a sheet revision, a custom column. Save. Close. Open again.
Everything must be there. Open the saved file in a **different PDF viewer**: the markups must show there too.

**T48 Autosave and recovery.** Start the program with `OPENREVU_AUTOSAVE_SECONDS=5 openrevu`. Open a file, add two markups, wait 10 seconds.
Kill the program hard (`kill -9 <pid>`, or end the task). Start it again. Expect a question: "OpenRevu closed unexpectedly last time…".
Choose **Recover**. Expect a tab named `<file> (recovered)` with your markups. Press Ctrl+S: it asks for a file name (it must not overwrite the original silently).

**T49 Rotated page.** Open `rotated.pdf`. Draw a rectangle, a line, a cloud, a stamp, and a text box. Move and resize them. Search for text.
Every markup must appear **where you drew it** and move the way the mouse moves.

**T50 Speed.** Open `many_pages.pdf` (60 pages). Scroll quickly, jump to page 60, open the Sheets tab, run **Detect**.
Expect no long freezes (more than 2 seconds), and memory that does not keep growing.

### H. Visual and feel (look at the whole window)

Answer these as a person would. Write a sentence for each.

1. Is anything cut off, overlapping, or too small to read? (Look at the toolbars, the panel tabs, the Sheets table, dialogs.)
2. Do the icons mean what they say? Point at each to read its tooltip. Which icon is the hardest to understand?
3. Are the cursor shapes right? (Arrow for Select, a hand for Pan, a cross for drawing tools.)
4. Do the dialogs have clear titles and button names? Is any message confusing or too technical?
5. After 10 minutes, what slowed you down? What did you have to look for?
6. Try making the window small (1100 x 700). Does it stay usable?
7. If you have a high-resolution screen: are the page and the icons sharp?
8. What would a Bluebeam Revu user miss first?

## 5. Known limits (do not report these)

- No real-time collaboration (Studio Sessions / Projects).
- Only `.btx` tool sets are imported (not `.bpx` / `.bax`). `tools.btx` was made to a community description, not exported from Revu.
- A **Count** marker has a fixed size. Highlight, underline, and strikeout markups, stamps, and measurements cannot be copied or saved as tools.
- Links to files and programs are never opened (on purpose).
- PDF/A-1 does not allow transparency; the program warns when a page loses its text. Use 2b.
- The Fill tool needs a closed boundary.
- The packaged programs do not include Tesseract, Ghostscript, or veraPDF.

## 6. Report

Save your report as `docs/testing/results/RESULTS-<date>-<your name>.md` in your clone (**do not push it**; give the file to the maintainer).
Use this layout:

```
# OpenRevu test report
Tester: <name>    Date: <date>    OpenRevu version: <openrevu --version>
Operating system / screen size / Python version:
Optional programs found: Tesseract yes/no, Ghostscript yes/no, veraPDF yes/no

## Summary
<5 lines: how it felt, the 3 worst problems, the 3 best things>

## Results
| Test | Result | Notes |
|---|---|---|
| T01 | PASS | |
| T02 | FAIL | <exactly what you did, what you saw, what you expected> screenshot: shots/T02.png |
...

## Problems found (worst first)
### 1. <short title>
- Test: T__    Severity: crash / data loss / wrong result / confusing / cosmetic
- Steps: 1. ... 2. ... 3. ...
- Expected:
- Actual:
- Screenshot:
- How often: always / sometimes

## Feel and usability (section H)
<your answers>
```

**Severity guide.** *crash*: the program closes or shows an error window. *data loss*: your work or a file is damaged or lost.
*wrong result*: a number, a file, or a position is wrong. *confusing*: it works but a user would be lost. *cosmetic*: it looks wrong.

If the program shows an "unexpected problem" window, press **Show Details** and copy the text into your report. That is a bug.
