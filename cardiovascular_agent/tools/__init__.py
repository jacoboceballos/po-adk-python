"""
cardiovascular_agent tools — re-exports all tool functions for this agent.

ASCVD tools (ascvd.py)
──────────────────────
  assess_ascvd_risk             Automatic — pulls data from FHIR and computes risk.
  calculate_ascvd_risk_manual   Manual — accepts explicit clinical values.
"""

from .ascvd import assess_ascvd_risk, calculate_ascvd_risk_manual

__all__ = [
    "assess_ascvd_risk",
    "calculate_ascvd_risk_manual",
]