"""
FHIR tools — query a FHIR R4 server on behalf of the patient in context.

These tools are registered with the agent in agent.py.  At call time, each
tool reads the FHIR credentials (fhir_url, fhir_token, patient_id) from
tool_context.state — values that were injected by fhir_hook.extract_fhir_context
before the LLM was called.  The credentials never appear in the prompt.

─────────────────────────────────────────────────────────────────────────────
Adding your own FHIR tools
─────────────────────────────────────────────────────────────────────────────
1. Write a new function in this file (or create a new file in shared/tools/).
2. Add tool_context: ToolContext as the LAST parameter.
3. Start with  ctx = _get_fhir_context(tool_context); if isinstance(ctx, dict): return ctx
4. Export it from shared/tools/__init__.py.
5. Add it to the tools=[...] list in whichever agent(s) need it.

All FHIR REST calls go through _fhir_get(), which attaches the Bearer token
and sets the Accept header.  httpx is used (already a transitive dependency of
google-adk / a2a-sdk — no extra install required).
"""
import logging

import httpx
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

_FHIR_TIMEOUT = 15  # seconds


# ── Private helpers ────────────────────────────────────────────────────────────

def _get_fhir_context(tool_context: ToolContext):
    """
    Read FHIR credentials injected by fhir_hook into the session state.

    Returns (fhir_url, fhir_token, patient_id) on success.
    Returns an error dict if any credential is missing so the caller can
    return it directly as the tool result.
    """
    fhir_url   = tool_context.state.get("fhir_url",   "").rstrip("/")
    fhir_token = tool_context.state.get("fhir_token", "")
    patient_id = tool_context.state.get("patient_id", "")

    missing = [
        name for name, val in [
            ("fhir_url",   fhir_url),
            ("fhir_token", fhir_token),
            ("patient_id", patient_id),
        ]
        if not val
    ]
    if missing:
        return {
            "status": "error",
            "error_message": (
                f"FHIR context is not available — missing: {', '.join(missing)}. "
                "Ensure the caller includes 'fhir-context' in the A2A message metadata."
            ),
        }
    return fhir_url, fhir_token, patient_id


def _fhir_get(fhir_url: str, token: str, path: str, params: dict | None = None) -> dict:
    """Perform an authenticated FHIR GET and return the parsed JSON response."""
    response = httpx.get(
        f"{fhir_url}/{path}",
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept":        "application/fhir+json",
        },
        timeout=_FHIR_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _http_error_result(exc: httpx.HTTPStatusError) -> dict:
    return {
        "status":        "error",
        "http_status":   exc.response.status_code,
        "error_message": f"FHIR server returned HTTP {exc.response.status_code}: {exc.response.text[:200]}",
    }


def _connection_error_result(exc: Exception) -> dict:
    return {
        "status":        "error",
        "error_message": f"Could not reach FHIR server: {exc}",
    }


def _coding_display(codings: list) -> str:
    """Return the first human-readable display text from a list of FHIR codings."""
    for c in codings:
        if c.get("display"):
            return c["display"]
    return "Unknown"


# ── Tool: patient demographics ─────────────────────────────────────────────────

