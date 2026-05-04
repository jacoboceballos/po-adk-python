"""
Shared tools catalogue — re-exports all tool functions available in this library.

FHIR tools (fhir.py)
────────────────────
  get_patient_summary        Full clinical snapshot (demographics, meds, conditions, vitals, labs)
  get_patient_demographics   Patient name, DOB, gender, contacts
  get_active_medications     Active MedicationRequest resources
  get_active_conditions      Active Condition resources (problem list)
  get_recent_observations    Observation resources — vitals, labs, etc.
  get_recent_encounters      Recent clinical encounters (visits, admissions)
  get_lab_trend              Time-series values for a specific LOINC-coded lab
  interpret_labs             Recent labs annotated with normal/low/high flags

To add new shared tools:
  1. Create a new file in shared/tools/ (e.g. scheduling.py).
  2. Write your tool functions there (last param must be tool_context: ToolContext).
  3. Import and re-export them below.
  4. Add them to the tools=[...] list in whichever agent(s) need them.
"""

from .fhir import (
    get_active_conditions,
    get_active_medications,
    get_lab_trend,
    get_patient_demographics,
    get_patient_summary,
    get_recent_encounters,
    get_recent_observations,
    interpret_labs,
)

__all__ = [
    "get_patient_summary",
    "get_patient_demographics",
    "get_active_medications",
    "get_active_conditions",
    "get_recent_observations",
    "get_recent_encounters",
    "get_lab_trend",
    "interpret_labs",
]