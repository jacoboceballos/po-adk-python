"""
General-purpose tools — no FHIR server required.

These tools demonstrate the tool pattern without any external API dependency.
They work immediately after cloning the repo with just a Google API key.

get_current_datetime  Returns the current date and time in any IANA timezone.
look_up_icd10         Returns the ICD-10-CM code for common clinical conditions
                      using a built-in reference table organized by ICD-10 chapter.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)


# ── ICD-10-CM reference table ──────────────────────────────────────────────────
# Keys are lowercase clinical shorthand.  Values are (code, full_description).
# Codes are unspecified / default forms (the ".9" style); clinicians reach for
# these in conversational lookup.  More specific codes (with laterality,
# complications, etc.) require a full terminology service.
#
# Organized by ICD-10-CM chapter for readability.  The lookup logic is flat.

_ICD10_TABLE: dict[str, tuple[str, str]] = {

    # ── Chapter 1 — Infectious & parasitic diseases (A00–B99) ───────────────
    "hiv":                     ("B20",    "Human immunodeficiency virus [HIV] disease"),
    "tuberculosis":            ("A15.9",  "Respiratory tuberculosis unspecified"),
    "sepsis":                  ("A41.9",  "Sepsis, unspecified organism"),
    "uti":                     ("N39.0",  "Urinary tract infection, site not specified"),
    "pneumonia":               ("J18.9",  "Pneumonia, unspecified organism"),
    "covid-19":                ("U07.1",  "COVID-19"),
    "cellulitis":              ("L03.90", "Cellulitis, unspecified"),

    # ── Chapter 2 — Neoplasms (C00–D49) ────────────────────────────────────
    "breast cancer":           ("C50.919","Malignant neoplasm of unspecified site of unspecified female breast"),
    "lung cancer":             ("C34.90", "Malignant neoplasm of unspecified part of unspecified bronchus or lung"),
    "colon cancer":            ("C18.9",  "Malignant neoplasm of colon, unspecified"),
    "prostate cancer":         ("C61",    "Malignant neoplasm of prostate"),
    "lymphoma":                ("C85.90", "Non-Hodgkin lymphoma, unspecified, unspecified site"),
    "leukemia":                ("C95.90", "Leukemia, unspecified of unspecified cell type, not having achieved remission"),

    # ── Chapter 4 — Endocrine, nutritional, metabolic (E00–E89) ────────────
    "diabetes type 1":         ("E10.9",  "Type 1 diabetes mellitus without complications"),
    "diabetes type 2":         ("E11.9",  "Type 2 diabetes mellitus without complications"),
    "hyperlipidemia":          ("E78.5",  "Hyperlipidemia, unspecified"),
    "hypercholesterolemia":    ("E78.00", "Pure hypercholesterolemia, unspecified"),
    "obesity":                 ("E66.9",  "Obesity, unspecified"),
    "hypothyroidism":          ("E03.9",  "Hypothyroidism, unspecified"),
    "hyperthyroidism":         ("E05.90", "Thyrotoxicosis, unspecified without thyrotoxic crisis or storm"),
    "metabolic syndrome":      ("E88.81", "Metabolic syndrome"),
    "vitamin d deficiency":    ("E55.9",  "Vitamin D deficiency, unspecified"),
    "gout":                    ("M10.9",  "Gout, unspecified"),

    # ── Chapter 5 — Mental & behavioural (F01–F99) ─────────────────────────
    "depression":              ("F32.9",  "Major depressive disorder, single episode, unspecified"),
    "anxiety":                 ("F41.9",  "Anxiety disorder, unspecified"),
    "bipolar":                 ("F31.9",  "Bipolar disorder, unspecified"),
    "schizophrenia":           ("F20.9",  "Schizophrenia, unspecified"),
    "adhd":                    ("F90.9",  "Attention-deficit hyperactivity disorder, unspecified type"),
    "ptsd":                    ("F43.10", "Post-traumatic stress disorder, unspecified"),
    "alcohol use disorder":    ("F10.20", "Alcohol dependence, uncomplicated"),
    "opioid use disorder":     ("F11.20", "Opioid dependence, uncomplicated"),
    "dementia":                ("F03.90", "Unspecified dementia, unspecified severity"),
    "alzheimer":               ("G30.9",  "Alzheimer's disease, unspecified"),

    # ── Chapter 6 — Nervous system (G00–G99) ───────────────────────────────
    "migraine":                ("G43.909","Migraine, unspecified, not intractable, without status migrainosus"),
    "epilepsy":                ("G40.909","Epilepsy, unspecified, not intractable, without status epilepticus"),
    "parkinson":               ("G20",    "Parkinson's disease"),
    "multiple sclerosis":      ("G35",    "Multiple sclerosis"),
    "stroke":                  ("I63.9",  "Cerebral infarction, unspecified"),
    "tia":                     ("G45.9",  "Transient cerebral ischemic attack, unspecified"),
    "peripheral neuropathy":   ("G62.9",  "Polyneuropathy, unspecified"),

    # ── Chapter 9 — Circulatory (I00–I99) ──────────────────────────────────
    "hypertension":            ("I10",    "Essential (primary) hypertension"),
    "heart failure":           ("I50.9",  "Heart failure, unspecified"),
    "chf":                     ("I50.9",  "Heart failure, unspecified"),
    "atrial fibrillation":     ("I48.91", "Unspecified atrial fibrillation"),
    "afib":                    ("I48.91", "Unspecified atrial fibrillation"),
    "mi":                      ("I21.9",  "Acute myocardial infarction, unspecified"),
    "heart attack":            ("I21.9",  "Acute myocardial infarction, unspecified"),
    "cad":                     ("I25.10", "Atherosclerotic heart disease of native coronary artery without angina pectoris"),
    "coronary artery disease": ("I25.10", "Atherosclerotic heart disease of native coronary artery without angina pectoris"),
    "angina":                  ("I20.9",  "Angina pectoris, unspecified"),
    "dvt":                     ("I82.40", "Acute embolism and thrombosis of unspecified deep veins of lower extremity"),
    "pulmonary embolism":      ("I26.99", "Other pulmonary embolism without acute cor pulmonale"),
    "peripheral vascular disease": ("I73.9", "Peripheral vascular disease, unspecified"),
    "aortic stenosis":         ("I35.0",  "Nonrheumatic aortic (valve) stenosis"),

    # ── Chapter 10 — Respiratory (J00–J99) ─────────────────────────────────
    "asthma":                  ("J45.909","Unspecified asthma, uncomplicated"),
    "copd":                    ("J44.9",  "Chronic obstructive pulmonary disease, unspecified"),
    "obstructive sleep apnea": ("G47.33", "Obstructive sleep apnea (adult) (pediatric)"),
    "sleep apnea":             ("G47.30", "Sleep apnea, unspecified"),
    "bronchitis":              ("J40",    "Bronchitis, not specified as acute or chronic"),
    "influenza":               ("J11.1",  "Influenza due to unidentified influenza virus with other respiratory manifestations"),

    # ── Chapter 11 — Digestive (K00–K95) ───────────────────────────────────
    "gerd":                    ("K21.9",  "Gastro-esophageal reflux disease without esophagitis"),
    "peptic ulcer":            ("K27.9",  "Peptic ulcer, site unspecified, unspecified as acute or chronic, without hemorrhage or perforation"),
    "irritable bowel syndrome":("K58.9",  "Irritable bowel syndrome without diarrhea"),
    "ibs":                     ("K58.9",  "Irritable bowel syndrome without diarrhea"),
    "crohn":                   ("K50.90", "Crohn's disease, unspecified, without complications"),
    "ulcerative colitis":      ("K51.90", "Ulcerative colitis, unspecified, without complications"),
    "cirrhosis":               ("K74.60", "Unspecified cirrhosis of liver"),
    "hepatitis c":             ("B18.2",  "Chronic viral hepatitis C"),
    "hepatitis b":             ("B18.1",  "Chronic viral hepatitis B without delta-agent"),

    # ── Chapter 13 — Musculoskeletal (M00–M99) ─────────────────────────────
    "osteoarthritis":          ("M19.90", "Unspecified osteoarthritis, unspecified site"),
    "rheumatoid arthritis":    ("M06.9",  "Rheumatoid arthritis, unspecified"),
    "osteoporosis":            ("M81.0",  "Age-related osteoporosis without current pathological fracture"),
    "lupus":                   ("M32.9",  "Systemic lupus erythematosus, unspecified"),
    "sle":                     ("M32.9",  "Systemic lupus erythematosus, unspecified"),
    "low back pain":           ("M54.50", "Low back pain, unspecified"),
    "fibromyalgia":            ("M79.7",  "Fibromyalgia"),

    # ── Chapter 14 — Genitourinary (N00–N99) ───────────────────────────────
    "ckd":                     ("N18.9",  "Chronic kidney disease, unspecified"),
    "chronic kidney disease":  ("N18.9",  "Chronic kidney disease, unspecified"),
    "bph":                     ("N40.1",  "Benign prostatic hyperplasia with lower urinary tract symptoms"),
    "erectile dysfunction":    ("N52.9",  "Male erectile dysfunction, unspecified"),
    "menopause":               ("N95.1",  "Menopausal and female climacteric states"),

    # ── Chapter 15 — Pregnancy & childbirth (O00–O9A) ──────────────────────
    "preeclampsia":            ("O14.90", "Unspecified preeclampsia, unspecified trimester"),
    "gestational diabetes":    ("O24.410","Gestational diabetes mellitus in pregnancy, diet controlled"),
    "gestational hypertension":("O13.9",  "Gestational hypertension without significant proteinuria, unspecified trimester"),

    # ── Chapter 18 — Symptoms & signs (R00–R99) ────────────────────────────
    "chest pain":              ("R07.9",  "Chest pain, unspecified"),
    "shortness of breath":     ("R06.02", "Shortness of breath"),
    "dyspnea":                 ("R06.00", "Dyspnea, unspecified"),
    "fatigue":                 ("R53.83", "Other fatigue"),
    "syncope":                 ("R55",    "Syncope and collapse"),

    # ── Chapter 21 — Health status & service encounters (Z00–Z99) ──────────
    "annual physical":         ("Z00.00", "Encounter for general adult medical examination without abnormal findings"),
    "tobacco use":              ("Z72.0",  "Tobacco use"),
    "history of smoking":      ("Z87.891","Personal history of nicotine dependence"),
    "family history of mi":    ("Z82.49", "Family history of ischemic heart disease and other diseases of the circulatory system"),
    "family history of diabetes": ("Z83.3","Family history of diabetes mellitus"),
    "family history of cancer": ("Z80.9", "Family history of malignant neoplasm, unspecified"),
}


# ── Tool: current datetime ─────────────────────────────────────────────────────

def get_current_datetime(timezone: str, tool_context: ToolContext) -> dict:
    """
    Returns the current date and time in the specified timezone.

    Args:
        timezone: IANA timezone string.  Examples: 'America/Chicago', 'UTC',
                  'America/New_York', 'Europe/London', 'Asia/Tokyo'.
                  Defaults to UTC if not provided.

    Returns a dict with date, time, day of week, and full ISO-8601 datetime.
    """
    tz_str = (timezone or "UTC").strip()
    logger.info("tool_get_current_datetime timezone=%s", tz_str)

    try:
        tz  = ZoneInfo(tz_str)
        now = datetime.now(tz)
        return {
            "status":      "success",
            "timezone":    tz_str,
            "datetime":    now.isoformat(),
            "date":        now.strftime("%Y-%m-%d"),
            "time":        now.strftime("%H:%M:%S"),
            "day_of_week": now.strftime("%A"),
        }
    except ZoneInfoNotFoundError:
        return {
            "status":        "error",
            "error_message": (
                f"Unknown timezone: '{tz_str}'. "
                "Use IANA format, e.g. 'America/Chicago', 'UTC', 'Europe/London'."
            ),
        }


# ── Tool: ICD-10 lookup ────────────────────────────────────────────────────────

def look_up_icd10(term: str, tool_context: ToolContext) -> dict:
    """
    Looks up the ICD-10-CM code for a common clinical condition.

    Args:
        term: Condition name to look up.  Examples: 'hypertension',
              'diabetes type 2', 'asthma', 'heart failure', 'copd',
              'atrial fibrillation', 'ckd', 'stroke', 'lupus'.

    Returns the ICD-10-CM code and full description if found.
    Falls back to a partial-match search if the exact term is not found.

    Note: This tool uses a built-in reference table of ~85 common conditions
    organized by ICD-10 chapter.  Codes returned are the unspecified / default
    form (e.g. I10 for hypertension, E11.9 for type 2 diabetes without
    complications).  For more specific codes — with laterality, complications,
    severity modifiers — use a full terminology server such as NLM's VSAC or
    a FHIR ValueSet $expand endpoint.
    """
    raw  = (term or "").strip()
    key  = raw.lower()
    logger.info("tool_look_up_icd10 term=%s", raw)

    # Exact match
    if key in _ICD10_TABLE:
        code, description = _ICD10_TABLE[key]
        return {
            "status":      "success",
            "term":        raw,
            "icd10_code":  code,
            "description": description,
        }

    # Partial / substring match — return the first hit
    matches = [(k, v) for k, v in _ICD10_TABLE.items() if key in k or k in key]
    if matches:
        matched_key, (code, description) = matches[0]
        return {
            "status":       "success",
            "term":         raw,
            "matched_term": matched_key,
            "note":         "Exact match not found; showing closest match from built-in table.",
            "icd10_code":   code,
            "description":  description,
        }

    return {
        "status":          "not_found",
        "term":            raw,
        "error_message":   (
            f"No ICD-10 code found for '{raw}'. "
            "This tool uses a built-in reference table of ~85 common conditions. "
            "For specific code variants (laterality, complications, severity) "
            "or less common conditions, use a full terminology server."
        ),
        "available_term_count": len(_ICD10_TABLE),
    }