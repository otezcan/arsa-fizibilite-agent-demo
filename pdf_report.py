from __future__ import annotations
from typing import Dict, Any, List
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os

def _register_fonts():
    # Türkçe karakter uyumu için DejaVu
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont("DejaVu", font_path))
    if os.path.exists(bold_path):
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold_path))

def money(x: float) -> str:
    return f"${x:,.0f}"

def num(x: float, d: int = 2) -> str:
    return f"{x:,.{d}f}"

def build_pdf(path: str, title: str, inputs: Dict[str, Any], outputs: Dict[str, Any], warnings: List[str]):
    _register_fonts()
    base_font = "DejaVu" if "DejaVu" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    bold_font = "DejaVu-Bold" if "DejaVu-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1", fontName=bold_font, fontSize=18, leading=22, spaceAfter=10))
    styles.add(ParagraphStyle(name="H2", fontName=bold_font, fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="Cell", fontName=base_font, fontSize=9.6, leading=12))
    styles.add(ParagraphStyle(name="CellBold", fontName=bold_font, fontSize=9.6, leading=12))
    styles.add(ParagraphStyle(name="Small", fontName=base_font, fontSize=9, leading=12, textColor=colors.HexColor("#555555")))

    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=2.0*cm, rightMargin=2.0*cm,
                            topMargin=1.7*cm, bottomMargin=1.7*cm)

    story = []
    story.append(Paragraph(f"{title}", styles["H1"]))
    story.append(Paragraph(f"Tarih: {datetime.now().strftime('%d.%m.%Y')}", styles["Small"]))
    story.append(Spacer(1, 10))

    # Filigran uyarısı
    story.append(Paragraph("DEMO AMAÇLIDIR — Ticari/Yatırım kararı için tek başına kullanılamaz.", styles["Small"]))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Özet", styles["H2"]))
    summary_data = [
        ["Satılabilir Alan", f"{num(outputs['satilabilir_alan_m2'],0)} m²"],
        ["Toplam İnşaat Alanı", f"{num(outputs['toplam_insaat_alani_m2'],0)} m²"],
        ["Proje Hasılatı", money(outputs["proje_hasilati_usd"])],
        ["Toplam Proje Maliyeti (Arsa Dahil)", money(outputs["toplam_proje_maliyeti_usd"])],
        ["Proje Karı", money(outputs["proje_kari_usd"])],
        ["Brüt Karlılık Oranı", f"{num(outputs['brut_karlilik_orani']*100,1)}%"],
        ["Yaklaşık Konut Adedi", f"{num(outputs['yaklasik_konut_adedi'],1)} adet"],
    ]
    t = Table(summary_data, colWidths=[9.5*cm, 6.5*cm])
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, colors.HexColor("#FAFBFD")]),
        ("LINEBELOW", (0,0), (-1,-1), 0.25, colors.HexColor("#D6DDE6")),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("FONTNAME", (0,0), (-1,-1), base_font),
    ]))
    story.append(t)

    if warnings:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Uyarılar", styles["H2"]))
        for w in warnings:
            story.append(Paragraph(f"• {w}", styles["Cell"]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Kabul ve Girdiler", styles["H2"]))
    rows = []
    for k, v in inputs.items():
        rows.append([k, str(v)])
    at = Table(rows, colWidths=[7.0*cm, 9.0*cm])
    at.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D6DDE6")),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#F2F5F9")),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("FONTNAME", (0,0), (-1,-1), base_font),
    ]))
    story.append(at)

    def watermark(canvas, _doc):
        canvas.saveState()
        canvas.setFont(bold_font, 60)
        canvas.setFillColorRGB(0.9, 0.9, 0.9)
        canvas.translate(150, 400)
        canvas.rotate(35)
        canvas.drawString(0, 0, "DEMO")
        canvas.restoreState()

    doc.build(story, onFirstPage=watermark, onLaterPages=watermark)

