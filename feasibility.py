from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Dict, Any, List, Tuple
import math

OtoparkTipi = Literal["ACIK", "KAPALI"]
KonutSinifi = Literal["ALT", "ORTA", "YUKSEK"]

DEFAULTS = {
    "satilabilir_katsayi": 1.25,
    "otopark_katsayi": {"ACIK": 1.20, "KAPALI": 1.60},
    "insaat_maliyet_usd_m2": {"ALT": 700, "ORTA": 900, "YUKSEK": 1100},
    "ortalama_konut_m2": 120,
}

def compute_outputs(inputs: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    # Zorunlu alanlar
    required = [
        "arsa_alani_m2",
        "emsal",
        "otopark_tipi",
        "satis_birim_fiyat_usd_m2",
        "konut_sinifi",
        "arsa_toplam_degeri_usd",
    ]
    for k in required:
        if k not in inputs or inputs[k] in [None, ""]:
            raise ValueError(f"Eksik alan: {k}")

    arsa = float(inputs["arsa_alani_m2"])
    emsal = float(inputs["emsal"])

    satilabilir_katsayi = float(inputs.get("satilabilir_katsayi", DEFAULTS["satilabilir_katsayi"]))

    otopark_tipi: OtoparkTipi = inputs["otopark_tipi"]
    otopark_katsayi = float(inputs.get("otopark_katsayi", DEFAULTS["otopark_katsayi"][otopark_tipi]))

    satis_fiyat = float(inputs["satis_birim_fiyat_usd_m2"])

    konut_sinifi: KonutSinifi = inputs["konut_sinifi"]
    insaat_maliyet_birim = float(inputs.get("insaat_maliyet_usd_m2", DEFAULTS["insaat_maliyet_usd_m2"][konut_sinifi]))

    arsa_degeri = float(inputs["arsa_toplam_degeri_usd"])
    ort_konut = float(inputs.get("ortalama_konut_m2", DEFAULTS["ortalama_konut_m2"]))

    emsal_insaat = arsa * emsal
    satilabilir = emsal_insaat * satilabilir_katsayi
    insaat_alani = satilabilir * otopark_katsayi

    hasilat = satilabilir * satis_fiyat
    insaat_maliyeti = insaat_alani * insaat_maliyet_birim
    toplam_maliyet = insaat_maliyeti + arsa_degeri

    kar = hasilat - toplam_maliyet
    brut_karlilik = (kar / toplam_maliyet) if toplam_maliyet > 0 else 0.0
    konut_adedi = (satilabilir / ort_konut) if ort_konut > 0 else 0.0

    outputs = {
        "emsal_insaat_alani_m2": emsal_insaat,
        "satilabilir_alan_m2": satilabilir,
        "toplam_insaat_alani_m2": insaat_alani,
        "proje_hasilati_usd": hasilat,
        "insaat_maliyeti_usd": insaat_maliyeti,
        "toplam_proje_maliyeti_usd": toplam_maliyet,
        "proje_kari_usd": kar,
        "brut_karlilik_orani": brut_karlilik,
        "yaklasik_konut_adedi": konut_adedi,
    }

    warnings: List[str] = []

    # Kırmızı bayraklar (deterministik)
    if brut_karlilik < 0:
        warnings.append("🚩 Proje zararda görünüyor (brüt karlılık negatif).")
    elif brut_karlilik < 0.10:
        warnings.append("⚠️ Brüt karlılık %10’un altında (düşük).")
    elif brut_karlilik < 0.20:
        warnings.append("ℹ️ Brüt karlılık %10–%20 aralığında (orta).")

    # Mantık uyarıları
    if satilabilir_katsayi < 1.0 or satilabilir_katsayi > 1.6:
        warnings.append("⚠️ Satılabilir katsayı alışılmadık aralıkta. (Örn 1.10–1.35 yaygın olur)")
    if satis_fiyat <= 0 or insaat_maliyet_birim <= 0:
        warnings.append("⚠️ Satış fiyatı / maliyet 0 veya negatif olamaz.")
    if ort_konut < 60 or ort_konut > 250:
        warnings.append("ℹ️ Ortalama konut m² değeri alışılmadık olabilir.")

    return outputs, warnings

def sensitivity(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Satış ±%10 ve Maliyet ±%10 ile 3x3 duyarlılık tablosu üretir.
    """
    base = dict(inputs)
    base_out, _ = compute_outputs(base)

    sales_mults = [0.9, 1.0, 1.1]
    cost_mults = [0.9, 1.0, 1.1]

    grid = []
    for cm in cost_mults:
        row = []
        for sm in sales_mults:
            tmp = dict(base)
            tmp["satis_birim_fiyat_usd_m2"] = float(base["satis_birim_fiyat_usd_m2"]) * sm
            # maliyet birimi override ediliyorsa onu çarp
            if "insaat_maliyet_usd_m2" in tmp and tmp["insaat_maliyet_usd_m2"] is not None:
                tmp["insaat_maliyet_usd_m2"] = float(tmp["insaat_maliyet_usd_m2"]) * cm
            else:
                # sınıfa göre default seçilmiş maliyeti çarp (outputs için)
                tmp["insaat_maliyet_usd_m2"] = float(DEFAULTS["insaat_maliyet_usd_m2"][tmp["konut_sinifi"]]) * cm

            out, _ = compute_outputs(tmp)
            row.append({
                "sales_mult": sm,
                "cost_mult": cm,
                "profit_usd": out["proje_kari_usd"],
                "gross_margin": out["brut_karlilik_orani"],
            })
        grid.append(row)

    return {"base": base_out, "grid": grid, "sales_mults": sales_mults, "cost_mults": cost_mults}

