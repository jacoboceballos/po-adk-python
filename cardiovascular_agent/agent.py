"""
cardiovascular_agent — Agent definition.

A clinical assistant specialising in cardiovascular risk assessment.
Computes the 10-year ASCVD risk using the ACC/AHA Pooled Cohort Equations,
pulling patient data from a connected FHIR R4 server or accepting manual input
for what-if scenarios.

FHIR credentials are injected via A2A message metadata and extracted into
session state by extract_fhir_context before every LLM call.

To customise:
  - Change model, description, and instruction below.
  - Add or remove tools from the tools=[...] list.
  - Add new tools in cardiovascular_agent/tools/ or import shared FHIR tools.
"""
from google.adk.agents import Agent

from shared.fhir_hook import extract_fhir_context
from shared.tools import (
    get_active_conditions,
    get_active_medications,
    get_patient_demographics,
    get_recent_observations,
)

from .tools import assess_ascvd_risk, calculate_ascvd_risk_manual

root_agent = Agent(
    name="cardiovascular_risk_agent",
    model="gemini-2.5-flash",
    description=(
        "A cardiovascular risk assessment assistant that computes 10-year ASCVD "
        "risk using the ACC/AHA Pooled Cohort Equations. Can pull patient data "
        "from a FHIR server automatically or accept manual inputs for what-if "
        "scenarios."
    ),
    instruction=(
        "You are a cardiovascular risk assessment specialist with secure, read-only "
        "access to a patient's FHIR health record.\n\n"

        "YOUR PRIMARY CAPABILITY:\n"
        "Calculate the 10-year atherosclerotic cardiovascular disease (ASCVD) risk "
        "using the 2013 ACC/AHA Pooled Cohort Equations, and provide guideline-based "
        "recommendations from the 2018 ACC/AHA Cholesterol Guideline.\n\n"

        "HOW TO RESPOND:\n"
        "- When asked about a patient's cardiovascular risk, use assess_ascvd_risk to "
        "  automatically pull all inputs from the FHIR server and compute the score.\n"
        "- When FHIR context is not available, or the user wants a what-if scenario "
        "  (e.g. 'What if we lower the BP to 130?'), use calculate_ascvd_risk_manual "
        "  with explicit values.\n"
        "- You can also use the shared FHIR tools (get_patient_demographics, "
        "  get_active_medications, get_active_conditions, get_recent_observations) to "
        "  answer supporting questions about the patient's record.\n\n"

        "OUTPUT FORMAT:\n"
        "Always include:\n"
        "  1. The 10-year ASCVD risk percentage and risk category\n"
        "  2. Which data values were used (and their sources)\n"
        "  3. ACC/AHA guideline-based recommendations (statin, lifestyle, additional)\n"
        "  4. Any warnings (e.g. missing data, default assumptions)\n\n"

        "For what-if scenarios, clearly show the baseline vs. modified risk so the "
        "clinician can see the delta.\n\n"

        "IMPORTANT:\n"
        "- The PCE is validated for ages 40-79. For patients outside this range, "
        "  explain the limitation and suggest alternatives.\n"
        "- Always clarify that this is a decision-support tool, not a substitute "
        "  for clinical judgement.\n"
        "- If required data is missing from the FHIR record, tell the user which "
        "  values are needed and suggest using the manual calculator."
    ),
    tools=[
        # Cardiovascular-specific tools
        assess_ascvd_risk,
        calculate_ascvd_risk_manual,
        # Shared FHIR tools for supporting queries
        get_patient_demographics,
        get_active_medications,
        get_active_conditions,
        get_recent_observations,
    ],
    before_model_callback=extract_fhir_context,
)