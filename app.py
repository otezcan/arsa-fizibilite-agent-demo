import streamlit as st
from openai import OpenAI
from datetime import datetime, date
import hashlib
from typing import Dict, Any, Optional
import xml.etree.ElementTree as ET
from urllib.request import urlopen

from feasibility import compute_outputs, sensitivity, DEFAULTS
from pdf_report import build_pdf

APP_TITLE = "AI Konut Fizibilite Asistanı — Dr. Ömür Tezcan / GGtech"
DEFAULT_DAILY_LIMIT = 5

# ----------------------------
# Helpers
# ----------------------------
def fmt_int(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"{x:,.0f}"

def fmt_usd(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"${x:,.0f}"

def fmt_try(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"₺{x:,.0f}"

def fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"{x*100:.1f}%"

def get_client() -> OpenAI:
    api_key = st.secrets.get("OPENAI_API_KEY", None)
    if not api_key:
        st.error("OPENAI_API_KEY eksik. Streamlit Secrets'e eklemelisin.")
        st.stop()
    return OpenAI(api_key=api_key)

def stable_user_key() -> str:
    # Streamlit Cloud'da IP her zaman net gelmeyebilir → headers + fallback
    try:
        xf = st.context.headers.get("X-Forwarded-For", "")
        ua = st.context.headers.get("User-Agent", "")
    except Exception:
        xf, ua = "", ""
    base = (xf or "") + "|" + (ua or "") + "|" + st.session_state.get("session_fallback", "fallback")
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

@st.cache_resource
def usage_store():
    return {"day": date.today().isoformat(), "counts": {}}

def check_and_increment_quota() -> bool:
    store = usage_store()
    today = date.today().isoformat()
    if store["day"] != today:
        store["day"] = today
        store["counts"] = {}
    key = stable_user_key()
    limit = int(st.secrets.get("DAILY_LIMIT", DEFAULT_DAILY_LIMIT))
    count = store["counts"].get(key, 0)
    if count >= limit:
        return False
    store["counts"][key] = count + 1
    return True

def ensure_defaults(inputs: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(inputs)
    out.setdefault("satilabilir_katsayi", DEFAULTS["satilabilir_katsayi"])
    out.setdefault("ortalama_konut_m2", DEFAULTS["ortalama_konut_m2"])
    if out.get("otopark_tipi") in ["ACIK", "KAPALI"] and "otopark_katsayi" not in out:
        out["otopark_katsayi"] = DEFAULTS["otopark_katsayi"][out["otopark_tipi"]]
    if out.get("konut_sinifi") in ["ALT", "ORTA", "YUKSEK"] and "insaat_maliyet_usd_m2" not in out:
        out["insaat_maliyet_usd_m2"] = DEFAULTS["insaat_maliyet_usd_m2"][out["konut_sinifi"]]
    return out

# ----------------------------
# TCMB USD/TRY Fetch
# ----------------------------
TCMB_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"  # :contentReference[oaicite:1]{index=1}

@st.cache_data(ttl=60 * 30)  # 30 dakika cache
def fetch_usd_try_from_tcmb() -> Dict[str, Optional[str]]:
    """
    Returns:
      {
        "rate": float | None,
        "date": "DD.MM.YYYY" | None,
        "source": "TCMB today.xml" 
      }
    Not: TCMB XML'de USD için ForexSelling / ForexBuying bulunur.
    Biz "ForexSelling" (satış) değerini kullanıyoruz (daha muhafazakar).
    """
    try:
        with urlopen(TCMB_URL, timeout=10) as r:
            xml_bytes = r.read()
        root = ET.fromstring(xml_bytes)
        tarih = root.attrib.get("Tarih", None)

        usd_node = None
        for cur in root.findall("Currency"):
            code = cur.attrib.get("CurrencyCode", "")
            if code == "USD":
                usd_node = cur
                break

        if usd_node is None:
            return {"rate": None, "date": tarih, "source": "TCMB today.xml"}

        selling = usd_node.findtext("ForexSelling")
        buying = usd_node.findtext("ForexBuying")

        # Selling öncelik; yoksa buying
        val = selling or buying
        if val is None:
            return {"rate": None, "date": tarih, "source": "TCMB today.xml"}

        rate = float(val.strip())
        return {"rate": rate, "date": tarih, "source": "TCMB today.xml"}

    except Exception:
        return {"rate": None, "date": None, "source": "TCMB today.xml"}

# ----------------------------
# LLM Tool for patch
# ----------------------------
PARSE_TOOL = {
    "type": "function",
    "function": {
        "name": "patch_inputs",
        "description": "Kullanıcı mesajından fizibilite girdilerini çıkart ve mevcut inputs üzerine uygulanacak patch üret.",
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "object",
                    "description": "Sadece bulunan alanları ekle. Bulamadıklarını ekleme.",
                    "properties": {
                        "arsa_alani_m2": {"type": "number"},
                        "emsal": {"type": "number"},
                        "satilabilir_katsayi": {"type": "number"},
                        "otopark_tipi": {"type": "string", "enum": ["ACIK", "KAPALI"]},
                        "otopark_katsayi": {"type": "number"},
                        "satis_birim_fiyat_usd_m2": {"type": "number"},
                        "konut_sinifi": {"type": "string", "enum": ["ALT", "ORTA", "YUKSEK"]},
                        "insaat_maliyet_usd_m2": {"type": "number"},
                        "arsa_toplam_degeri_usd": {"type": "number"},
                        "ortalama_konut_m2": {"type": "number"},
                    },
                    "additionalProperties": False
                },
                "explanations": {"type": "array", "items": {"type": "string"}},
                "next_questions": {"type": "array", "items": {"type": "string"}},
                "confirmations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["patch", "next_questions", "confirmations", "explanations"],
            "additionalProperties": False
        }
    }
}

AGENT_SYSTEM = """
Sen bir “Konut Fizibilite Asistanı”sın. Amaç: kullanıcıdan girdileri pratik şekilde toplayıp, kabulleri netleştirip, sonuçları kısa ve anlaşılır sunmak.

Kritik akış:
1) İlk mesajında kullanıcıdan aşağıdaki şablonu tek seferde doldurmasını iste:
   - Arsa alanı (m²)
   - Emsal
   - Otopark (Açık/Kapalı)
   - Konut sınıfı (Alt/Orta/Yüksek)
   - Arsa değeri ($)
   - (Opsiyonel) Ortalama konut m² (default 120)
2) Satış fiyatını ilk turda isteme.
   Önce: başabaş satış fiyatı + %10/%30/%50 hedef satış fiyatlarını göster.
   Sonra: “Hangi satış fiyatıyla çalışalım?” diye sor.
3) Kullanıcı anlamsız değer girerse nazikçe teyit iste (emsal>5, arsa alanı çok küçük, vb.)

Kurallar:
- Matematiksel hesap yapma. Arayüz sonuç paneli hesaplayacak.
- patch_inputs tool’u ile sadece patch üret.
Dil: Türkçe, net, premium ton (kısa, maddeli).
"""

def llm_extract_patch(client: OpenAI, user_text: str, current_inputs: Dict[str, Any]) -> Dict[str, Any]:
    import json
    resp = client.chat.completions.create(
        model=st.secrets.get("OPENAI_MODEL", "gpt-4.1-mini"),
        messages=[
            {"role": "system", "content": AGENT_SYSTEM},
            {"role": "user", "content": f"Mevcut inputs: {current_inputs}\n\nKullanıcı mesajı: {user_text}"}
        ],
        tools=[PARSE_TOOL],
        tool_choice="required",
        temperature=0.2
    )
    msg = resp.choices[0].message
    tool_call = msg.tool_calls[0]
    data = json.loads(tool_call.function.arguments)
    return data

def merge_patch(inputs: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(inputs)
    for k, v in patch.items():
        merged[k] = v
    return ensure_defaults(merged)

def compute_if_possible(inputs: Dict[str, Any], usd_try_rate: Optional[float]):
    must = ["arsa_alani_m2", "emsal", "otopark_tipi", "konut_sinifi", "arsa_toplam_degeri_usd"]
    if not all(k in inputs and inputs[k] not in [None, ""] for k in must):
        return None
    outputs, warnings = compute_outputs(inputs, usd_try_rate=usd_try_rate)
    return {"outputs": outputs, "warnings": warnings}

# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title="AI Konut Fizibilite", layout="wide")

# Subtle premium CSS
st.markdown("""
<style>
.block-container {padding-top: 1.2rem;}
h1, h2, h3 {letter-spacing: -0.02em;}
.kpi-card {border: 1px solid #E6EAF0; border-radius: 16px; padding: 14px 14px; background: white;}
.muted {color:#667085;}
</style>
""", unsafe_allow_html=True)

st.markdown(f"## {APP_TITLE}")
st.markdown('<div class="muted">Kur otomatik TCMB • USD & TL sonuç • Başabaş ve hedef fiyat önerileri</div>', unsafe_allow_html=True)

# session init
if "session_fallback" not in st.session_state:
    st.session_state.session_fallback = hashlib.sha256(str(datetime.now()).encode()).hexdigest()

if "inputs" not in st.session_state:
    st.session_state.inputs = ensure_defaults({})
if "messages" not in st.session_state:
    st.session_state.messages = []
if "initialized" not in st.session_state:
    st.session_state.initialized = False

client = get_client()

# Fetch TCMB rate
tcmb = fetch_usd_try_from_tcmb()
auto_rate = tcmb.get("rate", None)
rate_date = tcmb.get("date", None)
rate_source = tcmb.get("source", "TCMB today.xml")

col_chat, col_form = st.columns([1.08, 1])

# -------- Chat Panel --------
with col_chat:
    st.subheader("💬 Asistan")

    if not st.session_state.initialized:
        st.session_state.initialized = True
        intro = (
            "Merhaba! Konut projesi için hızlı fizibilite çıkaralım.\n\n"
            "**Lütfen şu formatta tek mesajda yaz:**\n"
            "- Arsa alanı (m²)\n"
            "- Emsal\n"
            "- Otopark (Açık/Kapalı)\n"
            "- Konut sınıfı (Alt/Orta/Yüksek)\n"
            "- Arsa değeri ($)\n"
            "- (Opsiyonel) Ortalama konut m² (default 120)\n\n"
            "Satış fiyatını en sonda isteyeceğim; önce başabaş ve hedef fiyatları göstereceğim."
        )
        st.session_state.messages.append({"role": "assistant", "content": intro})

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    user_text = st.chat_input("Bilgileri yaz veya bir değeri güncelle (örn: emsal 2.0)")
    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})

        data = llm_extract_patch(client, user_text, st.session_state.inputs)
        patch = data.get("patch", {})
        explanations = data.get("explanations", [])
        confirmations = data.get("confirmations", [])
        next_qs = data.get("next_questions", [])

        st.session_state.inputs = merge_patch(st.session_state.inputs, patch)

        usd_try_rate = auto_rate
        result = compute_if_possible(st.session_state.inputs, usd_try_rate)

        if result:
            if not check_and_increment_quota():
                st.session_state.messages.append({"role": "assistant", "content": "Bugünlük kullanım limitine ulaşıldı. Yarın tekrar deneyebilirsin."})
                st.rerun()

            outs = result["outputs"]
            warns = result["warnings"]

            lines = []
            if explanations:
                lines.append("**Anladıklarım**")
                lines += [f"- {e}" for e in explanations]
            if confirmations:
                lines.append("\n**Kabuller**")
                lines += [f"- {c}" for c in confirmations]

            # Core outputs
            lines.append("\n**Hızlı Özet**")
            lines.append(f"- Satılabilir alan: **{fmt_int(outs.get('satilabilir_alan_m2'))} m²**")
            lines.append(f"- Toplam proje maliyeti: **{fmt_usd(outs.get('toplam_proje_maliyeti_usd'))}**  /  **{fmt_try(outs.get('toplam_proje_maliyeti_try'))}**")
            lines.append(f"- Başabaş satış: **{fmt_int(outs.get('breakeven_usd_m2'))} $/m²**  /  **{fmt_int(outs.get('breakeven_try_m2'))} ₺/m²**")

            lines.append("\n**Hedef Satış Fiyatları (Brüt kârlılık)**")
            lines.append(f"- %10: **{fmt_int(outs.get('target_10_usd_m2'))} $/m²** / **{fmt_int(outs.get('target_10_try_m2'))} ₺/m²**")
            lines.append(f"- %30: **{fmt_int(outs.get('target_30_usd_m2'))} $/m²** / **{fmt_int(outs.get('target_30_try_m2'))} ₺/m²**")
            lines.append(f"- %50: **{fmt_int(outs.get('target_50_usd_m2'))} $/m²** / **{fmt_int(outs.get('target_50_try_m2'))} ₺/m²**")

            # Ask for sales price if missing
            if not outs.get("satis_birim_fiyat_usd_m2"):
                lines.append("\nŞimdi hangi **satış fiyatıyla** çalışalım? (örn: **2200 $/m²** veya **95.000 ₺/m²**)")

            # warnings
            if warns:
                lines.append("\n**Notlar/Uyarılar**")
                lines += [f"- {w}" for w in warns]

            st.session_state.messages.append({"role": "assistant", "content": "\n".join(lines)})
        else:
            ask = []
            if explanations:
                ask.append("**Anladıklarım**\n" + "\n".join([f"- {e}" for e in explanations]))
            if next_qs:
                ask.append("**Devam**\n" + "\n".join([f"- {q}" for q in next_qs]))
            else:
                ask.append("Devam edelim: Arsa alanı (m²), emsal, otopark tipi, konut sınıfı ve arsa değerini yazar mısın?")
            st.session_state.messages.append({"role": "assistant", "content": "\n\n".join(ask)})

        st.rerun()

# -------- Form + Results Panel --------
with col_form:
    st.subheader("📌 Girdiler & Sonuçlar")

    # Kur kutusu: otomatik + override
    st.markdown("**Kur (USD/TRY)**")
    c1, c2 = st.columns([1, 1])
    with c1:
        st.metric("Otomatik (TCMB)", "-" if auto_rate is None else f"{auto_rate:.4f} TL")
        if rate_date:
            st.caption(f"Tarih: {rate_date} • Kaynak: {rate_source}")
    with c2:
        override = st.checkbox("Manuel kur kullan", value=False)
        manual_rate = st.number_input("Manuel USD/TRY", value=float(auto_rate or 0.0), step=0.10, format="%.2f", disabled=not override)

    usd_try_rate = manual_rate if override else auto_rate

    st.divider()
    st.markdown("### Girdiler (Form)")

    inp = st.session_state.inputs

    arsa = st.number_input("Arsa Alanı (m²)", value=float(inp.get("arsa_alani_m2", 0.0) or 0.0), step=100.0)
    emsal = st.number_input("Emsal", value=float(inp.get("emsal", 0.0) or 0.0), step=0.05, format="%.2f")

    sat_kats = st.number_input("Satılabilir Alan Katsayısı", value=float(inp.get("satilabilir_katsayi", 1.25)), step=0.01, format="%.2f")

    otopark_tipi = st.selectbox("Otopark Tipi", ["ACIK", "KAPALI"], index=0 if inp.get("otopark_tipi","ACIK")=="ACIK" else 1)
    default_ot_kats = DEFAULTS["otopark_katsayi"][otopark_tipi]
    ot_kats = st.number_input("Otopark Katsayısı", value=float(inp.get("otopark_katsayi", default_ot_kats)), step=0.05, format="%.2f")

    konut_sinifi = st.selectbox("Konut Sınıfı", ["ALT", "ORTA", "YUKSEK"], index=["ALT","ORTA","YUKSEK"].index(inp.get("konut_sinifi","ORTA")))
    default_cost = DEFAULTS["insaat_maliyet_usd_m2"][konut_sinifi]
    cost = st.number_input("İnşaat Maliyeti ($/m²)", value=float(inp.get("insaat_maliyet_usd_m2", default_cost)), step=25.0)

    arsa_degeri = st.number_input("Arsa Toplam Değeri ($)", value=float(inp.get("arsa_toplam_degeri_usd", 0.0) or 0.0), step=100000.0)
    ort_konut = st.number_input("Ortalama Konut (m²)", value=float(inp.get("ortalama_konut_m2", 120.0)), step=5.0)

    # Satış fiyatı: artık opsiyonel
    satis = st.number_input("Satış Birim Fiyatı ($/m²) — opsiyonel", value=float(inp.get("satis_birim_fiyat_usd_m2", 0.0) or 0.0), step=50.0)

    if st.button("🔄 Güncelle ve Hesapla", use_container_width=True):
        st.session_state.inputs = ensure_defaults({
            "arsa_alani_m2": arsa,
            "emsal": emsal,
            "satilabilir_katsayi": sat_kats,
            "otopark_tipi": otopark_tipi,
            "otopark_katsayi": ot_kats,
            "konut_sinifi": konut_sinifi,
            "insaat_maliyet_usd_m2": cost,
            "arsa_toplam_degeri_usd": arsa_degeri,
            "ortalama_konut_m2": ort_konut,
            "satis_birim_fiyat_usd_m2": (satis if satis > 0 else None),
        })
        if not check_and_increment_quota():
            st.error("Bugünlük kullanım limitine ulaşıldı. Yarın tekrar deneyebilirsin.")
        st.rerun()

    st.divider()
    st.markdown("### Sonuçlar")

    result = compute_if_possible(st.session_state.inputs, usd_try_rate)
    if result:
        outs = result["outputs"]
        warns = result["warnings"]

        # KPI cards
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown('<div class="kpi-card">Satılabilir Alan<br><b>' + f"{fmt_int(outs.get('satilabilir_alan_m2'))} m²" + '</b></div>', unsafe_allow_html=True)
        with k2:
            st.markdown('<div class="kpi-card">Toplam İnşaat Alanı<br><b>' + f"{fmt_int(outs.get('toplam_insaat_alani_m2'))} m²" + '</b></div>', unsafe_allow_html=True)
        with k3:
            st.markdown('<div class="kpi-card">Toplam Maliyet (USD)<br><b>' + f"{fmt_usd(outs.get('toplam_proje_maliyeti_usd'))}" + '</b></div>', unsafe_allow_html=True)
        with k4:
            st.markdown('<div class="kpi-card">Toplam Maliyet (TL)<br><b>' + f"{fmt_try(outs.get('toplam_proje_maliyeti_try'))}" + '</b></div>', unsafe_allow_html=True)

        st.caption(f"Konut adedi (tam): {int(outs.get('yaklasik_konut_adedi') or 0)} • Kalan satılabilir alan: {fmt_int(outs.get('kalan_satilabilir_alan_m2'))} m²")

        st.divider()
        st.subheader("🎯 Başabaş ve Önerilen Satış Fiyatları (USD / TL)")

        table = [
            ["Hedef", "USD/m²", "TL/m²"],
            ["Başabaş", f"{fmt_int(outs.get('breakeven_usd_m2'))}", f"{fmt_int(outs.get('breakeven_try_m2'))}"],
            ["%10 Brüt Karlılık", f"{fmt_int(outs.get('target_10_usd_m2'))}", f"{fmt_int(outs.get('target_10_try_m2'))}"],
            ["%30 Brüt Karlılık", f"{fmt_int(outs.get('target_30_usd_m2'))}", f"{fmt_int(outs.get('target_30_try_m2'))}"],
            ["%50 Brüt Karlılık", f"{fmt_int(outs.get('target_50_usd_m2'))}", f"{fmt_int(outs.get('target_50_try_m2'))}"],
        ]
        st.table(table)

        # Revenue mode
        if outs.get("satis_birim_fiyat_usd_m2"):
            st.divider()
            st.subheader("💰 Gelir ve Kârlılık (Seçilen Satış Fiyatına Göre)")

            r1, r2, r3 = st.columns(3)
            r1.metric("Hasılat (USD)", fmt_usd(outs.get("proje_hasilati_usd")))
            r2.metric("Kâr (USD)", fmt_usd(outs.get("proje_kari_usd")))
            r3.metric("Brüt Karlılık", fmt_pct(outs.get("brut_karlilik_orani")))

            if outs.get("proje_hasilati_try") is not None:
                st.caption(f"Hasılat (TL): {fmt_try(outs.get('proje_hasilati_try'))} • Kâr (TL): {fmt_try(outs.get('proje_kari_try'))}")

        if warns:
            st.warning("\n".join(warns))

        # Sensitivity (only meaningful if sales price exists)
        if outs.get("satis_birim_fiyat_usd_m2"):
            st.divider()
            st.subheader("📈 Duyarlılık (±%10)")
            sens = sensitivity(st.session_state.inputs, usd_try_rate=usd_try_rate)
            grid = sens.get("grid", [])
            if grid:
                sales_mults = sens["sales_mults"]
                cost_mults = sens["cost_mults"]
                header = ["Maliyet \\ Satış"] + [f"{int(sm*100)}%" for sm in sales_mults]
                t2 = [header]
                for i, cm in enumerate(cost_mults):
                    row = [f"{int(cm*100)}%"]
                    for j, _sm in enumerate(sales_mults):
                        row.append(f"{(grid[i][j]['profit_usd'] or 0):,.0f}")
                    t2.append(row)
                st.write("Kar (USD) tablosu:")
                st.table(t2)

        st.divider()
        if st.button("📄 PDF Rapor Oluştur", use_container_width=True):
            pdf_path = "konut_fizibilite_raporu.pdf"
            build_pdf(
                path=pdf_path,
                project_title="Konut Projesi Fizibilite",
                inputs=st.session_state.inputs,
                outputs=outs,
                warnings=warns,
                usd_try_rate=usd_try_rate,
                rate_source=(rate_source if usd_try_rate is not None else None),
            )
            with open(pdf_path, "rb") as f:
                st.download_button("PDF’i indir", data=f, file_name="konut_fizibilite_raporu.pdf", mime="application/pdf", use_container_width=True)

    else:
        st.info("Hesap için: Arsa alanı, emsal, otopark tipi, konut sınıfı ve arsa değerini girmen yeterli.")
