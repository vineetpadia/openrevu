"""Regenerates the README screenshots: QT_QPA_PLATFORM=offscreen python docs/make_screenshots.py"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fitz
from PyQt5 import QtCore, QtWidgets

from openrevu.core import Scale
from openrevu.gui import Main

OUT = os.path.dirname(os.path.abspath(__file__))


def sample_plan(path):
    d = fitz.open()
    for n, name in enumerate(["A-101 Ground floor plan", "A-102 First floor plan", "S-201 Framing"], 1):
        p = d.new_page(width=1224, height=792)  # ARCH-ish landscape
        p.draw_rect(fitz.Rect(60, 60, 1164, 732), width=1.5)
        p.insert_text((80, 100), name, fontsize=20)
        p.insert_text((1000, 715), name.split()[0], fontsize=26)
        p.draw_rect(fitz.Rect(150, 160, 560, 460), width=3)                    # room 1
        p.draw_rect(fitz.Rect(560, 160, 900, 460), width=3)                    # room 2
        p.draw_rect(fitz.Rect(150, 460, 900, 640), width=3)                    # corridor
        p.draw_rect(fitz.Rect(300, 260, 340, 300), width=2, fill=(0.6, 0.6, 0.6))  # column
        p.insert_text((250, 220), "OFFICE 101", fontsize=14)
        p.insert_text((680, 220), "MEETING 102", fontsize=14)
        p.insert_text((450, 560), f"Refer to {'A-102' if n == 1 else 'A-101'} for details", fontsize=12)
        for x in (150, 560, 900):
            p.draw_line((x, 470), (x, 470), width=0)
    d.save(path)


def main():
    tmp = tempfile.mkdtemp()
    pdf = os.path.join(tmp, "Sample project.pdf")
    sample_plan(pdf)
    app = QtWidgets.QApplication([])
    w = Main(pdf)
    w.resize(1500, 950)
    w.show()
    app.processEvents()
    d = w.doc
    d.set_scale(Scale.from_ratio(0.25, 1, "ft"))
    d.auto_bookmarks()
    d.add_fill_area(0, (350, 400), subject="Office")
    d.add_fill_area(0, (700, 400), subject="Meeting room")
    d.add_length(0, [(150, 680), (900, 680)])
    d.add_cloud(0, fitz.Rect(280, 240, 360, 320), (1, 0, 0))
    d.add_callout(0, fitz.Rect(960, 220, 1130, 280), "Confirm column size with structural", (340, 285))
    d.add_stamp(0, fitz.Rect(930, 90, 1150, 150), "REVIEWED")
    for x, y in [(210, 470), (410, 470), (610, 470)]:
        d.add_count(0, (x, y), group="Door")
    d.add_highlight(0, fitz.Rect(240, 205, 330, 225), (1, 1, 0))
    m = [x for x in d.markups() if x.subject == "Cloud"][0]
    m.set_status("Accepted")
    d.add_reply(m, "Updated in rev B")
    w.cv.reload()
    w.cv.fit_width()
    w.cv.select([x for x in d.markups() if x.subject == "Cloud"][0])
    w._after_change(); w.refresh_all()
    w.d_thumbs.raise_()
    for _ in range(30):
        app.processEvents()
        QtCore.QThread.msleep(20)
    w.grab().save(os.path.join(OUT, "main-window.png"))

    w.d_toc.raise_(); w.cv.select(None); w.cv.goto_page(1)
    for _ in range(10):
        app.processEvents()
    w.grab().save(os.path.join(OUT, "bookmarks-and-page2.png"))
    w.cv.goto_page(0)
    for _ in range(10):
        app.processEvents()

    # measurement summary dialog (modal: capture and close it from a timer)
    def snap_and_close():
        for x in app.topLevelWidgets():
            if isinstance(x, QtWidgets.QDialog) and x.isVisible():
                x.grab().save(os.path.join(OUT, "measurement-summary.png"))
                x.done(0)

    QtCore.QTimer.singleShot(300, snap_and_close)
    w.show_summary()


if __name__ == "__main__":
    sys.exit(main())
