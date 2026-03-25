"""
ASCVD Risk Calculator — 10-year atherosclerotic cardiovascular disease risk.

Implements the 2013 ACC/AHA Pooled Cohort Equations (PCE).
Reference: Goff DC Jr, et al. Circulation. 2014;129(suppl 2):S49-S73.

Two tools:
  assess_ascvd_risk           Pulls all inputs from FHIR automatically.
  calculate_ascvd_risk_manual Accepts explicit values for what-if scenarios.
"""
import logging
import math
from datetime import date, datetime

import httpx
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

_FHIR_TIMEOUT = 15  # seconds


# ═══════════════════════════════════════════════════════════════════════════════
# Pooled Cohort Equations — coefficient tables
# ═══════════════════════════════════════════════════════════════════════════════
# Keys: (race_group, sex)   race_group ∈ {"white", "aa"}   sex ∈ {"male", "female"}

_PCE_COEFFICIENTS = {
    ("white", "female"): {
        "ln_age": -29.799, "ln_age_sq": 4.884,
        "ln_tc": 13.540, "ln_age_ln_tc": -3.114,
        "ln_hdl": -13.578, "ln_age_ln_hdl": 3.149,
        "ln_treated_sbp": 2.019, "ln_age_ln_treated_sbp": 0.0,
        "ln_untreated_sbp": 1.957, "ln_age_ln_untreated_sbp": 0.0,
        "smoking": 7.574, "ln_age_smoking": -1.665,
        "diabetes": 0.661,
        "baseline_survival": 0.9665, "mean_coeff": -29.18,
    },
    ("aa", "female"): {
        "ln_age": 17.114, "ln_age_sq": 0.0,
        "ln_tc": 0.940, "ln_age_ln_tc": 0.0,
        "ln_hdl": -18.920, "ln_age_ln_hdl": 4.475,
        "ln_treated_sbp": 29.291, "ln_age_ln_treated_sbp": -6.432,
        "ln_untreated_sbp": 27.820, "ln_age_ln_untreated_sbp": -6.087,
        "smoking": 0.691, "ln_age_smoking": 0.0,
        "diabetes": 0.874,
        "baseline_survival": 0.9533, "mean_coeff": 86.61,
    },
    ("white", "male"): {
        "ln_age": 12.344, "ln_age_sq": 0.0,
        "ln_tc": 11.853, "ln_age_ln_tc": -2.664,
        "ln_hdl": -7.990, "ln_age_ln_hdl": 1.769,
        "ln_treated_sbp": 1.797, "ln_age_ln_treated_sbp": 0.0,
        "ln_untreated_sbp": 1.764, "ln_age_ln_untreated_sbp": 0.0,
        "smoking": 7.837, "ln_age_smoking": -1.795,
        "diabetes": 0.658,
        "baseline_survival": 0.9144, "mean_coeff": 61.18,
    },
    ("aa", "male"): {
        "ln_age": 2.469, "ln_age_sq": 0.0,
        "ln_tc": 0.302, "ln_age_ln_tc": 0.0,
        "ln_hdl": -0.307, "ln_age_ln_hdl": 0.0,
        "ln_treated_sbp": 1.916, "ln_age_ln_treated_sbp": 0.0,
        "ln_untreated_sbp": 1.809, "ln_age_ln_untreated_sbp": 0.0,
        "smoking": 0.549, "ln_age_smoking": 0.0,
        "diabetes": 0.645,
        "baseline_survival": 0.8954, "mean_coeff": 19.54,
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# Common antihypertensive medication names (for detecting BP treatment)
# ═══════════════════════════════════════════════════════════════════════════════

_ANTIHYPERTENSIVE_KEYWORDS = {
    # ACE inhibitors
    "lisinopril", "enalapril", "ramipril", "benazepril", "captopril",
    "fosinopril", "quinapril", "perindopril", "trandolapril", "moexipril",
    # ARBs
    "losartan", "valsartan", "irbesartan", "olmesartan", "candesartan",
    "telmisartan", "azilsartan", "eprosartan",
    # Calcium channel blockers
    "amlodipine", "nifedipine", "diltiazem", "verapamil", "felodipine",
    "nicardipine", "clevidipine",
    # Thiazide diuretics
    "hydrochlorothiazide", "hctz", "chlorthalidone", "indapamide",
    "metolazone",
    # Beta blockers (also used for BP)
    "metoprolol", "atenolol", "propranolol", "carvedilol", "bisoprolol",
    "nebivolol", "labetalol", "nadolol",
    # Alpha blockers
    "doxazosin", "prazosin", "terazosin",
    # Direct vasodilators
    "hydralazine", "minoxidil",
    # Aldosterone antagonists
    "spironolactone", "eplerenone",
    # Central alpha agonists
    "clonidine", "methyldopa", "guanfacine",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Core PCE calculation
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_pce(
    age: int,
    sex: str,
    race_group: str,
    total_cholesterol: float,
    hdl_cholesterol: float,
    systolic_bp: float,
    on_bp_treatment: bool,
    has_diabetes: bool,
    is_smoker: bool,
) -> dict:
    """
    Core Pooled Cohort Equations calculation.

    Returns a dict with the 10-year ASCVD risk percentage, risk category,
    and ACC/AHA guideline-based recommendations.
    """
    # ── Validate inputs ────────────────────────────────────────────────────
    if not (40 <= age <= 79):
        return {
            "status": "error",
            "error_message": (
                f"The Pooled Cohort Equations are validated for ages 40-79. "
                f"Patient age is {age}. For patients outside this range, consider "
                f"lifetime risk assessment or clinical judgement."
            ),
        }

    if total_cholesterol < 50 or total_cholesterol > 500:
        return {"status": "error", "error_message": f"Total cholesterol {total_cholesterol} mg/dL is outside plausible range (50-500)."}
    if hdl_cholesterol < 10 or hdl_cholesterol > 200:
        return {"status": "error", "error_message": f"HDL cholesterol {hdl_cholesterol} mg/dL is outside plausible range (10-200)."}
    if systolic_bp < 60 or systolic_bp > 250:
        return {"status": "error", "error_message": f"Systolic BP {systolic_bp} mmHg is outside plausible range (60-250)."}

    # ── Normalise sex & race ───────────────────────────────────────────────
    sex_key = "male" if sex.lower() in ("male", "m") else "female"

    race_lower = (race_group or "").lower()
    if any(t in race_lower for t in ("african", "black", "aa", "african american")):
        race_key = "aa"
    else:
        # PCE uses White-population coefficients for all non-African-American groups.
        race_key = "white"

    coeff = _PCE_COEFFICIENTS[(race_key, sex_key)]

    # ── Compute individual sum ─────────────────────────────────────────────
    ln_age = math.log(age)
    ln_tc  = math.log(total_cholesterol)
    ln_hdl = math.log(hdl_cholesterol)
    ln_sbp = math.log(systolic_bp)
    smoke  = 1.0 if is_smoker else 0.0
    diab   = 1.0 if has_diabetes else 0.0

    if on_bp_treatment:
        sbp_term = coeff["ln_treated_sbp"] * ln_sbp + coeff["ln_age_ln_treated_sbp"] * ln_age * ln_sbp
    else:
        sbp_term = coeff["ln_untreated_sbp"] * ln_sbp + coeff["ln_age_ln_untreated_sbp"] * ln_age * ln_sbp

    individual_sum = (
        coeff["ln_age"]         * ln_age
        + coeff["ln_age_sq"]    * (ln_age ** 2)
        + coeff["ln_tc"]        * ln_tc
        + coeff["ln_age_ln_tc"] * ln_age * ln_tc
        + coeff["ln_hdl"]       * ln_hdl
        + coeff["ln_age_ln_hdl"]* ln_age * ln_hdl
        + sbp_term
        + coeff["smoking"]      * smoke
        + coeff["ln_age_smoking"] * ln_age * smoke
        + coeff["diabetes"]     * diab
    )

    # ── 10-year risk ───────────────────────────────────────────────────────
    risk = 1.0 - coeff["baseline_survival"] ** math.exp(individual_sum - coeff["mean_coeff"])
    risk_pct = round(max(0.0, min(risk * 100, 100.0)), 1)

    # ── Risk category & recommendations (ACC/AHA 2018 Cholesterol Guideline) ─
    if risk_pct < 5.0:
        category = "Low"
        statin_rec = "Statin therapy is generally not indicated based on risk alone."
        lifestyle_rec = "Emphasise heart-healthy lifestyle: diet, exercise, weight management, tobacco avoidance."
        additional = "Reassess risk in 4-6 years, or sooner if risk factors change."
    elif risk_pct < 7.5:
        category = "Borderline"
        statin_rec = "Statin therapy may be considered if risk-enhancing factors are present (family history of premature ASCVD, metabolic syndrome, CKD, inflammatory conditions, elevated Lp(a), apoB, or hsCRP)."
        lifestyle_rec = "Lifestyle modifications are the primary intervention."
        additional = "Discuss risk-enhancing factors. Coronary artery calcium (CAC) scoring can help refine the decision."
    elif risk_pct < 20.0:
        category = "Intermediate"
        statin_rec = "Moderate-intensity statin therapy is recommended to reduce LDL-C by 30-49%."
        lifestyle_rec = "Lifestyle modifications are essential alongside pharmacotherapy."
        additional = "If decision is uncertain, consider CAC scoring: CAC = 0 favours deferring statin (except in diabetics and smokers); CAC >= 100 or >= 75th percentile supports statin therapy."
    else:
        category = "High"
        statin_rec = "High-intensity statin therapy is recommended to reduce LDL-C by >= 50%. If LDL-C remains >= 70 mg/dL on maximally tolerated statin, consider adding ezetimibe or a PCSK9 inhibitor."
        lifestyle_rec = "Aggressive lifestyle modifications alongside pharmacotherapy."
        additional = "Consider aspirin 75-100 mg/day for adults 40-70 without increased bleeding risk (shared decision-making). Optimise blood pressure to < 130/80 mmHg."

    return {
        "status": "success",
        "ten_year_ascvd_risk_pct": risk_pct,
        "risk_category": category,
        "recommendations": {
            "statin": statin_rec,
            "lifestyle": lifestyle_rec,
            "additional": additional,
        },
        "inputs_used": {
            "age": age,
            "sex": sex_key,
            "race_group": f"{race_key} ({'African American' if race_key == 'aa' else 'White / Other'})",
            "total_cholesterol_mg_dl": total_cholesterol,
            "hdl_cholesterol_mg_dl": hdl_cholesterol,
            "systolic_bp_mmhg": systolic_bp,
            "on_bp_treatment": on_bp_treatment,
            "has_diabetes": has_diabetes,
            "is_smoker": is_smoker,
        },
        "equation": "2013 ACC/AHA Pooled Cohort Equations (Goff et al., Circulation 2014;129(S2):S49-S73)",
        "guideline": "2018 ACC/AHA Cholesterol Clinical Practice Guideline (Grundy et al., Circulation 2019;139:e1082-e1143)",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FHIR helpers (private)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_fhir_context(tool_context: ToolContext):
    """Read FHIR credentials from session state.  Returns tuple or error dict."""
    fhir_url   = tool_context.state.get("fhir_url",   "").rstrip("/")
    fhir_token = tool_context.state.get("fhir_token", "")
    patient_id = tool_context.state.get("patient_id", "")

    missing = [n for n, v in [("fhir_url", fhir_url), ("fhir_token", fhir_token), ("patient_id", patient_id)] if not v]
    if missing:
        return {
            "status": "error",
            "error_message": (
                f"FHIR context is not available — missing: {', '.join(missing)}. "
                "Ensure the caller includes 'fhir-context' in the A2A message metadata. "
                "Alternatively, use the manual ASCVD calculator by providing values directly."
            ),
        }
    return fhir_url, fhir_token, patient_id


def _fhir_get(fhir_url: str, token: str, path: str, params: dict | None = None) -> dict:
    """Authenticated FHIR GET -> parsed JSON."""
    resp = httpx.get(
        f"{fhir_url}/{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"},
        timeout=_FHIR_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _age_from_birthdate(birth_date_str: str) -> int | None:
    """Calculate age in years from a FHIR date string (YYYY-MM-DD or YYYY)."""
    try:
        if len(birth_date_str) == 4:  # year only
            return date.today().year - int(birth_date_str)
        bd = datetime.strptime(birth_date_str[:10], "%Y-%m-%d").date()
        today = date.today()
        return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
    except Exception:
        return None


def _extract_race(patient: dict) -> str:
    """Extract race from US Core Race extension on a FHIR Patient resource."""
    us_core_race_url = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-race"
    for ext in patient.get("extension", []):
        if ext.get("url") == us_core_race_url:
            for sub in ext.get("extension", []):
                if sub.get("url") == "ombCategory":
                    coding = sub.get("valueCoding", {})
                    display = coding.get("display", "").lower()
                    code = coding.get("code", "")
                    if "african" in display or "black" in display or code == "2054-5":
                        return "aa"
                    return "white"
            # Fallback to text
            text_ext = next((s for s in ext.get("extension", []) if s.get("url") == "text"), None)
            if text_ext:
                txt = (text_ext.get("valueString") or "").lower()
                if "african" in txt or "black" in txt:
                    return "aa"
                return "white"
    return "unknown"


def _find_latest_observation(entries: list, loinc_codes: set) -> float | None:
    """Find the most recent observation value matching any of the given LOINC codes."""
    for entry in entries:
        res = entry.get("resource", {})
        codings = res.get("code", {}).get("coding", [])
        codes = {c.get("code") for c in codings}
        if codes & loinc_codes:
            # Direct value
            vq = res.get("valueQuantity")
            if vq and vq.get("value") is not None:
                return float(vq["value"])
            # Component (e.g., systolic inside a BP panel)
            for comp in res.get("component", []):
                comp_codes = {c.get("code") for c in comp.get("code", {}).get("coding", [])}
                if comp_codes & loinc_codes:
                    cvq = comp.get("valueQuantity", {})
                    if cvq.get("value") is not None:
                        return float(cvq["value"])
    return None


def _find_systolic_bp(entries: list) -> float | None:
    """Extract systolic BP — check standalone and as component of BP panel."""
    # LOINC 8480-6 = Systolic BP, 85354-9 = Blood pressure panel
    systolic_codes = {"8480-6"}
    panel_codes = {"85354-9"}

    # Try standalone systolic observation first
    val = _find_latest_observation(entries, systolic_codes)
    if val is not None:
        return val

    # Try as component of a BP panel
    for entry in entries:
        res = entry.get("resource", {})
        codings = res.get("code", {}).get("coding", [])
        codes = {c.get("code") for c in codings}
        if codes & (panel_codes | systolic_codes):
            for comp in res.get("component", []):
                comp_codes = {c.get("code") for c in comp.get("code", {}).get("coding", [])}
                if comp_codes & systolic_codes:
                    cvq = comp.get("valueQuantity", {})
                    if cvq.get("value") is not None:
                        return float(cvq["value"])
    return None


def _detect_diabetes(conditions: list) -> bool:
    """Check active conditions for diabetes (ICD-10 E10.x, E11.x, E13.x or text match)."""
    for entry in conditions:
        res = entry.get("resource", {})
        code = res.get("code", {})
        text = (code.get("text") or "").lower()
        if "diabetes" in text:
            return True
        for coding in code.get("coding", []):
            icd = (coding.get("code") or "").upper()
            if icd.startswith(("E10", "E11", "E13")):
                return True
            display = (coding.get("display") or "").lower()
            if "diabetes" in display:
                return True
    return False


def _detect_smoking(entries: list) -> bool:
    """Check social-history observations for current smoker status."""
    # LOINC 72166-2 = Tobacco smoking status
    smoking_codes = {"72166-2"}
    current_smoker_snomedcts = {
        "449868002",  # Current every day smoker
        "428041000124106",  # Current some day smoker
        "77176002",   # Smoker (finding)
        "428071000124103",  # Heavy tobacco smoker
    }
    for entry in entries:
        res = entry.get("resource", {})
        codings = res.get("code", {}).get("coding", [])
        codes = {c.get("code") for c in codings}
        if codes & smoking_codes:
            vcc = res.get("valueCodeableConcept", {})
            # Check SNOMED codes
            for vc in vcc.get("coding", []):
                if vc.get("code") in current_smoker_snomedcts:
                    return True
                display = (vc.get("display") or "").lower()
                if "current" in display and "smok" in display:
                    return True
            # Check text
            text = (vcc.get("text") or "").lower()
            if "current" in text and "smok" in text:
                return True
    return False


def _detect_bp_treatment(medications: list) -> bool:
    """Check active medications for known antihypertensive drugs."""
    for entry in medications:
        res = entry.get("resource", {})
        med_concept = res.get("medicationCodeableConcept", {})
        med_name = (
            med_concept.get("text", "")
            or next((c.get("display", "") for c in med_concept.get("coding", [])), "")
            or res.get("medicationReference", {}).get("display", "")
        ).lower()
        for keyword in _ANTIHYPERTENSIVE_KEYWORDS:
            if keyword in med_name:
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Tool 1: Automatic FHIR-based ASCVD assessment
# ═══════════════════════════════════════════════════════════════════════════════

def assess_ascvd_risk(tool_context: ToolContext) -> dict:
    """
    Calculates the patient's 10-year ASCVD risk by automatically pulling all
    required inputs from the connected FHIR server.

    No arguments required — the patient identity and FHIR credentials come
    from the session context (injected via A2A message metadata).

    Gathers: age, sex, race, total cholesterol, HDL cholesterol, systolic BP,
    BP treatment status, diabetes status, and smoking status.

    Returns the 10-year ASCVD risk percentage, risk category (Low / Borderline /
    Intermediate / High), and ACC/AHA guideline-based recommendations including
    statin therapy guidance.
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx

    logger.info("tool_assess_ascvd_risk patient_id=%s", patient_id)
    data_sources = {}
    warnings = []

    # ── 1. Patient demographics (age, sex, race) ──────────────────────────
    try:
        patient = _fhir_get(fhir_url, fhir_token, f"Patient/{patient_id}")
    except Exception as e:
        return {"status": "error", "error_message": f"Could not fetch Patient resource: {e}"}

    birth_date = patient.get("birthDate")
    age = _age_from_birthdate(birth_date) if birth_date else None
    sex = patient.get("gender")
    race = _extract_race(patient)

    data_sources["age"] = {"value": age, "source": f"Patient.birthDate ({birth_date})"}
    data_sources["sex"] = {"value": sex, "source": "Patient.gender"}
    data_sources["race"] = {"value": race, "source": "Patient US Core Race extension"}

    if race == "unknown":
        race = "white"  # PCE default
        warnings.append(
            "Race not found in patient record. Defaulting to White/Other coefficients. "
            "The PCE has separate coefficients only for African American and White/Other groups."
        )

    # ── 2. Labs: total cholesterol, HDL ───────────────────────────────────
    try:
        lab_bundle = _fhir_get(fhir_url, fhir_token, "Observation", params={
            "patient": patient_id, "category": "laboratory", "_sort": "-date", "_count": "50",
        })
    except Exception as e:
        return {"status": "error", "error_message": f"Could not fetch laboratory observations: {e}"}

    lab_entries = lab_bundle.get("entry", [])
    total_chol = _find_latest_observation(lab_entries, {"2093-3"})   # LOINC: Cholesterol [Mass/Vol]
    hdl_chol   = _find_latest_observation(lab_entries, {"2085-9"})   # LOINC: HDL Cholesterol [Mass/Vol]

    data_sources["total_cholesterol"] = {"value": total_chol, "source": "Observation LOINC 2093-3", "unit": "mg/dL"}
    data_sources["hdl_cholesterol"]   = {"value": hdl_chol,   "source": "Observation LOINC 2085-9", "unit": "mg/dL"}

    # ── 3. Vitals: systolic blood pressure ────────────────────────────────
    try:
        vitals_bundle = _fhir_get(fhir_url, fhir_token, "Observation", params={
            "patient": patient_id, "category": "vital-signs", "_sort": "-date", "_count": "20",
        })
    except Exception as e:
        return {"status": "error", "error_message": f"Could not fetch vital-sign observations: {e}"}

    vitals_entries = vitals_bundle.get("entry", [])
    systolic_bp = _find_systolic_bp(vitals_entries)
    data_sources["systolic_bp"] = {"value": systolic_bp, "source": "Observation LOINC 8480-6 / 85354-9", "unit": "mmHg"}

    # ── 4. Conditions: diabetes ───────────────────────────────────────────
    try:
        conditions_bundle = _fhir_get(fhir_url, fhir_token, "Condition", params={
            "patient": patient_id, "clinical-status": "active", "_count": "100",
        })
    except Exception as e:
        return {"status": "error", "error_message": f"Could not fetch conditions: {e}"}

    has_diabetes = _detect_diabetes(conditions_bundle.get("entry", []))
    data_sources["has_diabetes"] = {"value": has_diabetes, "source": "Condition (ICD-10 E10/E11/E13 or text match)"}

    # ── 5. Medications: BP treatment ──────────────────────────────────────
    try:
        meds_bundle = _fhir_get(fhir_url, fhir_token, "MedicationRequest", params={
            "patient": patient_id, "status": "active", "_count": "100",
        })
    except Exception as e:
        return {"status": "error", "error_message": f"Could not fetch medications: {e}"}

    on_bp_treatment = _detect_bp_treatment(meds_bundle.get("entry", []))
    data_sources["on_bp_treatment"] = {"value": on_bp_treatment, "source": "MedicationRequest (antihypertensive keyword match)"}

    # ── 6. Social history: smoking ────────────────────────────────────────
    try:
        social_bundle = _fhir_get(fhir_url, fhir_token, "Observation", params={
            "patient": patient_id, "category": "social-history", "_sort": "-date", "_count": "10",
        })
    except Exception as e:
        # Non-fatal — default to non-smoker with a warning
        social_bundle = {"entry": []}
        warnings.append(f"Could not fetch social-history observations: {e}. Defaulting smoking status to false.")

    is_smoker = _detect_smoking(social_bundle.get("entry", []))
    data_sources["is_smoker"] = {"value": is_smoker, "source": "Observation LOINC 72166-2 (social-history)"}

    # ── Check for missing required inputs ─────────────────────────────────
    missing = []
    if age is None:
        missing.append("age (Patient.birthDate)")
    if sex is None:
        missing.append("sex (Patient.gender)")
    if total_chol is None:
        missing.append("total cholesterol (LOINC 2093-3)")
    if hdl_chol is None:
        missing.append("HDL cholesterol (LOINC 2085-9)")
    if systolic_bp is None:
        missing.append("systolic blood pressure (LOINC 8480-6)")

    if missing:
        return {
            "status": "incomplete",
            "error_message": (
                f"Cannot compute ASCVD risk — the following required values were not found "
                f"in the patient's FHIR record: {', '.join(missing)}. "
                f"You can use the manual calculator to provide these values directly."
            ),
            "data_found": data_sources,
            "warnings": warnings,
        }

    # ── Compute ───────────────────────────────────────────────────────────
    result = _compute_pce(
        age=age, sex=sex, race_group=race,
        total_cholesterol=total_chol, hdl_cholesterol=hdl_chol,
        systolic_bp=systolic_bp, on_bp_treatment=on_bp_treatment,
        has_diabetes=has_diabetes, is_smoker=is_smoker,
    )

    if result["status"] == "success":
        result["data_sources"] = data_sources
        if warnings:
            result["warnings"] = warnings

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Tool 2: Manual / what-if ASCVD calculation
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_ascvd_risk_manual(
    age: int,
    sex: str,
    race: str,
    total_cholesterol: float,
    hdl_cholesterol: float,
    systolic_bp: float,
    on_bp_treatment: bool,
    has_diabetes: bool,
    is_smoker: bool,
    tool_context: ToolContext,
) -> dict:
    """
    Calculates 10-year ASCVD risk from explicitly provided clinical values.

    Use this tool for what-if scenarios, when FHIR context is unavailable,
    or to override values found in the patient record.

    Args:
        age:               Patient age in years (valid range: 40-79).
        sex:               'male' or 'female'.
        race:              Race group — 'white', 'african american', 'black', 'aa',
                           or 'other'.  The Pooled Cohort Equations use separate
                           coefficients for African American and White/Other groups.
        total_cholesterol: Total cholesterol in mg/dL (typical range: 130-320).
        hdl_cholesterol:   HDL cholesterol in mg/dL (typical range: 20-100).
        systolic_bp:       Systolic blood pressure in mmHg (typical range: 90-200).
        on_bp_treatment:   True if patient is currently on antihypertensive medication.
        has_diabetes:      True if patient has type 1 or type 2 diabetes.
        is_smoker:         True if patient is a current smoker.

    Returns the 10-year ASCVD risk percentage, risk category, and guideline
    recommendations.
    """
    logger.info(
        "tool_calculate_ascvd_risk_manual age=%s sex=%s race=%s tc=%s hdl=%s sbp=%s bp_tx=%s dm=%s smoke=%s",
        age, sex, race, total_cholesterol, hdl_cholesterol, systolic_bp,
        on_bp_treatment, has_diabetes, is_smoker,
    )

    return _compute_pce(
        age=int(age),
        sex=str(sex),
        race_group=str(race),
        total_cholesterol=float(total_cholesterol),
        hdl_cholesterol=float(hdl_cholesterol),
        systolic_bp=float(systolic_bp),
        on_bp_treatment=bool(on_bp_treatment),
        has_diabetes=bool(has_diabetes),
        is_smoker=bool(is_smoker),
    )