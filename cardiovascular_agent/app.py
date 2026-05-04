"""
cardiovascular_agent — A2A application entry point.

Start the server with:
    uvicorn cardiovascular_agent.app:a2a_app --host 0.0.0.0 --port 8004

The agent card is served publicly at:
    GET http://localhost:8004/.well-known/agent-card.json

All other endpoints require an X-API-Key header (see shared/middleware.py).
"""
import os

from a2a.types import AgentSkill
from shared.app_factory import create_a2a_app

from .agent import root_agent

a2a_app = create_a2a_app(
    agent=root_agent,
    name="cardiovascular_risk_agent",
    description=(
        "A cardiovascular risk assessment assistant that computes 10-year ASCVD risk "
        "using the ACC/AHA Pooled Cohort Equations. Can pull patient data from a FHIR "
        "server automatically or accept manual inputs for what-if scenarios."
    ),
    url=os.getenv("CARDIOVASCULAR_AGENT_URL", os.getenv("BASE_URL", "http://localhost:8004")),
    port=8004,
    # FHIR context is declared but not strictly required — the agent can also
    # run manual what-if calculations without a connected FHIR server.
    # Set PO_PLATFORM_BASE_URL=https://app.promptopinion.ai in your .env
    fhir_extension_uri=(
        f"{os.getenv('PO_PLATFORM_BASE_URL', 'https://app.promptopinion.ai')}"
        f"/schemas/a2a/v1/fhir-context"
    ),
    require_api_key=True,
    skills=[
        AgentSkill(
            id="ascvd-risk-assessment",
            name="ascvd-risk-assessment",
            description=(
                "Computes the 10-year atherosclerotic cardiovascular disease (ASCVD) risk "
                "using the 2013 ACC/AHA Pooled Cohort Equations. Automatically pulls inputs "
                "from the patient's FHIR record (age, sex, race, cholesterol, BP, diabetes, "
                "smoking status, medications) and returns a risk percentage with ACC/AHA "
                "guideline-based statin and lifestyle recommendations."
            ),
            tags=["cardiovascular", "ascvd", "risk", "fhir"],
        ),
        AgentSkill(
            id="ascvd-risk-manual",
            name="ascvd-risk-manual",
            description=(
                "Calculates 10-year ASCVD risk from explicitly provided clinical values. "
                "Use for what-if scenarios (e.g. 'What if we lower BP to 130?', "
                "'What if the patient quits smoking?') or when FHIR context is unavailable."
            ),
            tags=["cardiovascular", "ascvd", "risk", "what-if"],
        ),
    ],
)