import streamlit as st
from openai import OpenAI
from datetime import datetime, date
import hashlib
from typing import Dict, Any, List, Optional

from feasibility import compute_outputs, sensitivity, DEFAULTS
from pdf_report import build_pdf

# ----------------------------
# Demo ayarları
# ----------------------------
APP_TITLE = "AI Konut Fizibilite Agent (DEMO)"
DEFAULT_DAILY_LIMIT = 5

def get_client() -> OpenAI:
    # Streamlit secrets ile güvenli okuma
    api_key = st.secrets.get("OPENAI_API_KEY", None)
    if not api_key:
        st.error("OPENAI_API_KEY eksik. Streamlit Secrets'e eklemelisin.")
        st.stop()
    return OpenAI(api_key=api_key)

def stable_ip_id() -> str:
    # Streamlit Cloud'da gerçek IP'ye her zaman erişemeyebilirsin.
    # Yine de header denemesi + fallback ile "yaklaşık" bir kullanıcı anahtarı üretir.
    try:
        ip = st.context.headers.get("X-Forwarded-For", "") or st.context.headers.get("Remote-Addr", "")
    except Exception:
        ip = ""
    base = ip + st.session_state.get("ua", "")
    if not base:
        base = st.session_state.get("session_fallback", "fallback")
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]

@st.cache_resource
def usage_store():
    # Basit in-memory store (demo için yeterli)
    return {"day": date.today().isoformat(), "counts": {}}

def check_and_increment_quota() -> bool:
    store = usage_store()
    today = date.today().isoformat()
    if store["day"] != today:
        store["day"] = today
        store["counts"] = {}

    key = stable_ip_id()
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
    # otopark tipi varsa katsayıyı defaultla
    if out.get("otopark_tipi") in ["ACIK", "KAPALI"] and "otopark_katsayi" not in out:
        out["otopark_katsayi"] = DEFAULTS["otopark_katsayi"][out["otopark_tipi"]]
    # konut sınıfı varsa maliyeti defaultla
    if out.get("konut_sinifi") in ["ALT", "ORTA", "YUKSEK"] and "insaat_maliyet_usd_m2" not in out:
        out["insaat_maliyet_usd_m2"] = DEFAULTS["insaat_maliyet_usd_m2"][out["konut_sinifi"]]
    return out

def merge_patch(inputs: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(inputs)
    for k, v in patch.items():
        merged[k] = v
    return ensure_defaults(merged)

# ----------------------------
# LLM Tool (function)
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
                "explanations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Kısa açıklamalar: hangi alanı nasıl anladın."
                },
                "next_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Eksik alanlar için sıradaki soru önerileri."
                },
                "confirmations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Varsayım/kat sayı onayı için kısa cümleler."
                }
            },
            "required": ["patch", "next_questions", "confirmations", "explanations"],
            "additionalProperties": False
        }
    }
}

AGENT_SYSTEM = """
Sen bir “Konut Fizibilite Agent”sın. Kullanıcıdan girdileri adım adım alırsın, her adımda varsayımı açıklarsın ve değişiklik isterse patch önerirsin.
Kurallar:
- Matematik hesaplaması yapma. Hesap için arayüzdeki backend/compute fonksiyonu kullanılacak.
- Kullanıcı mesajından sayısal/verisel alanları patch_inputs tool’u ile yapılandırılmış olarak çıkar.
- Eksik alanları sırayla sor:
  1) arsa_alani_m2
  2) emsal
  3) satilabilir_katsayi (default 1.25)
  4) otopark_tipi (ACIK/KAPALI) -> default katsayı: ACIK 1.20, KAPALI 1.60
  5) satis_birim_fiyat_usd_m2
  6) konut_sinifi (ALT/ORTA/YUKSEK) -> default maliyet: 700/900/1100
  7) arsa_toplam_degeri_usd
  8) ortalama_konut_m2 (default 120)
- Her adımda: “Bu adımda şu varsayımı kullandım: ... Değiştirmek ister misiniz?” diye sor.
Dil: Türkçe, net, kısa, yönlendirici.
"""

def llm_extract_patch(client: OpenAI, user_text: str, current_inputs: Dict[str, Any]) -> Dict[str, Any]:
    # Basit: modelden tool çağrısı bekliyoruz (tool_choice: required).
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
    args = tool_call.function.arguments
    # arguments JSON string; OpenAI python SDK bunu string döndürebilir
    import json
    data = json.loads(args)
    return data

