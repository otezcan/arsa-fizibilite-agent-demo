from __future__ import annotations
from typing import Dict, Any, List, Optional
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
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont("DejaVu", font_path))
    if os.path.exists(bold_path):
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold_path))

def money_usd(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"${x:,.0f}"

def money_try(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"₺{x:,.0f}"

def num(x: Optional[float], d: int = 2) -> str:
    if x is None:
        return "-"
    return f"{x:,.{d}f}"

def build_pdf(
    path: str,
    project_title: str,
    inputs: Dict[str, Any],
    outputs: Dict[str, Any],
    warnings: List[str],
    usd_try_rate: Optional[float],
    rate_source: Optional[str],
):
    _register_fonts()
    base_font = "DejaVu" if "DejaVu" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    bold_font = "DejaVu-Bold" if "DejaVu-Bold" in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold"

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1", fontName=bold_font, fontSize=18, leading=22, spaceAfter=10))
    styles.add(ParagraphStyle(name="H2", fontName=bold_font, fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="Cell", fontName=base_font, fontSize=9.6, leading=12))
    styles.add(ParagraphStyle(name="CellBold", fontName=bold_font, fontSize=9.6, leading=12))
    styles.add(ParagraphStyle(name="Small", fontName=base_font, fontSize=9, leading=12, textColor=colors.HexColor("#555555")))

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=2.0*cm, rightMargin=2.0*cm,
        topMargin=1.7*cm, bottomMargin=1.7*cm
    )

    story = []

    # --- Header / Cover ---
    story.append(Paragraph("Dr. Ömür Tezcan / GGtech", styles["H2"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph("AI Destekli Konut Projesi Fizibilite Raporu", styles["Small"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph(f"Proje: {project_title}", styles["H1"]))
    story.append(Paragraph(f"Tarih: {datetime.now().strftime('%d.%m.%Y')}", styles["Small"]))
    if usd_try_rate is not None:
        src = rate_source or "USD/TRY"
        story.append(Paragraph(f"Kur: 1 USD = {num(usd_try_rate, 4)} TL ({src})", styles["Small"]))
    story.append(Paragraph("Not: Bu rapor hızlı ön fizibilite amaçlıdır; nihai karar için detaylı proje bütçesi ve uzman görüşü önerilir.", styles["Small"]))
    story.append(Paragraph("İletişim: omurtezcan@gmail.com", styles["Small"]))
    story.append(Spacer(1, 10))

    # --- Executive Summary (USD + TRY) ---
    story.append(Paragraph("Özet (USD / TL)", styles["H2"]))
    summary_data = [
        ["Satılabilir Alan", f"{num(outputs.get('satilabilir_alan_m2'),0)} m²", "-"],
        ["Toplam İnşaat Alanı", f"{num(outputs.get('toplam_insaat_alani_m2'),0)} m²", "-"],
        ["Toplam Proje Maliyeti", money_usd(outputs.get("toplam_proje_maliyeti_usd")), money_try(outputs.get("toplam_proje_maliyeti_try"))],
        ["Başabaş Satış Fiyatı", f"{num(outputs.get('breakeven_usd_m2'),0)} $/m²", f"{num(outputs.get('breakeven_try_m2'),0)} ₺/m²" if outputs.get("breakeven_try_m2") is not None else "-"],
        ["Hedef %10 Satış Fiyatı", f"{num(outputs.get('target_10_usd_m2'),0)} $/m²", f"{num(outputs.get('target_10_try_m2'),0)} ₺/m²" if outputs.get("target_10_try_m2") is not None else "-"],
        ["Hedef %30 Satış Fiyatı", f"{num(outputs.get('target_30_usd_m2'),0)} $/m²", f"{num(outputs.get('target_30_try_m2'),0)} ₺/m²" if outputs.get("target_30_try_m2") is not None else "-"],
        ["Hedef %50 Satış Fiyatı", f"{num(outputs.get('target_50_usd_m2'),0)} $/m²", f"{num(outputs.get('target_50_try_m2'),0)} ₺/m²" if outputs.get("target_50_try_m2") is not None else "-"],
        ["Yaklaşık Konut Adedi", f"{int(outputs.get('yaklasik_konut_adedi') or 0)} adet", "-"],
        ["Kalan Satılabilir Alan", f"{num(outputs.get('kalan_satilabilir_alan_m2'),0)} m²", "-"],
    ]

    t = Table(summary_data, colWidths=[6.4*cm, 4.9*cm, 4.7*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#F2F5F9")),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D6DDE6")),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("FONTNAME", (0,0), (-1,-1), base_font),
    ]))
    story.append(t)

    # --- Revenue mode summary if sales price present ---
    if outputs.get("satis_birim_fiyat_usd_m2"):
        story.append(Spacer(1, 10))
        story.append(Paragraph("Gelir/Kârlılık (Seçilen Satış Fiyatına Göre)", styles["H2"]))
        rdata = [
            ["Satış Fiyatı", f"{num(outputs.get('satis_birim_fiyat_usd_m2'),0)} $/m²", f"{num(outputs.get('satis_birim_fiyat_try_m2'),0)} ₺/m²" if outputs.get("satis_birim_fiyat_try_m2") is not None else "-"],
            ["Proje Hasılatı", money_usd(outputs.get("proje_hasilati_usd")), money_try(outputs.get("proje_hasilati_try"))],
            ["Proje Kârı", money_usd(outputs.get("proje_kari_usd")), money_try(outputs.get("proje_kari_try"))],
            ["Brüt Karlılık", f"{num((outputs.get('brut_karlilik_orani') or 0)*100, 1)}%", "-"],
        ]
        rt = Table(rdata, colWidths=[6.4*cm, 4.9*cm, 4.7*cm])
        rt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#F2F5F9")),
            ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D6DDE6")),
            ("LEFTPADDING", (0,0), (-1,-1), 8),
            ("RIGHTPADDING", (0,0), (-1,-1), 8),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
            ("FONTNAME", (0,0), (-1,-1), base_font),
        ]))
        story.append(rt)

    # --- Warnings ---
    if warnings:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Uyarılar ve Notlar", styles["H2"]))
        for w in warnings:
            story.append(Paragraph(f"• {w}", styles["Cell"]))

    # --- Inputs ---
    story.append(Spacer(1, 10))
    story.append(Paragraph("Girdiler ve Kabuller", styles["H2"]))
    rows = [["Alan", "Değer"]]
    for k, v in inputs.items():
        rows.append([k, str(v)])
    at = Table(rows, colWidths=[7.0*cm, 9.0*cm])
    at.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#F2F5F9")),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D6DDE6")),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("FONTNAME", (0,0), (-1,-1), base_font),
    ]))
    story.append(at)

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(base_font, 9)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(2.0*cm, 1.2*cm, "GGtech • omurtezcan@gmail.com")
        canvas.drawRightString(A4[0]-2.0*cm, 1.2*cm, f"Sayfa {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


