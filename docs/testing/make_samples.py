"""Make the sample files for the manual test plan (docs/testing/MANUAL_TEST_PLAN.md).

    python docs/testing/make_samples.py [folder]        default folder: ~/openrevu-test-files

Every file has a known content, so a tester can check what OpenRevu reports against the numbers in expected.json."""
import json
import os
import sys
import zlib

import pymupdf as fitz

OUT = os.path.abspath(os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/openrevu-test-files"))
os.makedirs(OUT, exist_ok=True)
W, H = 1224, 792                      # a landscape sheet, in points
PT_PER_FT = 18.0                      # drawing scale: 18 points = 1 foot (the dimension line on every sheet shows this)


def sheet(doc, number, title, body, extra=None, rooms=True):
    p = doc.new_page(width=W, height=H)
    p.draw_rect(fitz.Rect(40, 40, W - 40, H - 40), width=1.5)                     # sheet border
    p.insert_text((60, 80), f"{number}  {title}", fontsize=20)
    p.insert_text((W - 200, H - 55), number, fontsize=30)                        # the sheet number, in the title block
    p.insert_text((60, H - 55), body, fontsize=11)
    # a dimension line of known length: 360 points = 20 feet (use it to calibrate the scale)
    p.draw_line((100, 150), (460, 150), width=1.2)
    p.draw_line((100, 142), (100, 158), width=1.2); p.draw_line((460, 142), (460, 158), width=1.2)
    p.insert_text((230, 143), "20'-0\"", fontsize=12)
    if rooms:
        p.draw_rect(fitz.Rect(100, 200, 460, 470), width=2)                      # room 1: inside 358 x 268 pt (about 19.9' x 14.9')
        p.draw_rect(fitz.Rect(460, 200, 820, 470), width=2)                      # room 2: the same size
        p.draw_rect(fitz.Rect(100, 470, 820, 600), width=2)                      # corridor
        p.draw_rect(fitz.Rect(250, 300, 286, 336), width=1.5, fill=(0.55, 0.55, 0.55))   # a 2' x 2' column in room 1
        p.insert_text((200, 240), "OFFICE 101", fontsize=14); p.insert_text((560, 240), "MEETING 102", fontsize=14)
    if extra:
        extra(p)
    return p


# ---- plan_v1.pdf: three sheets ----
v1 = fitz.open()
sheet(v1, "A-101", "Ground floor plan", "Rev one. Walls as drawn. See A-102 for the stairs.")
sheet(v1, "A-102", "First floor plan", "Rev one. Stairs and landing. Refer to S-201 for framing.")
sheet(v1, "S-201", "Roof framing plan", "Rev one. Steel beams B1 to B4.", rooms=False)
v1.save(f"{OUT}/plan_v1.pdf")

# ---- plan_v2.pdf: a later issue. S-201 first, A-101 changed, A-102 gone, A-103 new ----
v2 = fitz.open()
sheet(v2, "S-201", "Roof framing plan", "Rev TWO. Steel beams B1 to B5.", rooms=False)
sheet(v2, "A-101", "Ground floor plan", "Rev TWO. Wall moved. See A-103 for the annex.",
      extra=lambda p: p.draw_line((640, 200), (640, 470), width=4))
sheet(v2, "A-103", "Annex plan", "Rev TWO. New sheet.")
v2.save(f"{OUT}/plan_v2.pdf")

# ---- protected.pdf: password test123 ----
d = fitz.open(f"{OUT}/plan_v1.pdf")
d.save(f"{OUT}/protected.pdf", encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="test123", owner_pw="owner-test123",
       permissions=-1)

# ---- scanned.pdf: a page that is only a picture of text (for OCR) ----
src = fitz.open(); sp = src.new_page(width=612, height=792); sp.insert_text((72, 150), "INVOICE TOTAL 4500", fontsize=36)
pix = sp.get_pixmap(dpi=200)
sc = fitz.open(); q = sc.new_page(width=612, height=792); q.insert_image(q.rect, pixmap=pix); sc.save(f"{OUT}/scanned.pdf")

# ---- rotated.pdf: a sheet with a 90 degree rotation ----
r = fitz.open(); sheet(r, "R-1", "Rotated sheet", "This page has /Rotate 90."); r[0].set_rotation(90); r.save(f"{OUT}/rotated.pdf")

# ---- many_pages.pdf: 60 sheets, for speed ----
big = fitz.open()
for i in range(1, 61):
    sheet(big, f"A-{i:03d}", f"Sheet {i}", f"Sheet {i} of 60")
big.save(f"{OUT}/many_pages.pdf")

# ---- tools.btx: a Bluebeam tool set made to the community description (not a real Revu export) ----
def item(name, text):
    return (f'<ToolChestItem Version="1"><Name>{name}</Name><Type>Bluebeam.PDF.Annotations.Test</Type>'
            f"<Raw>{zlib.compress(text.encode('latin-1')).hex()}</Raw><X>0</X><Y>0</Y><Index>0</Index><Mode>0</Mode></ToolChestItem>")
items = [item("Red box", "<</Subtype/Square/C[1 0 0]/IC[1 1 0]/CA 0.5/BS<</W 3>>/Subj(Door tag)/Rect[0 0 80 40]>>"),
         item("Blue arrow", "<</Subtype/Line/L[0 0 100 50]/LE[/None/OpenArrow]/C[0 0 1]/BS<</W 2>>>>"),
         item("Green cloud", "<</Subtype/Polygon/IT/PolygonCloud/Vertices[0 0 100 0 100 60 0 60]/C[0 0.6 0]>>"),
         item("An approved stamp", "<</Subtype/Stamp/Subj(Approved)>>")]          # not supported: must be listed with a reason
open(f"{OUT}/tools.btx", "w", encoding="utf-8").write(
    '<?xml version="1.0" encoding="utf-8"?><BluebeamRevuToolSet Version="1"><Title>Test set</Title>' + "".join(items) + "</BluebeamRevuToolSet>")

# ---- form.pdf: a page with fillable fields ----
f = fitz.open(); fp = f.new_page()
for i, n in enumerate(("name", "company")):
    w = fitz.Widget(); w.field_name, w.field_type = n, fitz.PDF_WIDGET_TYPE_TEXT; w.rect = fitz.Rect(72, 100 + 40 * i, 300, 120 + 40 * i)
    fp.add_widget(w)
f.save(f"{OUT}/form.pdf")

expected = {
    "folder": OUT,
    "drawing_scale": "18 points = 1 foot. The dimension line on every sheet is 360 points = 20 ft long.",
    "room_area_sq_ft": {"room_1_inside_walls": "about 296 (358 x 268 pt = 19.9 x 14.9 ft)",
                         "room_1_net_of_the_2x2_ft_column": "about 291 (accept 282 to 300; the program gave 290.5)"},
    "plan_v1": ["A-101", "A-102", "S-201"], "plan_v2": ["S-201", "A-101", "A-103"],
    "compare_v1_to_v2_by_sheet": {"A-101": "changed (words and a new wall line)", "A-102": "removed", "S-201": "changed (words)",
                                   "A-103": "added"},
    "protected_password": "test123", "scanned_text_after_ocr": "INVOICE TOTAL 4500",
    "tools_btx": {"imported": ["Red box", "Blue arrow", "Green cloud"], "skipped": ["An approved stamp"]},
}
json.dump(expected, open(f"{OUT}/expected.json", "w"), indent=2)
print("made", len(os.listdir(OUT)), "files in", OUT)