def compute_if_possible(inputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    must = ["arsa_alani_m2","emsal","otopark_tipi","satis_birim_fiyat_usd_m2","konut_sinifi","arsa_toplam_degeri_usd"]
    if not all(k in inputs and inputs[k] not in [None, ""] for k in must):
        return None
    outputs, warnings = compute_outputs(inputs)
    return {"outputs": outputs, "warnings": warnings}

# ----------------------------
# UI
# ----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# User-agent-ish seed
if "ua" not in st.session_state:
    st.session_state.ua = st.context.headers.get("User-Agent", "") if hasattr(st, "context") else ""
if "session_fallback" not in st.session_state:
    st.session_state.session_fallback = hashlib.sha256(str(datetime.now()).encode()).hexdigest()

# Demo şifre gate
with st.sidebar:
    st.header("Demo Girişi")
    demo_pw = st.text_input("Demo şifresi", type="password")
    expected = st.secrets.get("DEMO_PASSWORD", "")
    if expected and demo_pw != expected:
        st.warning("Şifreyi girince demo açılır.")
        st.stop()

    st.caption("Kısıt: IP başına günlük kota + PDF filigran")
    st.write(f"Günlük limit: **{int(st.secrets.get('DAILY_LIMIT', DEFAULT_DAILY_LIMIT))}** hesap")

# state init
if "inputs" not in st.session_state:
    st.session_state.inputs = ensure_defaults({})
if "messages" not in st.session_state:
    st.session_state.messages = []

client = get_client()

col_chat, col_form = st.columns([1.15, 1])

# -------- Chat Panel --------
with col_chat:
    st.subheader("💬 Agent ile Sohbet")

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    user_text = st.chat_input("Örn: Arsa 5000 m2, emsal 1.8, kapalı otopark...")

    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})

        # Kota: sadece hesap yapılacağı zaman sayalım. Yine de spamı azaltmak için burada da check edebilirsin.
        with st.chat_message("assistant"):
            st.markdown("Mesajını aldım, bilgileri çıkarıyorum…")

        # LLM -> patch
        data = llm_extract_patch(client, user_text, st.session_state.inputs)
        patch = data.get("patch", {})
        explanations = data.get("explanations", [])
        confirmations = data.get("confirmations", [])
        next_qs = data.get("next_questions", [])

        st.session_state.inputs = merge_patch(st.session_state.inputs, patch)

        # Hesap mümkün mü?
        result = compute_if_possible(st.session_state.inputs)
        if result:
            # Kota artır: hesap yapılan an
            if not check_and_increment_quota():
                st.session_state.messages.append({"role": "assistant", "content": "Günlük demo limitine ulaştın. Yarın tekrar deneyebilirsin. 🙏"})
            else:
                outs = result["outputs"]
                warns = result["warnings"]

                reply = []
                if explanations:
                    reply.append("**Anladıklarım:**\n" + "\n".join([f"- {e}" for e in explanations]))
                if confirmations:
                    reply.append("**Varsayımlar / Kabuller:**\n" + "\n".join([f"- {c}" for c in confirmations]))

                reply.append("**Güncel Sonuçlar (Özet):**")
                reply.append(f"- Satılabilir Alan: **{outs['satilabilir_alan_m2']:.0f} m²**")
                reply.append(f"- Toplam İnşaat Alanı: **{outs['toplam_insaat_alani_m2']:.0f} m²**")
                reply.append(f"- Hasılat: **${outs['proje_hasilati_usd']:,.0f}**")
                reply.append(f"- Toplam Maliyet: **${outs['toplam_proje_maliyeti_usd']:,.0f}**")
                reply.append(f"- Kar: **${outs['proje_kari_usd']:,.0f}**")
                reply.append(f"- Brüt Karlılık: **{outs['brut_karlilik_orani']*100:.1f}%**")
                reply.append(f"- Yaklaşık Konut Adedi: **{outs['yaklasik_konut_adedi']:.1f}**")

                if warns:
                    reply.append("**Uyarılar:**\n" + "\n".join([f"- {w}" for w in warns]))

                # Ek öneri: duyarlılık
                reply.append("İstersen satış fiyatı ve maliyet için **±%10 duyarlılık analizini** de gösterebilirim. (Yaz: *duyarlılık*)")

                st.session_state.messages.append({"role": "assistant", "content": "\n".join(reply)})
        else:
            # Eksikler için soru sor
            ask = []
            if explanations:
                ask.append("**Anladıklarım:**\n" + "\n".join([f"- {e}" for e in explanations]))
            if confirmations:
                ask.append("**Varsayımlar / Kabuller:**\n" + "\n".join([f"- {c}" for c in confirmations]))
            if next_qs:
                ask.append("**Devam edelim:**\n" + "\n".join([f"- {q}" for q in next_qs]))
            else:
                ask.append("Devam edelim: Arsa alanı (m²) ve emsal değerini yazar mısın?")
            st.session_state.messages.append({"role": "assistant", "content": "\n".join(ask)})

        st.rerun()

