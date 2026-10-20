#!/usr/bin/env python3
# Copyright 2026 Laurence Sterne www.rimbaud.io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Build 3 printable A4 PDFs from last Q&A chats."""
from __future__ import annotations

import json
import os
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

HERE = os.path.dirname(os.path.abspath(__file__))
INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#444444")
RULE = colors.HexColor("#999999")
HEAD = colors.HexColor("#1a3a2a")
ROW = colors.HexColor("#f3f6f4")


def esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def inline_md(s: str) -> str:
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<font face='Courier' size='9'>\1</font>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
    return s


def styles():
    base = getSampleStyleSheet()
    ss = {
        "title": ParagraphStyle(
            "T", parent=base["Title"], fontName="Times-Bold", fontSize=16,
            leading=20, textColor=HEAD, spaceAfter=4, alignment=TA_LEFT,
        ),
        "sub": ParagraphStyle(
            "S", parent=base["Normal"], fontName="Times-Italic", fontSize=9,
            leading=12, textColor=MUTED, spaceAfter=10,
        ),
        "h": ParagraphStyle(
            "H", parent=base["Heading2"], fontName="Times-Bold", fontSize=12,
            leading=16, textColor=HEAD, spaceBefore=10, spaceAfter=6,
        ),
        "q": ParagraphStyle(
            "Q", parent=base["Normal"], fontName="Times-Bold", fontSize=11,
            leading=15, textColor=INK, spaceBefore=4, spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "B", parent=base["Normal"], fontName="Times-Roman", fontSize=10.5,
            leading=14.5, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "li": ParagraphStyle(
            "L", parent=base["Normal"], fontName="Times-Roman", fontSize=10.5,
            leading=14.5, textColor=INK, leftIndent=0, spaceAfter=2,
        ),
        "cell": ParagraphStyle(
            "C", parent=base["Normal"], fontName="Times-Roman", fontSize=8.5,
            leading=11, textColor=INK,
        ),
        "cellh": ParagraphStyle(
            "CH", parent=base["Normal"], fontName="Times-Bold", fontSize=8.5,
            leading=11, textColor=INK,
        ),
        "foot": ParagraphStyle(
            "F", parent=base["Normal"], fontName="Times-Roman", fontSize=8,
            textColor=MUTED,
        ),
        "pre": ParagraphStyle(
            "P", parent=base["Normal"], fontName="Courier", fontSize=8,
            leading=11, textColor=INK, spaceAfter=8, leftIndent=4,
        ),
        "num": ParagraphStyle(
            "N", parent=base["Normal"], fontName="Times-Roman", fontSize=10.5,
            leading=14.5, textColor=INK, leftIndent=14, spaceAfter=3,
        ),
    }
    return ss


def parse_table(block: str, cell, cellh):
    rows = []
    for line in block.strip().splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if all(re.match(r"^:?-+:?$", c) for c in cols):
            continue
        rows.append(cols)
    if not rows:
        return None
    n = max(len(r) for r in rows)
    for r in rows:
        while len(r) < n:
            r.append("")
    usable = A4[0] - 36 * mm
    widths = [usable / n] * n
    data = []
    for i, r in enumerate(rows):
        st = cellh if i == 0 else cell
        data.append([Paragraph(inline_md(c), st) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.3, RULE),
                ("BACKGROUND", (0, 0), (-1, 0), ROW),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


def md_flow(text: str, ss):
    story = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    para = []

    def flush_para():
        nonlocal para
        if para:
            story.append(Paragraph(inline_md(" ".join(para)), ss["body"]))
            para = []

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and re.search(r"\|[\s:-]+\|", lines[i + 1]):
            flush_para()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            tbl = parse_table("\n".join(block), ss["cell"], ss["cellh"])
            if tbl:
                story.append(tbl)
                story.append(Spacer(1, 8))
            continue
        if re.match(r"^##\s+", line):
            flush_para()
            story.append(Paragraph(inline_md(re.sub(r"^##\s+", "", line)), ss["h"]))
            i += 1
            continue
        if re.match(r"^#\s+", line):
            flush_para()
            story.append(Paragraph(inline_md(re.sub(r"^#\s+", "", line)), ss["h"]))
            i += 1
            continue
        if line.strip().startswith("```"):
            flush_para()
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i].rstrip())
                i += 1
            if i < len(lines):
                i += 1
            story.append(Preformatted("\n".join(block) if block else " ", ss["pre"]))
            continue
        m = re.match(r"^[-*]\s+(.*)$", line)
        if m:
            flush_para()
            items = []
            while i < len(lines):
                mm = re.match(r"^[-*]\s+(.*)$", lines[i])
                if not mm:
                    break
                items.append(ListItem(Paragraph(inline_md(mm.group(1)), ss["li"]), leftIndent=12))
                i += 1
            story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=16, spaceAfter=6))
            continue
        nm = re.match(r"^(\d+)\.\s+(.*)$", line)
        if nm:
            flush_para()
            while i < len(lines):
                nn = re.match(r"^(\d+)\.\s+(.*)$", lines[i])
                if not nn:
                    break
                story.append(Paragraph(f"{nn.group(1)}. {inline_md(nn.group(2))}", ss["num"]))
                i += 1
            continue
        if not line.strip():
            flush_para()
            i += 1
            continue
        para.append(line.strip())
        i += 1
    flush_para()
    return story


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times-Roman", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 12 * mm, "Freiquell / Hummingbot — print notes")
    canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, str(doc.page))
    canvas.restoreState()


def build_one(path, title, q, a, ss):
    doc = SimpleDocTemplate(
        path,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Grok / Freiquell desk",
    )
    story = [
        Paragraph(esc(title), ss["title"]),
        Paragraph("Freiquell · last chats · for print", ss["sub"]),
        HRFlowable(width="100%", thickness=0.6, color=HEAD, spaceAfter=10),
        Paragraph("<b>Question</b>", ss["h"]),
        Paragraph(inline_md(q.replace("\n", " ")), ss["q"]),
        Paragraph("<b>Answer</b>", ss["h"]),
    ]
    story.extend(md_flow(a, ss))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    chats = json.load(open(os.path.join(HERE, "chats.json")))
    ss = styles()
    names = [
        ("01-markov-meta-bot.pdf", "1 · Markov as a meta-bot"),
        ("02-more-meta-algos.pdf", "2 · More meta-algos for Hummingbot"),
        ("03-regime-messgeraete.pdf", "3 · Regime gauges (Messgeräte) for bias"),
    ]
    for (fname, title), chat in zip(names, chats):
        path = os.path.join(HERE, fname)
        build_one(path, title, chat["q"], chat["a"], ss)
        print("wrote", path)


if __name__ == "__main__":
    main()