def get_patient_demographics(tool_context: ToolContext) -> dict:
    """
    Fetches the demographic information for the current patient from the FHIR server.

    Returns name, date of birth, gender, and primary contact details.
    No arguments required — the patient identity comes from the session context.
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx

    logger.info("tool_get_patient_demographics patient_id=%s", patient_id)
    try:
        patient = _fhir_get(fhir_url, fhir_token, f"Patient/{patient_id}")
    except httpx.HTTPStatusError as e:
        return _http_error_result(e)
    except Exception as e:
        return _connection_error_result(e)

    names    = patient.get("name", [])
    official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    given    = " ".join(official.get("given", []))
    family   = official.get("family", "")
    full_name = f"{given} {family}".strip() or "Unknown"

    contacts = [
        {"system": t.get("system"), "value": t.get("value"), "use": t.get("use")}
        for t in patient.get("telecom", [])
    ]

    addrs   = patient.get("address", [])
    address = None
    if addrs:
        a = addrs[0]
        address = ", ".join(filter(None, [
            " ".join(a.get("line", [])),
            a.get("city"), a.get("state"), a.get("postalCode"), a.get("country"),
        ]))

    return {
        "status":         "success",
        "patient_id":     patient_id,
        "name":           full_name,
        "birth_date":     patient.get("birthDate"),
        "gender":         patient.get("gender"),
        "active":         patient.get("active"),
        "contacts":       contacts,
        "address":        address,
        "marital_status": (patient.get("maritalStatus") or {}).get("text"),
    }


# ── Tool: active medications ───────────────────────────────────────────────────

def get_active_medications(tool_context: ToolContext) -> dict:
    """
    Retrieves the patient's current active medication list from the FHIR server.

    Queries MedicationRequest resources with status=active and returns medication
    names, dosage instructions, and prescribing dates.
    No arguments required.
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx

    logger.info("tool_get_active_medications patient_id=%s", patient_id)
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "MedicationRequest",
            params={"patient": patient_id, "status": "active", "_count": "50"},
        )
    except httpx.HTTPStatusError as e:
        return _http_error_result(e)
    except Exception as e:
        return _connection_error_result(e)

    medications = []
    for entry in bundle.get("entry", []):
        res         = entry.get("resource", {})
        med_concept = res.get("medicationCodeableConcept", {})
        med_name    = (
            med_concept.get("text")
            or _coding_display(med_concept.get("coding", []))
            or res.get("medicationReference", {}).get("display", "Unknown")
        )
        dosage_list = [d.get("text", "No dosage text") for d in res.get("dosageInstruction", [])]
        medications.append({
            "medication":  med_name,
            "status":      res.get("status"),
            "dosage":      dosage_list[0] if dosage_list else "Not specified",
            "authored_on": res.get("authoredOn"),
            "requester":   (res.get("requester") or {}).get("display"),
        })

    return {
        "status":      "success",
        "patient_id":  patient_id,
        "count":       len(medications),
        "medications": medications,
    }


# ── Tool: active conditions (problem list) ─────────────────────────────────────

def get_active_conditions(tool_context: ToolContext) -> dict:
    """
    Retrieves the patient's active conditions and diagnoses from the FHIR server.

    Queries Condition resources with clinical-status=active and returns the
    problem list with condition names, severity, and onset dates.
    No arguments required.
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx

    logger.info("tool_get_active_conditions patient_id=%s", patient_id)
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Condition",
            params={"patient": patient_id, "clinical-status": "active", "_count": "50"},
        )
    except httpx.HTTPStatusError as e:
        return _http_error_result(e)
    except Exception as e:
        return _connection_error_result(e)

    conditions = []
    for entry in bundle.get("entry", []):
        res   = entry.get("resource", {})
        code  = res.get("code", {})
        onset = res.get("onsetDateTime") or (res.get("onsetPeriod") or {}).get("start")
        conditions.append({
            "condition":       code.get("text") or _coding_display(code.get("coding", [])),
            "clinical_status": (
                (res.get("clinicalStatus") or {}).get("coding", [{}])[0].get("code")
            ),
            "severity":        (res.get("severity") or {}).get("text"),
            "onset":           onset,
            "recorded_date":   res.get("recordedDate"),
        })

    return {
        "status":     "success",
        "patient_id": patient_id,
        "count":      len(conditions),
        "conditions": conditions,
    }


# ── Tool: recent observations (vitals / labs) ──────────────────────────────────

def get_recent_observations(category: str, tool_context: ToolContext) -> dict:
    """
    Retrieves recent clinical observations for the patient from the FHIR server.

    Args:
        category: FHIR observation category. Common values:
                    'vital-signs'    — blood pressure, heart rate, temperature, SpO2
                    'laboratory'     — lab results (CBC, HbA1c, metabolic panel, etc.)
                    'social-history' — smoking status, alcohol use, etc.
                  Defaults to 'vital-signs' if not specified.

    Returns the 20 most recent observations in the category, newest first.
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx

    category = (category or "vital-signs").strip().lower()
    logger.info("tool_get_recent_observations patient_id=%s category=%s", patient_id, category)
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Observation",
            params={"patient": patient_id, "category": category, "_sort": "-date", "_count": "20"},
        )
    except httpx.HTTPStatusError as e:
        return _http_error_result(e)
    except Exception as e:
        return _connection_error_result(e)

    observations = []
    for entry in bundle.get("entry", []):
        res  = entry.get("resource", {})
        code = res.get("code", {})
        obs_name = code.get("text") or _coding_display(code.get("coding", []))

        value, unit = None, None
        if "valueQuantity" in res:
            vq    = res["valueQuantity"]
            value = vq.get("value")
            unit  = vq.get("unit") or vq.get("code")
        elif "valueCodeableConcept" in res:
            value = (res["valueCodeableConcept"].get("text")
                     or _coding_display(res["valueCodeableConcept"].get("coding", [])))
        elif "valueString" in res:
            value = res["valueString"]

        components = []
        for comp in res.get("component", []):
            comp_code = (comp.get("code") or {})
            comp_name = comp_code.get("text") or _coding_display(comp_code.get("coding", []))
            comp_vq   = comp.get("valueQuantity", {})
            components.append({
                "name":  comp_name,
                "value": comp_vq.get("value"),
                "unit":  comp_vq.get("unit") or comp_vq.get("code"),
            })

        observations.append({
            "observation":    obs_name,
            "value":          value,
            "unit":           unit,
            "components":     components or None,
            "effective_date": res.get("effectiveDateTime") or (res.get("effectivePeriod") or {}).get("start"),
            "status":         res.get("status"),
            "interpretation": (
                (res.get("interpretation") or [{}])[0].get("text")
                or _coding_display((res.get("interpretation") or [{}])[0].get("coding", []))
            ),
        })

    return {
        "status":       "success",
        "patient_id":   patient_id,
        "category":     category,
        "count":        len(observations),
        "observations": observations,
    }


