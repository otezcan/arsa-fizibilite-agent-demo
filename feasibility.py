from __future__ import annotations
from typing import Literal, Dict, Any, List, Tuple, Optional
import math

OtoparkTipi = Literal["ACIK", "KAPALI"]
KonutSinifi = Literal["ALT", "ORTA", "YUKSEK"]

DEFAULTS = {
    "satilabilir_katsayi": 1.25,
    "otopark_katsayi": {"ACIK": 1.20, "KAPALI": 1.60},
    "insaat_maliyet_usd_m2": {"ALT": 700, "ORTA": 900, "YUKSEK": 1100},
    "ortalama_konut_m2": 120,
}

def compute_outputs(
    inputs: Dict[str, Any],
    usd_try_rate: Optional[float] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    2 mod:
    - Maliyet modu: satış fiyatı girilmeden çalışır (başabaş + hedef fiyatlar üretir)
    - Gelir modu: satış fiyatı varsa hasılat/kâr/kârlılık hesaplar
    """

    # Zorunlu alanlar (satış fiyatı artık zorunlu değil)
    required = [
        "arsa_alani_m2",
        "emsal",
        "otopark_tipi",
        "konut_sinifi",
        "arsa_toplam_degeri_usd",
    ]
    for k in required:
        if k not in inputs or inputs[k] in [None, ""]:
            raise ValueError(f"Eksik alan: {k}")

    # --- Inputs ---
    arsa = float(inputs["arsa_alani_m2"])
    emsal = float(inputs["emsal"])

    satilabilir_katsayi = float(inputs.get("satilabilir_katsayi", DEFAULTS["satilabilir_katsayi"]))

    otopark_tipi: OtoparkTipi = inputs["otopark_tipi"]
    otopark_katsayi = float(inputs.get("otopark_katsayi", DEFAULTS["otopark_katsayi"][otopark_tipi]))

    konut_sinifi: KonutSinifi = inputs["konut_sinifi"]
    insaat_maliyet_birim = float(inputs.get("insaat_maliyet_usd_m2", DEFAULTS["insaat_maliyet_usd_m2"][konut_sinifi]))

    arsa_degeri = float(inputs["arsa_toplam_degeri_usd"])
    ort_konut = float(inputs.get("ortalama_konut_m2", DEFAULTS["ortalama_konut_m2"]))

    satis_fiyat_raw = inputs.get("satis_birim_fiyat_usd_m2", None)
    satis_fiyat = float(satis_fiyat_raw) if satis_fiyat_raw not in [None, ""] else None

    # --- Core areas ---
    emsal_insaat = arsa * emsal
    satilabilir = emsal_insaat * satilabilir_katsayi
    insaat_alani = satilabilir * otopark_katsayi

    # --- Costs ---
    insaat_maliyeti = insaat_alani * insaat_maliyet_birim
    toplam_maliyet = insaat_maliyeti + arsa_degeri

    # --- Units / apartment count (integer) ---
    konut_adedi_raw = satilabilir / ort_konut if ort_konut > 0 else 0.0
    konut_adedi = math.floor(konut_adedi_raw) if konut_adedi_raw > 0 else 0
    kalan_alan = satilabilir - (konut_adedi * ort_konut) if konut_adedi > 0 else satilabilir

    # --- Breakeven and target sales prices (USD/m²) ---
    breakeven_usd_m2 = (toplam_maliyet / satilabilir) if satilabilir > 0 else 0.0

    def target_price_usd_m2(margin: float) -> float:
        # margin is gross profitability target: kar/maliyet
        required_revenue = toplam_maliyet * (1.0 + margin)
        return (required_revenue / satilabilir) if satilabilir > 0 else 0.0

    target_10 = target_price_usd_m2(0.10)
    target_30 = target_price_usd_m2(0.30)
    target_50 = target_price_usd_m2(0.50)

    # --- USD->TRY conversions (if rate provided) ---
    def to_try(x_usd: Optional[float]) -> Optional[float]:
        if x_usd is None:
            return None
        if usd_try_rate is None:
            return None
        return x_usd * float(usd_try_rate)

    outputs: Dict[str, Any] = {
        # Areas
        "emsal_insaat_alani_m2": emsal_insaat,
        "satilabilir_alan_m2": satilabilir,
        "toplam_insaat_alani_m2": insaat_alani,

        # Costs
        "insaat_maliyeti_usd": insaat_maliyeti,
        "arsa_degeri_usd": arsa_degeri,
        "toplam_proje_maliyeti_usd": toplam_maliyet,

        "insaat_maliyeti_try": to_try(insaat_maliyeti),
        "arsa_degeri_try": to_try(arsa_degeri),
        "toplam_proje_maliyeti_try": to_try(toplam_maliyet),

        # Units
        "yaklasik_konut_adedi": konut_adedi,
        "kalan_satilabilir_alan_m2": kalan_alan,

        # Breakeven + targets
        "breakeven_usd_m2": breakeven_usd_m2,
        "target_10_usd_m2": target_10,
        "target_30_usd_m2": target_30,
        "target_50_usd_m2": target_50,

        "breakeven_try_m2": to_try(breakeven_usd_m2),
        "target_10_try_m2": to_try(target_10),
        "target_30_try_m2": to_try(target_30),
        "target_50_try_m2": to_try(target_50),

        # Revenue/profit placeholders (filled only if sales price provided)
        "satis_birim_fiyat_usd_m2": satis_fiyat,
        "satis_birim_fiyat_try_m2": to_try(satis_fiyat) if satis_fiyat is not None else None,

        "proje_hasilati_usd": None,
        "proje_hasilati_try": None,
        "proje_kari_usd": None,
        "proje_kari_try": None,
        "brut_karlilik_orani": None,
    }

    # --- Revenue mode (only if valid sales price) ---
    if satis_fiyat is not None and satis_fiyat > 0:
        hasilat = satilabilir * satis_fiyat
        kar = hasilat - toplam_maliyet
        brut_karlilik = (kar / toplam_maliyet) if toplam_maliyet > 0 else 0.0

        outputs.update({
            "proje_hasilati_usd": hasilat,
            "proje_hasilati_try": to_try(hasilat),
            "proje_kari_usd": kar,
            "proje_kari_try": to_try(kar),
            "brut_karlilik_orani": brut_karlilik,
        })

    # --- Warnings (deterministic) ---
    warnings: List[str] = []

    # Sanity checks
    if emsal <= 0:
        warnings.append("⚠️ Emsal 0 veya negatif olamaz.")
    if arsa <= 0:
        warnings.append("⚠️ Arsa alanı 0 veya negatif olamaz.")
    if satilabilir_katsayi <= 0:
        warnings.append("⚠️ Satılabilir katsayı 0 veya negatif olamaz.")
    if otopark_katsayi <= 0:
        warnings.append("⚠️ Otopark katsayısı 0 veya negatif olamaz.")
    if arsa_degeri < 0:
        warnings.append("⚠️ Arsa değeri negatif olamaz.")
    if insaat_maliyet_birim <= 0:
        warnings.append("⚠️ İnşaat maliyeti ($/m²) 0 veya negatif olamaz.")

    # Plausibility hints
    if emsal > 5:
        warnings.append("ℹ️ Emsal oldukça yüksek görünüyor; birimi/doğruluğu kontrol etmek isteyebilirsin.")
    if satilabilir_katsayi < 1.0 or satilabilir_katsayi > 1.6:
        warnings.append("ℹ️ Satılabilir katsayı alışılmadık aralıkta olabilir (örn. 1.10–1.35 sık görülür).")
    if ort_konut < 60 or ort_konut > 250:
        warnings.append("ℹ️ Ortalama konut m² değeri alışılmadık olabilir.")
    if usd_try_rate is None:
        warnings.append("ℹ️ TL karşılıkları için USD/TRY kuru bulunamadı (TL sütunları boş olabilir).")

    # Profitability flags only if revenue mode
    if outputs["brut_karlilik_orani"] is not None:
        gm = float(outputs["brut_karlilik_orani"])
        if gm < 0:
            warnings.append("🚩 Proje zararda görünüyor (brüt karlılık negatif).")
        elif gm < 0.10:
            warnings.append("⚠️ Brüt karlılık %10’un altında (düşük).")
        elif gm < 0.20:
            warnings.append("ℹ️ Brüt karlılık %10–%20 aralığında (orta).")

    return outputs, warnings


def sensitivity(inputs: Dict[str, Any], usd_try_rate: Optional[float] = None) -> Dict[str, Any]:
    """
    Satış ±%10 ve Maliyet ±%10 ile 3x3 duyarlılık tablosu üretir.
    Not: Satış fiyatı yoksa, önce satış fiyatı istemek daha mantıklı.
    """
    base = dict(inputs)
    base_out, _ = compute_outputs(base, usd_try_rate=usd_try_rate)

    sales_mults = [0.9, 1.0, 1.1]
    cost_mults = [0.9, 1.0, 1.1]

    # satış fiyatı yoksa, duyarlılık grid'i kısmen anlamsız kalır
    if base.get("satis_birim_fiyat_usd_m2", None) in [None, ""]:
        return {"base": base_out, "grid": [], "sales_mults": sales_mults, "cost_mults": cost_mults}

    grid = []
    for cm in cost_mults:
        row = []
        for sm in sales_mults:
            tmp = dict(base)
            tmp["satis_birim_fiyat_usd_m2"] = float(base["satis_birim_fiyat_usd_m2"]) * sm

            # maliyet birimi override ediliyorsa onu çarp
            if "insaat_maliyet_usd_m2" in tmp and tmp["insaat_maliyet_usd_m2"] not in [None, ""]:
                tmp["insaat_maliyet_usd_m2"] = float(tmp["insaat_maliyet_usd_m2"]) * cm
            else:
                tmp["insaat_maliyet_usd_m2"] = float(DEFAULTS["insaat_maliyet_usd_m2"][tmp["konut_sinifi"]]) * cm

            out, _ = compute_outputs(tmp, usd_try_rate=usd_try_rate)
            row.append({
                "sales_mult": sm,
                "cost_mult": cm,
                "profit_usd": out["proje_kari_usd"],
                "profit_try": out["proje_kari_try"],
                "gross_margin": out["brut_karlilik_orani"],
            })
        grid.append(row)

    return {"base": base_out, "grid": grid, "sales_mults": sales_mults, "cost_mults": cost_mults}
