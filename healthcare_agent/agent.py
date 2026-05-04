"""
healthcare_agent — Agent definition.

This agent has read-only access to a patient's FHIR R4 record.
FHIR credentials (server URL, bearer token, patient ID) are injected via the
A2A message metadata by the caller (e.g. Prompt Opinion) and extracted into
session state by extract_fhir_context before every LLM call.

To customise:
  • Change model, description, and instruction below.
  • Add or remove tools from the tools=[...] list.
  • Add new FHIR tools in shared/tools/fhir.py and export from shared/tools/__init__.py.
  • Add non-FHIR tools in shared/tools/ or locally in a tools/ folder here.
"""
from google.adk.agents import Agent

from shared.fhir_hook import extract_fhir_context
from shared.tools import (
    get_active_conditions,
    get_active_medications,
    get_lab_trend,
    get_patient_demographics,
    get_patient_summary,
    get_recent_encounters,
    get_recent_observations,
    interpret_labs,
)

root_agent = Agent(
    name="healthcare_fhir_agent",
    model="gemini-3.1-flash-lite-preview",
    description=(
        "A clinical assistant that queries a patient's FHIR health record "
        "to answer questions about demographics, medications, conditions, "
        "observations, encounters, and lab trends."
    ),
    instruction=(
        "You are a clinical assistant with secure, read-only access to a patient's FHIR health record. "
        "Use the available tools to retrieve real data from the connected FHIR server when answering questions. "
        "Always fetch data using the tools — never make up or guess clinical information. "
        "Present medical information clearly and concisely, as if briefing a clinician. "
        "If a tool returns an error, explain what went wrong and suggest how to resolve it. "
        "If FHIR context is not available, let the caller know they need to include it in their request.\n\n"
        "TOOL SELECTION GUIDE:\n"
        "\n"
        "• BROAD / OPEN-ENDED questions ('give me a summary', 'what's going on', "
        "'clinical overview', 'full picture'): use get_patient_summary. "
        "It returns demographics, medications, conditions, vitals, and labs in a "
        "single parallel call and is much faster than calling the individual tools "
        "one-by-one.\n"
        "\n"
        "• NARROW / TARGETED questions about one category: use the specific "
        "single-section tool — get_patient_demographics, get_active_medications, "
        "get_active_conditions, or get_recent_observations. Lighter and returns "
        "less noise than the full summary.\n"
        "\n"
        "• 'WHEN WERE THEY LAST SEEN?', 'HAVE THEY BEEN ADMITTED?', 'WHAT HAPPENED "
        "AT THEIR LAST VISIT?': use get_recent_encounters.\n"
        "\n"
        "• 'ARE THEIR LABS IN RANGE?', 'ANY ABNORMAL RESULTS?', 'HOW ARE THEIR "
        "LABS LOOKING?': use interpret_labs. It fetches recent labs and annotates "
        "each one with a normal / low / high flag against adult reference ranges. "
        "Always pass the 'caveat' field along to the user verbatim when "
        "presenting results — the reference ranges are educational, not patient-specific.\n"
        "\n"
        "• 'IS THIS LAB TRENDING UP?', 'HOW HAS THEIR HbA1c CHANGED?', 'RESPONSE TO "
        "THERAPY?': use get_lab_trend with the appropriate LOINC code and a count "
        "of 5–10. Common LOINC codes: 4548-4 (HbA1c), 2160-0 (Creatinine), "
        "13457-7 (LDL), 33914-3 (eGFR), 2345-7 (Glucose), 2093-3 (Total cholesterol). "
        "If you don't know the LOINC code for a lab the user named, first call "
        "get_recent_observations(category='laboratory') to discover it from the "
        "patient's own record, then call get_lab_trend.\n"
        "\n"
        "When a question combines multiple tool needs (e.g. 'what conditions do "
        "they have and are their labs in range?'), call the tools in sequence and "
        "synthesize a single answer rather than returning each tool's output separately."
    ),
    tools=[
        # Composite / broad
        get_patient_summary,
        # Narrow single-section
        get_patient_demographics,
        get_active_medications,
        get_active_conditions,
        get_recent_observations,
        # Temporal / longitudinal
        get_recent_encounters,
        get_lab_trend,
        # Interpretive
        interpret_labs,
    ],
    before_model_callback=extract_fhir_context,
)