# ── Tool: recent encounters ────────────────────────────────────────────────────

def get_recent_encounters(tool_context: ToolContext) -> dict:
    """
    Retrieves the patient's most recent clinical encounters from the FHIR server.

    Returns up to 10 encounters (newest first) including encounter type,
    status, dates, location, and participating providers.
    No arguments required.
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx

    logger.info("tool_get_recent_encounters patient_id=%s", patient_id)
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Encounter",
            params={"patient": patient_id, "_sort": "-date", "_count": "10"},
        )
    except httpx.HTTPStatusError as e:
        return _http_error_result(e)
    except Exception as e:
        return _connection_error_result(e)

    encounters = []
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        enc_types = res.get("type", [])
        type_text = (
            enc_types[0].get("text") or _coding_display(enc_types[0].get("coding", []))
            if enc_types else "Unknown"
        )
        period = res.get("period", {})
        participants = [
            (p.get("individual") or {}).get("display")
            for p in res.get("participant", [])
            if (p.get("individual") or {}).get("display")
        ]
        locations = [
            (loc.get("location") or {}).get("display")
            for loc in res.get("location", [])
            if (loc.get("location") or {}).get("display")
        ]
        reason_codes = res.get("reasonCode", [])
        reasons = [
            rc.get("text") or _coding_display(rc.get("coding", []))
            for rc in reason_codes
        ]
        enc_class = res.get("class") or {}
        encounters.append({
            "type":         type_text,
            "status":       res.get("status"),
            "class":        enc_class.get("display") or enc_class.get("code"),
            "start":        period.get("start"),
            "end":          period.get("end"),
            "participants": participants,
            "locations":    locations,
            "reasons":      reasons,
        })

    return {
        "status":     "success",
        "patient_id": patient_id,
        "count":      len(encounters),
        "encounters": encounters,
    }


# ── Tool: lab trend ────────────────────────────────────────────────────────────

def get_lab_trend(loinc_code: str, count: int, tool_context: ToolContext) -> dict:
    """
    Fetches a time-series of values for a specific lab test to assess trends.

    Args:
        loinc_code: LOINC code for the lab (e.g. '4548-4' for HbA1c,
                    '2160-0' for Creatinine, '13457-7' for LDL-C,
                    '33914-3' for eGFR, '2093-3' for Total Cholesterol,
                    '2345-7' for Glucose).
        count:      Number of historical results to return (1–20).
                    Use 5–10 for trend analysis; 1 for the most recent only.

    Returns results sorted newest first with dates and numeric values.
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx

    count = max(1, min(int(count or 10), 20))
    logger.info("tool_get_lab_trend patient_id=%s loinc=%s count=%d", patient_id, loinc_code, count)
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Observation",
            params={
                "patient": patient_id,
                "code":    loinc_code,
                "_sort":   "-date",
                "_count":  str(count),
            },
        )
    except httpx.HTTPStatusError as e:
        return _http_error_result(e)
    except Exception as e:
        return _connection_error_result(e)

    results = []
    lab_name = None
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        if lab_name is None:
            code = res.get("code", {})
            lab_name = code.get("text") or _coding_display(code.get("coding", []))

        value, unit = None, None
        if "valueQuantity" in res:
            vq    = res["valueQuantity"]
            value = vq.get("value")
            unit  = vq.get("unit") or vq.get("code")
        elif "valueCodeableConcept" in res:
            value = (res["valueCodeableConcept"].get("text")
                     or _coding_display(res["valueCodeableConcept"].get("coding", [])))
        elif "valueString" in res:
            value = res["valueString"]

        results.append({
            "date":   res.get("effectiveDateTime") or (res.get("effectivePeriod") or {}).get("start"),
            "value":  value,
            "unit":   unit,
            "status": res.get("status"),
        })

    return {
        "status":     "success",
        "patient_id": patient_id,
        "loinc_code": loinc_code,
        "lab_name":   lab_name or "Unknown",
        "count":      len(results),
        "results":    results,
    }