# -------- Form + Results Panel --------
with col_form:
    st.subheader("🧾 Girdiler (İstersen buradan da düzelt)")
    inp = st.session_state.inputs

    arsa = st.number_input("Arsa Alanı (m²)", value=float(inp.get("arsa_alani_m2", 0.0) or 0.0), step=100.0)
    emsal = st.number_input("Emsal", value=float(inp.get("emsal", 0.0) or 0.0), step=0.05, format="%.2f")
    sat_kats = st.number_input("Satılabilir Alan Katsayısı (default 1.25)", value=float(inp.get("satilabilir_katsayi", 1.25)), step=0.01, format="%.2f")

    otopark_tipi = st.selectbox("Otopark Tipi", ["ACIK", "KAPALI"], index=0 if inp.get("otopark_tipi","ACIK")=="ACIK" else 1)
    default_ot_kats = DEFAULTS["otopark_katsayi"][otopark_tipi]
    ot_kats = st.number_input(f"Otopark Katsayısı (default {default_ot_kats})", value=float(inp.get("otopark_katsayi", default_ot_kats)), step=0.05, format="%.2f")

    satis = st.number_input("Satış Birim Fiyatı ($/m²)", value=float(inp.get("satis_birim_fiyat_usd_m2", 0.0) or 0.0), step=50.0)

    konut_sinifi = st.selectbox("Konut Sınıfı", ["ALT", "ORTA", "YUKSEK"], index=["ALT","ORTA","YUKSEK"].index(inp.get("konut_sinifi","ORTA")))
    default_cost = DEFAULTS["insaat_maliyet_usd_m2"][konut_sinifi]
    cost = st.number_input(f"İnşaat Maliyeti ($/m²) (default {default_cost})", value=float(inp.get("insaat_maliyet_usd_m2", default_cost)), step=25.0)

    arsa_degeri = st.number_input("Arsa Toplam Değeri ($)", value=float(inp.get("arsa_toplam_degeri_usd", 0.0) or 0.0), step=100000.0)
    ort_konut = st.number_input("Ortalama Konut (m²) (default 120)", value=float(inp.get("ortalama_konut_m2", 120.0)), step=5.0)

    if st.button("🔄 Formdan Güncelle ve Hesapla"):
        st.session_state.inputs = ensure_defaults({
            "arsa_alani_m2": arsa,
            "emsal": emsal,
            "satilabilir_katsayi": sat_kats,
            "otopark_tipi": otopark_tipi,
            "otopark_katsayi": ot_kats,
            "satis_birim_fiyat_usd_m2": satis,
            "konut_sinifi": konut_sinifi,
            "insaat_maliyet_usd_m2": cost,
            "arsa_toplam_degeri_usd": arsa_degeri,
            "ortalama_konut_m2": ort_konut,
        })
        # Kota burada da sayılır (hesap)
        if not check_and_increment_quota():
            st.error("Günlük demo limitine ulaştın. Yarın tekrar deneyebilirsin.")
        st.rerun()

    st.divider()
    st.subheader("📊 Sonuçlar")

    result = compute_if_possible(st.session_state.inputs)
    if result:
        outs = result["outputs"]
        warns = result["warnings"]

        c1, c2 = st.columns(2)
        c1.metric("Satılabilir Alan (m²)", f"{outs['satilabilir_alan_m2']:.0f}")
        c2.metric("Brüt Karlılık", f"{outs['brut_karlilik_orani']*100:.1f}%")

        c3, c4 = st.columns(2)
        c3.metric("Hasılat ($)", f"{outs['proje_hasilati_usd']:,.0f}")
        c4.metric("Toplam Maliyet ($)", f"{outs['toplam_proje_maliyeti_usd']:,.0f}")

        st.metric("Kar ($)", f"{outs['proje_kari_usd']:,.0f}")
        st.caption(f"Yaklaşık konut adedi: {outs['yaklasik_konut_adedi']:.1f}")

        if warns:
            st.warning("\n".join(warns))

        # PDF
        if st.button("📄 PDF Rapor Oluştur"):
            pdf_path = "fizibilite_demo_rapor.pdf"
            build_pdf(
                path=pdf_path,
                title="Konut Projesi Fizibilite Raporu (DEMO)",
                inputs=st.session_state.inputs,
                outputs=outs,
                warnings=warns
            )
            with open(pdf_path, "rb") as f:
                st.download_button("PDF’i indir", data=f, file_name="konut_fizibilite_demo.pdf", mime="application/pdf")

        # Ek öneri: Duyarlılık analizi
        st.divider()
        st.subheader("📈 Duyarlılık (±%10)")
        sens = sensitivity(st.session_state.inputs)
        st.write("Satış (kolon) ve maliyet (satır) çarpanlarına göre **kar ($)**:")
        sales_mults = sens["sales_mults"]
        cost_mults = sens["cost_mults"]
        grid = sens["grid"]

        # basit tablo
        header = ["Maliyet \\ Satış"] + [f"{int(sm*100)}%" for sm in sales_mults]
        table = [header]
        for i, cm in enumerate(cost_mults):
            row = [f"{int(cm*100)}%"]
            for j, _sm in enumerate(sales_mults):
                row.append(f"{grid[i][j]['profit_usd']:,.0f}")
            table.append(row)
        st.table(table)

    else:
        st.info("Hesap için gerekli alanları doldurdukça burada sonuçları göreceksin.")

