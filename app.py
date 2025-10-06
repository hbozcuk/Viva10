# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 15:50:18 2025

@author: hbozcuk
"""

import subprocess, math, os
import gradio as gr
from pathlib import Path

# =========================
# 0) R 'preventr' Kurulumu
# =========================
def ensure_preventr():
    # Kurulu mu?
    try:
        subprocess.run(
            ["Rscript", "-e",
             'quit(save="no", status=ifelse(requireNamespace("preventr", quietly=TRUE), 0, 10))'],
            check=True
        )
    except subprocess.CalledProcessError:
        # Değilse kur
        subprocess.run(
            ["Rscript", "-e",
             'options(repos=c(CRAN="https://cloud.r-project.org")); '
             'install.packages("preventr", Ncpus=2)'],
            check=True
        )
ensure_preventr()

# =========================
# 1) Yardımcı Fonksiyonlar
# =========================
def clamp(x, lo, hi): return max(lo, min(hi, x))

def vki_hesapla_kg_m2(kilo_kg: float, boy_cm: float) -> float:
    boy_m = boy_cm / 100.0
    if boy_m <= 0: raise ValueError("Boy (cm) > 0 olmalı.")
    return kilo_kg / (boy_m ** 2)

def egfr_ckd_epi_2021(cinsiyet: str, yas: int, kreatinin_mg_dl: float) -> float:
    s = (cinsiyet or "").strip().lower()
    if s in ("erkek", "male"):
        K = 0.9; alpha = -0.302
    elif s in ("kadın", "kadin", "female"):
        K = 0.7; alpha = -0.241
    else:
        raise ValueError("Cinsiyet 'erkek/kadın' veya 'male/female' olmalı.")
    if yas < 18 or kreatinin_mg_dl <= 0: raise ValueError("Geçersiz yaş/kreatinin.")
    oran = kreatinin_mg_dl / K
    egfr = 142.0 * (min(oran, 1.0) ** alpha) * (max(oran, 1.0) ** -1.2) * (0.9938 ** yas)
    if s in ("kadın", "kadin", "female"):
        egfr *= 1.012
    return egfr  # mL/dk/1.73m^2

def chd_grup(y): return "düşük risk" if y < 5 else ("orta risk" if y < 20 else "belirgin risk")
def kanser_grup(y): return "düşük risk" if y < 5 else ("orta risk" if y < 10 else "belirgin risk")
def chd_group_en(y): return "low risk" if y < 5 else ("intermediate risk" if y < 20 else "high risk")
def cancer_group_en(y): return "low risk" if y < 5 else ("intermediate risk" if y < 10 else "high risk")

def _is_moderate_like(lbl: str) -> bool:
    et = (lbl or "").strip().lower()
    return et in {"hafif ya da orta", "hafif-orta", "hafif", "orta", "moderate"}

def _is_none_like(lbl: str) -> bool:
    et = (lbl or "").strip().lower()
    return et in {"yok ya da yoka yakın","yok","yoka yakın","hiç","inaktif","sedanter",
                  "none or very little","none","very little","inactive","sedentary"}

def egzersiz_kategori_ve_carpanlar(siddet: str, dakika_hafta: float):
    """
    Kılavuz: ≥150 dk/hafta hafif-orta veya ≥75 dk/hafta ağır
    Yüksek: kılavuzun ≥2 katı
    Dönüş: (kategori, HR_CHD, HR_Kanser)
    """
    it = (siddet or "").strip().lower()
    if _is_none_like(it):
        return ("yetersiz", 1.00, 1.00)
    if not (_is_moderate_like(it) or it in {"ağır", "vigorous"}):
        raise ValueError("Egzersiz 'yok ya da yoka yakın/none', 'hafif ya da orta/moderate' veya 'ağır/vigorous' olmalı.")
    m = max(0.0, float(dakika_hafta or 0))
    is_moderate = _is_moderate_like(it)
    kilavuz = 150.0 if is_moderate else 75.0
    yuksek  = 300.0 if is_moderate else 150.0
    if m >= yuksek:   return ("yüksek", 0.75, 0.89)   # CHD 0.75, Kanser 0.89
    if m >= kilavuz:  return ("kılavuz", 0.84, 0.93)  # CHD 0.84, Kanser 0.93
    return ("yetersiz", 1.00, 1.00)

def yok_var_to01(v: str) -> int:
    s = (v or "").strip().lower()
    return 1 if s in ("var","yes") else 0  # 'yok'/'no' -> 0

# =========================
# 2) PREVENT-CHD (Rscript)
# =========================
R_SCRIPT_PATH = Path("chd_estimate.R")

def prevent_chd_10y(cinsiyet_val, yas, total_chol, hdl, sbp, bp_ilac_01, sigara_01, diyabet_01, statin_01, vki, egfr):
    yas        = int(clamp(yas, 30, 79))
    total_chol = float(clamp(total_chol, 130, 320))
    hdl        = float(clamp(hdl, 20, 100))
    sbp        = float(clamp(sbp, 90, 180))
    egfr       = float(clamp(egfr, 15, 140))
    vki        = float(clamp(vki, 18.5, 39.9))
    sex = "male" if (cinsiyet_val or "").strip().lower() in ("erkek","male") else "female"

    args = ["Rscript", str(R_SCRIPT_PATH), str(yas), sex, f"{sbp:.6f}", str(int(bp_ilac_01)),
            f"{total_chol:.6f}", f"{hdl:.6f}", str(int(statin_01)), str(int(diyabet_01)),
            str(int(sigara_01)), f"{egfr:.6f}", f"{vki:.6f}"]
    out = subprocess.check_output(args, text=True).strip()
    chd_oran_0_1 = float(out)  # 0–1
    if math.isnan(chd_oran_0_1):
        raise ValueError("R dönen değer NaN.")
    return clamp(chd_oran_0_1 * 100.0, 0.0, 100.0)

# =======================================
# 3) Meta-analiz temelli Kanser Riski
# =======================================
def kanser_taban(cinsiyet_val, yas):
    c = (cinsiyet_val or "").strip().lower()
    if c in ("erkek","male"):
        if   yas < 40: return 0.2
        elif yas < 50: return 0.8
        elif yas < 60: return 1.8
        elif yas < 70: return 3.5
        else:          return 6.0
    else:
        if   yas < 40: return 0.15
        elif yas < 50: return 0.6
        elif yas < 60: return 1.5
        elif yas < 70: return 3.0
        else:          return 5.5

def hr_bmi_cancer(bmi: float) -> float:
    # ≥25 kg/m² üzerindeki her +5 kg/m² için ×1.10
    if bmi <= 25.0:
        return 1.00
    return 1.10 ** ((bmi - 25.0) / 5.0)

def hr_alkol(haftalik_ic):
    d = max(0, float(haftalik_ic))
    if d >= 22: return 1.39
    if d >= 15: return 1.19
    if d >= 8:  return 1.08
    if d >= 1:  return 1.02
    return 1.00

def hr_sigara(sigara_01):
    return 1.44 if int(sigara_01) == 1 else 1.00

def hr_egzersiz(kategori: str):
    k = (kategori or "").strip().lower()
    if k == "yüksek":   return 0.89
    if k == "kılavuz":  return 0.93
    return 1.00  # yetersiz

def kanser_riski_meta(cinsiyet_val, yas, bmi, aile_01, alkol_hafta, sigara_01, egzersiz_kategori):
    temel = kanser_taban(cinsiyet_val, yas)
    risk = (temel
            * hr_bmi_cancer(bmi)
            * hr_alkol(alkol_hafta)
            * hr_sigara(sigara_01)
            * hr_egzersiz(egzersiz_kategori))
    if int(aile_01) == 1:
        risk *= 2.0   # yakın akraba öyküsü → ×2
    return min(risk, 25.0)  # demo güvenli tavan

# =======================================
# 4) CHD Post-hoc Modifikasyonlar
# =======================================
ALCOHOL_POSTHOC_CAP_PERCENT = 45.0

def hr_bmi_chd(bmi: float) -> float:
    # 25 kg/m² üzerindeki her +5 kg/m² için ×1.16; altı için 1.00
    if bmi <= 25.0:
        return 1.00
    return 1.16 ** ((bmi - 25.0) / 5.0)

def hr_alkol_chd(units_per_week: float) -> float:
    # Eşik aralıklarında SABİT katsayı
    x = max(0.0, float(units_per_week))
    if x < 3:    return 1.00
    if x < 7:    return 1.15
    if x < 14:   return 1.50
    if x < 21:   return 3.00
    if x < 28:   return 10.00
    return 40.00

# =======================================
# 5) UI Metinleri
# =======================================
def header_md_text(lang):
    if lang == "English":
        return (
            "# **VIVA10**  \n"
            "### 10-year coronary heart disease and cancer risk estimator\n"
            "**Info:** This tool is for educational use and does not provide medical advice. "
            "It uses published evidence and meta-analyses for a personalized estimate.  \n"
            "**Privacy:** Your inputs are not stored on the server."
        )
    else:
        return (
            "# **VİVA10**  \n"
            "### 10 yıllık koroner kalp hastalığı ve kanser riski tahmin uygulaması\n"
            "**Bilgilendirme:** Bu araç eğitim amaçlıdır; kesinleşmiş tıbbi tavsiye sunmaz. "
            "Bilimsel çalışma ve metaanaliz verilerini kullanarak bireyselleştirilmiş risk tahmini yapar.  \n"
            "**Gizlilik:** Girdi verileriniz sunucu tarafında kaydedilmez."
        )

def L(lang="Türkçe"):
    if lang == "English":
        return {
            "gender_label": "Gender",
            "gender_choices": ["male", "female"],
            "age_label": "Age (years)",
            "height_label": "Height (cm)",
            "weight_label": "Weight (kg)",
            "tc_label": "Total Cholesterol (mg/dL)",
            "hdl_label": "HDL (mg/dL)",
            "sbp_label": "Systolic Blood Pressure (mmHg)",
            "cre_label": "Serum Creatinine (mg/dL)",
            "bpmed_label": "Blood pressure medication",
            "statin_label": "Statin",
            "diabetes_label": "Diabetes",
            "smoking_label": "Current smoking",
            "fhx_label": "Family history of cancer (first-degree)",
            "alcohol_label": "Alcohol (drinks/week)",
            "ex_intensity_label": "Exercise intensity",
            "ex_intensity_choices": ["none or very little","moderate","vigorous"],
            "ex_dur_label": "Exercise duration (min/week)",
            "calc_btn": "Calculate",
            "res_chd": "Coronary heart disease (10 years)",
            "res_cancer": "Cancer (10 years)",
        }
    else:
        return {
            "gender_label": "Cinsiyet",
            "gender_choices": ["erkek", "kadın"],
            "age_label": "Yaş (yıl)",
            "height_label": "Boy (cm)",
            "weight_label": "Kilo (kg)",
            "tc_label": "Toplam Kolesterol (mg/dL)",
            "hdl_label": "HDL (mg/dL)",
            "sbp_label": "Sistolik Tansiyon (mmHg)",
            "cre_label": "Serum Kreatinin (mg/dL)",
            "bpmed_label": "Tansiyon ilacı",
            "statin_label": "Statin",
            "diabetes_label": "Diyabet",
            "smoking_label": "Aktif sigara",
            "fhx_label": "Ailede kanser öyküsü (yakın akraba)",
            "alcohol_label": "Alkol (haftalık içki sayısı)",
            "ex_intensity_label": "Egzersiz şiddeti",
            "ex_intensity_choices": ["yok ya da yoka yakın","hafif ya da orta","ağır"],
            "ex_dur_label": "Egzersiz süresi (dk/hafta)",
            "calc_btn": "Hesapla",
            "res_chd": "Koroner kalp hastalığı (10 yıl)",
            "res_cancer": "Kanser (10 yıl)",
        }

def status_text(lang, chd_low, ca_low):
    if lang == "English":
        if chd_low and ca_low:
            return "Your heart disease and cancer risks are low. Keep up the good work and continue optimizing your risk factors."
        elif (not chd_low) and ca_low:
            return ("Your heart disease risk may be elevated. Please consider seeing a cardiology specialist. "
                    "Alternatively, you can book with our heart health specialist "
                    "([WhatsApp: +90 533 143 75 36](https://wa.me/905331437536)).")
        elif chd_low and (not ca_low):
            return ("Your cancer risk may be elevated. Please consider seeing an oncology specialist. "
                    "Alternatively, you can book with our oncology specialist "
                    "([WhatsApp: +90 554 500 43 33](https://wa.me/905545004333)).")
        else:
            return ("Both your cancer and heart disease risks may be elevated. Please consider consulting oncology and cardiology specialists. "
                    "Alternatively, you can book with our oncology specialist "
                    "([WhatsApp: +90 554 500 43 33](https://wa.me/905545004333)) and our cardiology specialist "
                    "([WhatsApp: +90 533 143 75 36](https://wa.me/905331437536)).")
    else:
        if chd_low and ca_low:
            return "Kalp hastalığı ve kanser riskiniz düşük. Risk faktörlerinizi daha da iyileştirerek sağlıklı günler ve yıllar dileriz."
        elif (not chd_low) and ca_low:
            return "Kalp hastalığı riskiniz artmış olabilir. Bir kardiyoloji uzmanından görüş almanızı öneririz. Alternatif olarak kalp sağlığı uzmanımızdan randevu alabilirsiniz ([WhatsApp: +90 533 143 75 36](https://wa.me/905331437536))."
        elif chd_low and (not ca_low):
            return "Kanser riskiniz artmış olabilir. Bir onkoloji uzmanından görüş almanızı öneririz. Alternatif olarak onkoloji uzmanımızdan randevu alabilirsiniz ([WhatsApp: +90 554 500 43 33](https://wa.me/905545004333))."
        else:
            return "Kanser ve kalp hastalığı riskiniz artmış olabilir. Onkoloji ve kardiyoloji uzmanlarından görüş almanızı öneririz. Alternatif olarak onkoloji uzmanımızdan ([WhatsApp: +90 554 500 43 33](https://wa.me/905545004333)) ve kardiyoloji uzmanımızdan ([WhatsApp: +90 533 143 75 36](https://wa.me/905331437536)) randevu alabilirsiniz."

# =======================================
# 6) Hesaplayıcı (Buton callback)
# =======================================
def hesapla(lang, cinsiyet, yas, kilo, boy_cm, total_chol, hdl, sbp, kreatinin,
            bp_ilac, sigara, diyabet, statin, aile_kanser, alkol_hafta,
            egzersiz_seviyesi, egzersiz_dk):
    try:
        # Kısıtlar
        yas = int(clamp(yas, 30, 79))
        kilo = float(clamp(kilo, 35, 200))
        boy_cm = float(clamp(boy_cm, 120, 210))
        total_chol = float(clamp(total_chol, 130, 320))
        hdl = float(clamp(hdl, 20, 100))
        sbp = float(clamp(sbp, 90, 180))
        kreatinin = float(clamp(kreatinin, 0.30, 2.50))
        alkol_hafta = float(clamp(alkol_hafta, 0, 35))

        # Egzersiz süresi
        if _is_none_like(egzersiz_seviyesi):
            egzersiz_dk = 0.0
        else:
            egzersiz_dk = float(clamp(egzersiz_dk, 0, 600))

        # yok/var & no/yes → 0/1
        bp_ilac_01  = yok_var_to01(bp_ilac)
        sigara_01   = yok_var_to01(sigara)
        diyabet_01  = yok_var_to01(diyabet)
        statin_01   = yok_var_to01(statin)
        aile_01     = yok_var_to01(aile_kanser)

        # Türevler
        bmi = vki_hesapla_kg_m2(kilo, boy_cm)
        egfr = clamp(egfr_ckd_epi_2021(cinsiyet, yas, kreatinin), 15, 140)

        # PREVENT-CHD taban (0–100 %)
        chd10_taban = prevent_chd_10y(cinsiyet, yas, total_chol, hdl, sbp,
                                      bp_ilac_01, sigara_01, diyabet_01, statin_01, bmi, egfr)

        # ---- CHD Post-hoc: BMI ----
        chd10_after_bmi = chd10_taban * hr_bmi_chd(bmi)

        # ---- CHD Post-hoc: Alcohol (stepwise) + CAP at 45% ----
        chd10_after_alcohol_raw = chd10_after_bmi * hr_alkol_chd(alkol_hafta)
        chd10_after_alcohol = min(chd10_after_alcohol_raw, ALCOHOL_POSTHOC_CAP_PERCENT)

        # Egzersiz kategorisi → HR'ler (CHD ve Kanser için ayrı)
        kategori, hr_chd_ex, hr_kans = egzersiz_kategori_ve_carpanlar(egzersiz_seviyesi, egzersiz_dk)

        # Egzersizi CHD'ye uygula ve genel sınırla
        chd10_duz  = clamp(chd10_after_alcohol * hr_chd_ex, 0.0, 100.0)
        # Nihai CHD sert tavan
        chd10_duz = min(chd10_duz, 45.0)

        # Kanser (BMI etkisi sürekli)
        kanser_duz = kanser_riski_meta(cinsiyet, yas, bmi, aile_01, alkol_hafta, sigara_01, kategori)

        # Etiketler
        if lang == "English":
            chd_label = chd_group_en(chd10_duz)
            ca_label  = cancer_group_en(kanser_duz)
            chd_title = L("English")["res_chd"]
            ca_title  = L("English")["res_cancer"]
        else:
            chd_label = chd_grup(chd10_duz)
            ca_label  = kanser_grup(kanser_duz)
            chd_title = L("Türkçe")["res_chd"]
            ca_title  = L("Türkçe")["res_cancer"]

        # Durum mesajı
        is_chd_low = chd10_duz < 5.0
        is_ca_low  = kanser_duz < 5.0
        durum = status_text(lang, is_chd_low, is_ca_low)

        ozet = (
            f"**{chd_title}:** {chd10_duz:.2f}% — _{chd_label}_\n\n"
            f"**{ca_title}:** {kanser_duz:.2f}% — _{ca_label}_\n\n"
            f"---\n\n{durum}"
        )
        return gr.update(value=ozet, visible=True)

    except Exception as e:
        return gr.update(value=f"⚠️ Hata/Error: {e}", visible=True)

# =======================================
# 7) Dil / Egzersiz UI Yardımcıları
# =======================================
EGZ_EXAMPLES_MODERATE_TR = """
**Hafif–orta egzersiz örnekleri**
- Hızlı tempo yürüyüş (≈5–6 km/s)
- Hafif bisiklet (≈10–16 km/s), düz zemin
- Su aerobiği / tempolu dans
- Çim biçme / orta tempo ev işleri
- Orta tempolu yüzme<br>
(_Konuşma testi:_ Rahat konuşursun, **şarkı söylemek zor**.)<br>
(_İdeal süre hedefi:_ **≥150 dk/hafta**)
"""

EGZ_EXAMPLES_VIGOROUS_TR = """
**Ağır egzersiz örnekleri**
- Koşu / jogging
- Tekler tenis, basketbol, futbol
- HIIT, ip atlama
- Hızlı/tepelik bisiklet (>16 km/s)
- Hızlı sürekli yüzme (intervals)<br>
(_Konuşma testi:_ **Cümleleri zor tamamlarsın**, nefes nefese kalırsın.)<br>
(_İdeal süre hedefi:_ **≥75 dk/hafta**)
"""

EGZ_EXAMPLES_MODERATE_EN = """
**Examples of moderate-intensity activity**
- Brisk walking (≈5–6 km/h)
- Easy cycling (≈10–16 km/h), flat terrain
- Water aerobics / tempo dancing
- Lawn mowing / moderate housework
- Moderate-paced swimming<br>
(_Talk test:_ You can talk, **singing is hard**.)<br>
(_Guideline target:_ **≥150 min/week**)
"""

EGZ_EXAMPLES_VIGOROUS_EN = """
**Examples of vigorous-intensity activity**
- Running / jogging
- Singles tennis, basketball, soccer
- HIIT, jump rope
- Fast/hilly cycling (>16 km/h)
- Continuous fast swimming (intervals)<br>
(_Talk test:_ **Hard to finish sentences**, you’re out of breath.)<br>
(_Guideline target:_ **≥75 min/week**)
"""

def toggle_egzersiz_optionA(lang, sev, current_val):
    sev_l = (sev or "").strip().lower()
    is_tr = (lang != "English")
    if _is_none_like(sev_l):
        return (
            gr.update(value="", visible=False),
            gr.update(visible=False, value=0,
                      label=L(lang)["ex_dur_label"])
        )
    elif _is_moderate_like(sev_l):
        hint = EGZ_EXAMPLES_MODERATE_TR if is_tr else EGZ_EXAMPLES_MODERATE_EN
        label = f"{L(lang)['ex_dur_label']} — {'ideal ≥150 dk' if is_tr else 'guideline ≥150 min'}"
        return (
            gr.update(value=hint, visible=True),
            gr.update(visible=True, value=current_val, label=label)
        )
    else:  # ağır / vigorous
        hint = EGZ_EXAMPLES_VIGOROUS_TR if is_tr else EGZ_EXAMPLES_VIGOROUS_EN
        label = f"{L(lang)['ex_dur_label']} — {'ideal ≥75 dk' if is_tr else 'guideline ≥75 min'}"
        return (
            gr.update(value=hint, visible=True),
            gr.update(visible=True, value=current_val, label=label)
        )

def apply_language(lang, cinsiyet_v, bp_ilac_v, statin_v, diyabet_v, sigara_v, aile_v, egz_sev_v):
    T = L(lang)
    def map_gender(v):
        m = {"erkek":"male","kadın":"female","male":"male","female":"female"}
        return m.get((v or "").lower(), T["gender_choices"][0])
    def map_yesno(v):
        m = {"yok":"no","var":"yes","no":"no","yes":"yes"}
        return m.get((v or "").lower(), "no")
    def map_ex(v):
        m = {
            "yok ya da yoka yakın":"none or very little",
            "hafif ya da orta":"moderate",
            "ağır":"vigorous",
            "none or very little":"none or very little",
            "moderate":"moderate",
            "vigorous":"vigorous",
        }
        return m.get((v or "").lower(), T["ex_intensity_choices"][0])

    if lang != "English":
        def map_gender(v):
            m = {"male":"erkek","female":"kadın","erkek":"erkek","kadın":"kadın"}
            return m.get((v or "").lower(), T["gender_choices"][0])
        def map_yesno(v):
            m = {"no":"yok","yes":"var","yok":"yok","var":"var"}
            return m.get((v or "").lower(), "yok")
        def map_ex(v):
            m = {
                "none or very little":"yok ya da yoka yakın",
                "moderate":"hafif ya da orta",
                "vigorous":"ağır",
                "yok ya da yoka yakın":"yok ya da yoka yakın",
                "hafif ya da orta":"hafif ya da orta",
                "ağır":"ağır",
            }
            return m.get((v or "").lower(), T["ex_intensity_choices"][0])

    return (
        gr.update(value=header_md_text(lang)),
        gr.update(label=T["gender_label"], choices=T["gender_choices"], value=map_gender(cinsiyet_v)),
        gr.update(label=T["age_label"]),
        gr.update(label=T["height_label"]),
        gr.update(label=T["weight_label"]),
        gr.update(label=T["tc_label"]),
        gr.update(label=T["hdl_label"]),
        gr.update(label=T["sbp_label"]),
        gr.update(label=T["cre_label"]),
        gr.update(label=T["bpmed_label"], choices=(["yok","var"] if lang!="English" else ["no","yes"]), value=map_yesno(bp_ilac_v)),
        gr.update(label=T["statin_label"], choices=(["yok","var"] if lang!="English" else ["no","yes"]), value=map_yesno(statin_v)),
        gr.update(label=T["diabetes_label"], choices=(["yok","var"] if lang!="English" else ["no","yes"]), value=map_yesno(diyabet_v)),
        gr.update(label=T["smoking_label"], choices=(["yok","var"] if lang!="English" else ["no","yes"]), value=map_yesno(sigara_v)),
        gr.update(label=T["fhx_label"], choices=(["yok","var"] if lang!="English" else ["no","yes"]), value=map_yesno(aile_v)),
        gr.update(label=T["alcohol_label"]),
        gr.update(label=T["ex_intensity_label"], choices=T["ex_intensity_choices"], value=map_ex(egz_sev_v)),
        gr.update(value=T["calc_btn"]),
        gr.update(value="", visible=False),
    )

# Küçük ölçek CSS
SCALE_CSS = """
.app-scale { transform: scale(0.85); transform-origin: top center; }
body { overflow-x: hidden; }
"""

# =======================================
# 8) Gradio Arayüz
# =======================================
with gr.Blocks(theme=gr.themes.Default(), css=SCALE_CSS) as demo:
    lang = gr.Radio(choices=["Türkçe","English"], value="Türkçe", label="Dil / Language")
    header_md = gr.Markdown(header_md_text("Türkçe"))

    with gr.Row():
        with gr.Column():
            cinsiyet  = gr.Radio(choices=["erkek","kadın"], value="erkek", label=L("Türkçe")["gender_label"])
            yas       = gr.Slider(30, 79, value=55, step=1,   label=L("Türkçe")["age_label"])
            boy_cm    = gr.Slider(120,210, value=175, step=0.5,label=L("Türkçe")["height_label"])
            kilo      = gr.Slider(35.0,200.0, value=80.0, step=0.1, label=L("Türkçe")["weight_label"])
            total_c   = gr.Slider(130,320, value=200, step=1, label=L("Türkçe")["tc_label"])
            hdl       = gr.Slider(20,100, value=50, step=1,   label=L("Türkçe")["hdl_label"])
            sbp       = gr.Slider(90,180, value=120, step=1,  label=L("Türkçe")["sbp_label"])
            kreatinin = gr.Slider(0.30,2.50, value=0.90, step=0.01, label=L("Türkçe")["cre_label"])

        with gr.Column():
            bp_ilac     = gr.Radio(choices=["yok","var"], value="yok", label=L("Türkçe")["bpmed_label"])
            statin      = gr.Radio(choices=["yok","var"], value="yok", label=L("Türkçe")["statin_label"])
            diyabet     = gr.Radio(choices=["yok","var"], value="yok", label=L("Türkçe")["diabetes_label"])
            sigara      = gr.Radio(choices=["yok","var"], value="yok", label=L("Türkçe")["smoking_label"])
            aile_kanser = gr.Radio(choices=["yok","var"], value="yok", label=L("Türkçe")["fhx_label"])
            alkol       = gr.Slider(0,35, value=0, step=1, label=L("Türkçe")["alcohol_label"])

            egz_sev   = gr.Radio(
                choices=L("Türkçe")["ex_intensity_choices"],
                value="yok ya da yoka yakın",
                label=L("Türkçe")["ex_intensity_label"]
            )
            egz_hint  = gr.Markdown("", visible=False)
            egz_dk    = gr.Slider(0,600, value=0, step=5, label=L("Türkçe")["ex_dur_label"], visible=False)

            btn       = gr.Button(L("Türkçe")["calc_btn"], variant="primary", scale=2)
            sonuc     = gr.Markdown("", visible=False)

    lang.change(
        apply_language,
        inputs=[lang, cinsiyet, bp_ilac, statin, diyabet, sigara, aile_kanser, egz_sev],
        outputs=[header_md, cinsiyet, yas, boy_cm, kilo, total_c, hdl, sbp, kreatinin,
                 bp_ilac, statin, diyabet, sigara, aile_kanser, alkol, egz_sev, btn, sonuc]
    )

    egz_sev.change(toggle_egzersiz_optionA, inputs=[lang, egz_sev, egz_dk], outputs=[egz_hint, egz_dk])

    btn.click(
        fn=hesapla,
        inputs=[lang, cinsiyet, yas, kilo, boy_cm, total_c, hdl, sbp, kreatinin,
                bp_ilac, sigara, diyabet, statin, aile_kanser, alkol, egz_sev, egz_dk],
        outputs=[sonuc],
    )

# Spaces otomatik başlatır; launch() gerekmez
demo.queue()