# ── Reference ranges for interpret_labs ───────────────────────────────────────
# Keyed by LOINC code: (low, high) — None means no bound in that direction.
# These are general adult ranges; they are not patient-specific.
_LAB_RANGES: dict[str, tuple] = {
    "2345-7":  (70.0,  100.0),   # Glucose fasting
    "14749-6": (70.0,  140.0),   # Glucose random
    "4548-4":  (4.0,   5.6),     # HbA1c
    "17856-6": (4.0,   5.6),     # HbA1c IFCC
    "2160-0":  (0.6,   1.3),     # Creatinine
    "38483-4": (0.6,   1.3),     # Creatinine
    "3094-0":  (7.0,   20.0),    # BUN
    "33914-3": (60.0,  None),    # eGFR (>=60 normal)
    "2093-3":  (None,  200.0),   # Total Cholesterol
    "13457-7": (None,  100.0),   # LDL-C
    "18262-6": (None,  100.0),   # LDL-C direct
    "2085-9":  (40.0,  None),    # HDL-C
    "2089-1":  (None,  150.0),   # Triglycerides
    "1920-8":  (10.0,  40.0),    # AST
    "1742-6":  (7.0,   56.0),    # ALT
    "3016-3":  (0.4,   4.0),     # TSH
    "718-7":   (12.0,  17.5),    # Hemoglobin
    "4544-3":  (34.9,  50.0),    # Hematocrit
    "6690-2":  (4.5,   11.0),    # WBC
    "777-3":   (150.0, 400.0),   # Platelets
    "2951-2":  (136.0, 145.0),   # Sodium
    "2823-3":  (3.5,   5.0),     # Potassium
    "1963-8":  (22.0,  28.0),    # Bicarbonate
}


def _classify_value(value: float, low: float | None, high: float | None) -> str:
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    return "normal"


# ── Tool: interpret labs ───────────────────────────────────────────────────────

