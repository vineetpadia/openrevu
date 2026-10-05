"""Markup Summary: a PDF report of the markups in a document."""
from __future__ import annotations

import datetime
import re

import fitz

PAGE_W, PAGE_H, MARGIN = 612, 792, 40
IMG_W, IMG_H = 130, 84
LINE = 11.5
FONT = fitz.Font("helv")


def _date(raw: str) -> str:
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", raw or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _wrap(text: str, width: float, size: float = 9) -> list[str]:
    """Break text into lines that fit width. Long words are cut."""
    lines: list[str] = []
    for para in str(text).splitlines() or [""]:
        cur = ""
        for word in para.split(" "):
            while FONT.text_length(word, size) > width and len(word) > 1:  # cut a word that is wider than the line
                cut = max(1, int(len(word) * width / FONT.text_length(word, size)) - 1)
                if cur:
                    lines.append(cur); cur = ""
                lines.append(word[:cut]); word = word[cut:]
            trial = (cur + " " + word).strip() if cur else word
            if FONT.text_length(trial, size) <= width:
                cur = trial
            else:
                lines.append(cur); cur = word
        lines.append(cur)
    return lines


def markup_summary_pdf(doc, path: str, include_images: bool = True, statuses=None, title: str | None = None) -> int:
    """Write the report. statuses: only list markups with these statuses ("" means no status). Returns the count."""
    items = [m for m in doc.markups() if statuses is None or m.status in statuses]
    items.sort(key=lambda m: (m.page_no, m.rect.y0, m.rect.x0))
    out = fitz.open()
    name = title or (doc.path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if doc.path else "Untitled")
    state = {"page": None, "y": 0}

    def new_page():
        pg = out.new_page(width=PAGE_W, height=PAGE_H)
        pg.insert_text((MARGIN, 28), f"Markup Summary — {name}", fontsize=9, color=(0.35, 0.35, 0.35))
        pg.insert_text((PAGE_W - MARGIN - 80, 28), f"Page {len(out)}", fontsize=9, color=(0.35, 0.35, 0.35))
        pg.draw_line((MARGIN, 34), (PAGE_W - MARGIN, 34), color=(0.8, 0.8, 0.8), width=0.6)
        state["page"], state["y"] = pg, 56
        return pg

    pg = new_page()
    pg.insert_text((MARGIN, state["y"]), "Markup Summary", fontsize=20, fontname="hebo")
    state["y"] += 22
    pg.insert_text((MARGIN, state["y"]), f"{name}    {datetime.date.today().isoformat()}    {len(items)} markup(s)", fontsize=10)
    state["y"] += 22
    counts: dict = {}
    for m in items:
        counts[m.status or "(no status)"] = counts.get(m.status or "(no status)", 0) + 1
    for k, v in sorted(counts.items()):
        pg.insert_text((MARGIN + 6, state["y"]), f"{k}: {v}", fontsize=10)
        state["y"] += 14
    state["y"] += 8
    if not items:
        pg.insert_text((MARGIN, state["y"]), "There are no markups.", fontsize=11)
    text_x = MARGIN + (IMG_W + 12 if include_images else 0)
    text_w = PAGE_W - MARGIN - text_x
    last_page = None
    for m in items:
        info = []
        head = f"{m.subject or m.kind}  ·  {m.kind}  ·  {m.author}  ·  {_date(m.date)}".strip(" ·")
        info.append(("b", head))
        meas = m.measurement()
        if meas:
            info.append(("", "Measurement: " + doc.fmt(*meas)))
        if m.status:
            info.append(("", f"Status: {m.status}"))
        if m.comment:
            info.append(("", m.comment))
        for a, t in m.replies():
            info.append(("i", f"Reply from {a}: {t}"))
        for col in doc.columns:
            v = m.custom().get(col["name"])
            if v is not None:
                info.append(("", f"{col['name']}: {v:g}" if isinstance(v, float) else f"{col['name']}: {v}"))
        lines = [(style, ln) for style, t in info for ln in _wrap(t, text_w)]
        h = max(IMG_H if include_images else 0, len(lines) * LINE) + 12
        heading_h = 24 if m.page_no != last_page else 0
        if state["y"] + h + heading_h > PAGE_H - 50:
            new_page()
        if m.page_no != last_page:
            sheet = doc.sheet_info(m.page_no)["number"]
            state["page"].insert_text((MARGIN, state["y"] + 14), f"Page {m.page_no + 1}" + (f"  ({sheet})" if sheet else ""),
                                      fontsize=12, fontname="hebo")
            state["y"] += heading_h
            last_page = m.page_no
        y0 = state["y"]
        if include_images:
            clip = (m.rect + (-30, -30, 30, 30)) & doc.doc[m.page_no].rect
            if not clip.is_empty:
                pix = doc.render(m.page_no, 1.3, clip=clip)
                box = fitz.Rect(MARGIN, y0, MARGIN + IMG_W, y0 + IMG_H)
                state["page"].insert_image(box, pixmap=pix, keep_proportion=True)
                state["page"].draw_rect(box, color=(0.8, 0.8, 0.8), width=0.5)
        for k, (style, ln) in enumerate(lines):
            font = {"b": "hebo", "i": "heit"}.get(style, "helv")
            state["page"].insert_text((text_x, y0 + 9 + k * LINE), ln, fontsize=9, fontname=font)
        state["y"] = y0 + h
        state["page"].draw_line((MARGIN, state["y"] - 4), (PAGE_W - MARGIN, state["y"] - 4), color=(0.9, 0.9, 0.9), width=0.4)
    out.save(path, garbage=3, deflate=True)
    out.close()
    return len(items)
