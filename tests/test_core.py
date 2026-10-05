import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import math

import fitz
import pytest

from openrevu.core import Document, Scale, cloud_points, polygon_area


@pytest.fixture
def pdf(tmp_path):
    d = fitz.open()
    p = d.new_page(width=612, height=792)
    p.insert_text((72, 100), "Hello plan sheet")
    d.new_page()
    path = tmp_path / "t.pdf"
    d.save(path)
    return str(path)


def test_polygon_area():
    assert polygon_area([(0, 0), (10, 0), (10, 5), (0, 5)]) == 50
    assert polygon_area([(0, 0), (1, 1)]) == 0


def test_scale_calibration():
    s = Scale.from_calibration((0, 0), (100, 0), 20, "ft")  # 100pt == 20ft
    assert s.length([(0, 0), (50, 0)]) == pytest.approx(10)
    assert s.area([(0, 0), (100, 0), (100, 100), (0, 100)]) == pytest.approx(400)
    with pytest.raises(ValueError):
        Scale.from_calibration((0, 0), (0, 0), 5, "ft")
    with pytest.raises(ValueError):
        Scale.from_calibration((0, 0), (1, 0), 5, "furlong")
    assert Scale.from_json(s.to_json()) == s


def test_cloud_is_closed_polyline():
    pts = cloud_points(fitz.Rect(10, 10, 110, 60))
    assert pts[0] == pts[-1] and len(pts) > 20


def test_markups_roundtrip_and_takeoff(pdf, tmp_path):
    doc = Document(pdf)
    doc.set_scale(Scale.from_calibration((0, 0), (100, 0), 10, "m"))
    doc.add_rect(0, fitz.Rect(50, 50, 150, 100))
    doc.add_ellipse(0, fitz.Rect(50, 150, 150, 200))
    doc.add_line(0, (10, 10), (100, 100), arrow=True)
    doc.add_freehand(0, [(10, 10), (20, 30), (40, 20)])
    doc.add_cloud(0, fitz.Rect(200, 200, 300, 260))
    doc.add_highlight(0, fitz.Rect(60, 85, 200, 105))
    doc.add_text(0, fitz.Rect(300, 300, 420, 330), "check this")
    doc.add_note(1, (50, 50), "a note")
    _, ln = doc.add_length(0, [(0, 0), (100, 0), (100, 50)])
    _, ar = doc.add_area(0, [(0, 0), (100, 0), (100, 100), (0, 100)])
    doc.add_count(0, (300, 400)); doc.add_count(0, (320, 400))
    assert ln == pytest.approx(15) and ar == pytest.approx(100)
    tot = doc.takeoff()
    assert tot[("length", "m")] == pytest.approx(15)
    assert tot[("area", "m")] == pytest.approx(100)
    assert tot[("count", "ea")] == 2

    out = str(tmp_path / "out.pdf")
    doc.save(out)
    re = Document(out)
    assert re.scale.unit == "m" and re.scale.unit_per_pt == pytest.approx(0.1)
    assert re.takeoff() == pytest.approx(tot)
    assert len(re.markups()) == len(doc.markups())
    assert any(m.subject == "Cloud" for m in re.markups())


def test_save_in_place_and_delete(pdf):
    doc = Document(pdf)
    doc.add_rect(0, fitz.Rect(10, 10, 50, 50))
    doc.save()
    doc2 = Document(pdf)
    ms = doc2.markups()
    assert len(ms) == 1
    doc2.delete(ms[0])
    doc2.save()
    assert Document(pdf).markups() == []


def test_csv_and_render(pdf, tmp_path):
    doc = Document(pdf)
    doc.add_length(0, [(0, 0), (72, 0)], label=False)
    doc.add_note(0, (5, 5), "hi")
    csvp = tmp_path / "m.csv"
    doc.export_csv(csvp)
    rows = csvp.read_text().splitlines()
    assert len(rows) == 3 and "Length" in rows[1]
    pix = doc.render(0, 1.5)
    assert pix.width == pytest.approx(612 * 1.5, abs=1)