def interpret_labs(tool_context: ToolContext) -> dict:
    """
    Fetches recent laboratory results and annotates each with a normal/low/high flag.

    Applies general adult reference ranges to numeric lab values. Use this when
    the user asks whether labs are in range, which results are abnormal, or for
    a quick overall read of the patient's labs.

    Returns an annotated list with a 'flag' field ('normal', 'low', 'high', or
    'no_range') and a caveat that ranges are general and not patient-specific.
    No arguments required.
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx

    logger.info("tool_interpret_labs patient_id=%s", patient_id)
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Observation",
            params={"patient": patient_id, "category": "laboratory", "_sort": "-date", "_count": "20"},
        )
    except httpx.HTTPStatusError as e:
        return _http_error_result(e)
    except Exception as e:
        return _connection_error_result(e)

    annotated = []
    for entry in bundle.get("entry", []):
        res  = entry.get("resource", {})
        code = res.get("code", {})
        codings = code.get("coding", [])
        lab_name = code.get("text") or _coding_display(codings)

        value, unit = None, None
        if "valueQuantity" in res:
            vq    = res["valueQuantity"]
            value = vq.get("value")
            unit  = vq.get("unit") or vq.get("code")

        loinc = next((c.get("code") for c in codings if c.get("system", "").endswith("loinc.org")), None)
        flag = "no_range"
        if isinstance(value, (int, float)) and loinc and loinc in _LAB_RANGES:
            low, high = _LAB_RANGES[loinc]
            flag = _classify_value(float(value), low, high)

        annotated.append({
            "lab":    lab_name,
            "value":  value,
            "unit":   unit,
            "flag":   flag,
            "date":   res.get("effectiveDateTime") or (res.get("effectivePeriod") or {}).get("start"),
            "status": res.get("status"),
        })

    return {
        "status":     "success",
        "patient_id": patient_id,
        "count":      len(annotated),
        "labs":       annotated,
        "caveat": (
            "Reference ranges are general adult values and are not patient-specific. "
            "Always present this caveat to the user when sharing interpretation results."
        ),
    }


# ── Tool: patient summary (composite) ─────────────────────────────────────────

def get_patient_summary(tool_context: ToolContext) -> dict:
    """
    Returns a full clinical snapshot of the patient in a single tool call.

    Retrieves demographics, active medications, active conditions, recent vital
    signs, and recent labs in one batch. Use this for broad questions ('give me
    a summary', 'what's going on', 'clinical overview'). For narrow single-section
    questions, use the individual tools instead.

    No arguments required.
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx

    logger.info("tool_get_patient_summary patient_id=%s", patient_id)

    results: dict = {"status": "success", "patient_id": patient_id}
    errors: list  = []

    # Demographics
    try:
        patient = _fhir_get(fhir_url, fhir_token, f"Patient/{patient_id}")
        names    = patient.get("name", [])
        official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
        given    = " ".join(official.get("given", []))
        family   = official.get("family", "")
        results["demographics"] = {
            "name":       f"{given} {family}".strip() or "Unknown",
            "birth_date": patient.get("birthDate"),
            "gender":     patient.get("gender"),
        }
    except Exception as e:
        errors.append(f"demographics: {e}")

    # Active medications
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "MedicationRequest",
            params={"patient": patient_id, "status": "active", "_count": "50"},
        )
        meds = []
        for entry in bundle.get("entry", []):
            res         = entry.get("resource", {})
            med_concept = res.get("medicationCodeableConcept", {})
            med_name    = (
                med_concept.get("text")
                or _coding_display(med_concept.get("coding", []))
                or res.get("medicationReference", {}).get("display", "Unknown")
            )
            dosage_list = [d.get("text", "") for d in res.get("dosageInstruction", [])]
            meds.append({"medication": med_name, "dosage": dosage_list[0] if dosage_list else None})
        results["medications"] = {"count": len(meds), "medications": meds}
    except Exception as e:
        errors.append(f"medications: {e}")

    # Active conditions
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Condition",
            params={"patient": patient_id, "clinical-status": "active", "_count": "50"},
        )
        conditions = []
        for entry in bundle.get("entry", []):
            res  = entry.get("resource", {})
            code = res.get("code", {})
            conditions.append(code.get("text") or _coding_display(code.get("coding", [])))
        results["conditions"] = {"count": len(conditions), "conditions": conditions}
    except Exception as e:
        errors.append(f"conditions: {e}")

    # Recent vitals
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Observation",
            params={"patient": patient_id, "category": "vital-signs", "_sort": "-date", "_count": "10"},
        )
        vitals = []
        for entry in bundle.get("entry", []):
            res  = entry.get("resource", {})
            code = res.get("code", {})
            name = code.get("text") or _coding_display(code.get("coding", []))
            value, unit = None, None
            if "valueQuantity" in res:
                vq = res["valueQuantity"]
                value, unit = vq.get("value"), vq.get("unit") or vq.get("code")
            vitals.append({"name": name, "value": value, "unit": unit,
                           "date": res.get("effectiveDateTime")})
        results["vitals"] = {"count": len(vitals), "vitals": vitals}
    except Exception as e:
        errors.append(f"vitals: {e}")

    # Recent labs
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Observation",
            params={"patient": patient_id, "category": "laboratory", "_sort": "-date", "_count": "10"},
        )
        labs = []
        for entry in bundle.get("entry", []):
            res  = entry.get("resource", {})
            code = res.get("code", {})
            name = code.get("text") or _coding_display(code.get("coding", []))
            value, unit = None, None
            if "valueQuantity" in res:
                vq = res["valueQuantity"]
                value, unit = vq.get("value"), vq.get("unit") or vq.get("code")
            labs.append({"name": name, "value": value, "unit": unit,
                         "date": res.get("effectiveDateTime")})
        results["labs"] = {"count": len(labs), "labs": labs}
    except Exception as e:
        errors.append(f"labs: {e}")

    if errors:
        results["partial_errors"] = errors
        results["status"] = "partial"

    return results