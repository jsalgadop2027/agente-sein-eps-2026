"""PDF de la conversación: consultas y respuestas del agente hasta el momento.

Es un documento de trabajo descargable en cualquier punto de la sesión; no
reemplaza el informe técnico firmado que emite ``export_report``. Las respuestas
vienen en Markdown y se convierten con un intérprete propio de bloques
(títulos, párrafos, listas, tablas, código, citas) para no depender de un
conversor HTML que falle con estructuras inesperadas.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from src.memory.store import now_iso

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
ACCENT = colors.HexColor("#0b5cad")
MUTED = colors.HexColor("#5f6b7a")
LINE = colors.HexColor("#c9d1db")
SHADE = colors.HexColor("#eef3fa")


def _fonts() -> tuple[str, str, str]:
    """DejaVu cubre tildes, ñ y símbolos técnicos (≥, Δ, →); sin ella, Helvetica (solo Latin-1)."""
    try:
        if "GenSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("GenSans", str(FONT_DIR / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("GenSans-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
            pdfmetrics.registerFont(TTFont("GenMono", str(FONT_DIR / "DejaVuSansMono.ttf")))
            pdfmetrics.registerFontFamily("GenSans", normal="GenSans", bold="GenSans-Bold",
                                          italic="GenSans", boldItalic="GenSans-Bold")
        return "GenSans", "GenSans-Bold", "GenMono"
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Courier"


SANS, BOLD, MONO = _fonts()


def _style(name: str, **kw) -> ParagraphStyle:
    base = {"fontName": SANS, "fontSize": 9.5, "leading": 13, "alignment": TA_LEFT, "spaceAfter": 4}
    return ParagraphStyle(name, **(base | kw))


ST = {
    "title": _style("title", fontName=BOLD, fontSize=17, leading=21, textColor=ACCENT, spaceAfter=2),
    "subtitle": _style("subtitle", textColor=MUTED, fontSize=9, spaceAfter=10),
    "q_head": _style("q_head", fontName=BOLD, fontSize=12, leading=15, textColor=ACCENT, spaceBefore=10),
    "label": _style("label", fontName=BOLD, fontSize=8.5, textColor=MUTED, spaceBefore=4, spaceAfter=2),
    "body": _style("body"),
    "question": _style("question", backColor=SHADE, borderPadding=(5, 6, 5, 6), spaceAfter=8, leftIndent=2,
                       rightIndent=2),
    "h1": _style("h1", fontName=BOLD, fontSize=12, leading=15, spaceBefore=6),
    "h2": _style("h2", fontName=BOLD, fontSize=11, leading=14, spaceBefore=5),
    "h3": _style("h3", fontName=BOLD, fontSize=10, leading=13, spaceBefore=4),
    "cell": _style("cell", fontSize=8, leading=10, spaceAfter=0),
    "cell_head": _style("cell_head", fontName=BOLD, fontSize=8, leading=10, spaceAfter=0),
    "code": _style("code", fontName=MONO, fontSize=7.5, leading=9.5, backColor=colors.HexColor("#f3f4f6"),
                   borderPadding=4, spaceBefore=2, spaceAfter=6),
    "quote": _style("quote", leftIndent=10, textColor=MUTED),
    "small": _style("small", fontSize=8, leading=10.5, textColor=MUTED),
    "warn": _style("warn", fontName=BOLD, fontSize=8.5, leading=11, textColor=colors.HexColor("#b3261e")),
}


# ---------------------------------------------------------------- Markdown → flowables

# Emoji frecuentes en las respuestas → símbolos que DejaVu sí tiene
_GLYPHS = str.maketrans({"✅": "✓", "✔": "✓", "❌": "✗", "✖": "✗", "🔴": "●", "🟢": "●", "🟡": "●",
                         "️": None, "🛡": ""})


def _inline(text: str) -> str:
    t = escape(text.translate(_GLYPHS))
    t = re.sub(r"&lt;br\s*/?&gt;", "<br/>", t)
    t = re.sub(r"`([^`]+)`", lambda m: f'<font face="{MONO}">{m.group(1)}</font>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" color="#0b5cad">\1</a>', t)
    return t


def _para(text: str, style: ParagraphStyle, **kw) -> Paragraph:
    try:
        return Paragraph(_inline(text), style, **kw)
    except Exception:  # marcado cruzado (p.ej. **a *b** c*): se muestra como texto plano
        return Paragraph(escape(text), style, **kw)


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _table(rows: list[str], width: float) -> Table:
    head, body = _cells(rows[0]), [_cells(r) for r in rows[2:]]
    n = len(head)
    body = [(r + [""] * n)[:n] for r in body]
    data = [[_para(c, ST["cell_head"]) for c in head]] + [[_para(c, ST["cell"]) for c in r] for r in body]
    # Ancho proporcional al texto más largo de cada columna (acotado para que ninguna se coma la tabla)
    weights = [min(max(len(r[j]) for r in [head] + body), 45) + 6 for j in range(n)]
    t = Table(data, colWidths=[width * w / sum(weights) for w in weights], repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), SHADE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")


def markdown_flowables(md: str, width: float) -> list:
    out, para = [], []
    lines = md.replace("\r\n", "\n").split("\n")

    def flush():
        if para:
            out.append(_para(" ".join(para), ST["body"]))
            para.clear()

    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            esc = escape("\n".join(code)).replace("\n", "<br/>")
            esc = re.sub(r"(?m)(^|<br/>)( +)", lambda m: m.group(1) + "&nbsp;" * len(m.group(2)), esc)
            out.append(Paragraph(esc or "&nbsp;", ST["code"]))
        elif s.startswith("|") and i + 1 < len(lines) and _SEP.match(lines[i + 1]):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out += [_table(rows, width), Spacer(1, 6)]
            continue
        elif m := re.match(r"^(#{1,6})\s+(.*)$", s):
            flush()
            out.append(_para(m.group(2), ST[f"h{min(len(m.group(1)), 3)}"]))
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            flush()
            out.append(HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=4, spaceAfter=6))
        elif m := _LIST.match(ln):
            flush()
            level = len(m.group(1).expandtabs(4)) // 2
            marker = m.group(2)
            bullet = "•" if marker in "-*+" else marker
            style = ParagraphStyle(f"li{level}", parent=ST["body"], leftIndent=12 + 12 * level,
                                   bulletIndent=2 + 12 * level, spaceAfter=2)
            out.append(_para(m.group(3), style, bulletText=bullet))
        elif s.startswith(">"):
            flush()
            out.append(_para(s.lstrip("> "), ST["quote"]))
        elif not s:
            flush()
        else:
            para.append(s)
        i += 1
    flush()
    return out


# ---------------------------------------------------------------- conversación

def conversation_turns(history: list[dict]) -> list[dict]:
    """Agrupa el historial en consultas: texto del usuario + respuestas y herramientas que siguieron."""
    turns: list[dict] = []
    for h in history:
        if h["role"] == "user":
            turns.append({"pregunta": h["text"], "respuestas": [], "herramientas": []})
        elif turns:
            if h.get("text"):
                turns[-1]["respuestas"].append(h["text"])
            turns[-1]["herramientas"] += [c["name"] for c in h.get("tool_calls", [])]
    return turns


def answered(history: list[dict]) -> bool:
    return any(t["respuestas"] for t in conversation_turns(history))


def build_pdf(session: dict, history: list[dict], model: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"GenEPS · {session['session_id']}",
                            author=session.get("user", ""), subject="Consultas y respuestas del agente GenEPS")
    width = doc.width
    v = session.get("variables", {})
    generated = now_iso()

    story = [
        Paragraph("GenEPS · Consultas y respuestas", ST["title"]),
        Paragraph(escape(f"Sesión {session['session_id']} · generado {generated} UTC"), ST["subtitle"]),
    ]
    meta = [
        ("Usuario", f"{session.get('user', '')} ({session.get('role', '')})"),
        ("Proyecto", session.get("project_id", "")),
        ("Objetivo", v.get("objetivo_estudio", "")),
        ("Escenario", v.get("escenario", "")),
        ("Casos", f"estático {v.get('caso_estatico') or 'ieee14_cdf'} · dinámico {v.get('caso_dinamico') or 'ieee14_dyn'}"),
        ("Modelo", model),
    ]
    mt = Table([[Paragraph(k, ST["cell_head"]), _para(str(val), ST["cell"])] for k, val in meta],
               colWidths=[28 * mm, width - 28 * mm], hAlign="LEFT")
    mt.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (0, -1), SHADE),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [mt, Spacer(1, 6),
              Paragraph("Documento de trabajo con las respuestas del agente hasta este momento. No reemplaza el "
                        "informe técnico firmado por el ingeniero responsable. Las cifras provienen de los "
                        "estudios citados por su study_id.", ST["small"]),
              HRFlowable(width="100%", thickness=0.6, color=LINE, spaceBefore=6, spaceAfter=2)]

    for n, t in enumerate(conversation_turns(history), 1):
        try:
            question = Paragraph("<br/>".join(_inline(x) for x in t["pregunta"].split("\n")), ST["question"])
        except Exception:
            question = Paragraph(escape(t["pregunta"]).replace("\n", "<br/>"), ST["question"])
        story.append(KeepTogether([Paragraph(f"Consulta {n}", ST["q_head"]), question]))
        if t["respuestas"]:
            story.append(Paragraph("Respuesta del agente", ST["label"]))
            for r in t["respuestas"]:
                story += markdown_flowables(r, width)
        else:
            story.append(Paragraph("Sin respuesta todavía (en proceso o pendiente de aprobación).", ST["small"]))
        if t["herramientas"]:
            story.append(Paragraph(escape("Herramientas usadas: " + ", ".join(dict.fromkeys(t["herramientas"]))),
                                   ST["small"]))
        checks = [v for v in session.get("verificaciones", []) if v.get("turno") == n]
        bad = sorted({x for v in checks for x in v["inexistentes"] + v["otro_proyecto"]})
        if bad:
            story.append(Paragraph(escape("⚠ Verificación automática: cita estudios inexistentes o de otro proyecto: "
                                          + ", ".join(bad) + ". Revise esas cifras."), ST["warn"]))
        elif checks:
            ok = sorted({x for v in checks for x in v["verificados"]})
            story.append(Paragraph(escape(f"✓ Verificación automática: los {len(ok)} estudio(s) citados existen "
                                          "en el proyecto."), ST["small"]))

    events = session.get("events", [])
    if events:
        story += [HRFlowable(width="100%", thickness=0.6, color=LINE, spaceBefore=10, spaceAfter=4),
                  Paragraph("Estudios ejecutados en la sesión", ST["h2"])]
        et = Table([[Paragraph("study_id", ST["cell_head"]), Paragraph("Tipo", ST["cell_head"])]]
                   + [[Paragraph(escape(e["study_id"]), ST["cell"]), Paragraph(escape(e["study_type"]), ST["cell"])]
                      for e in events], colWidths=[width * 0.6, width * 0.4], repeatRows=1, hAlign="LEFT")
        et.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (-1, 0), SHADE)]))
        story.append(et)

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont(SANS, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(d.leftMargin, 9 * mm, f"GenEPS · {session['session_id']}")
        canvas.drawRightString(d.leftMargin + d.width, 9 * mm, f"Página {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
