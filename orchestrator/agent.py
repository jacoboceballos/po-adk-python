"""
orchestrator — Multi-agent orchestrator.

This agent delegates to specialist sub-agents using ADK's AgentTool.
Gemini decides which sub-agent to call based on the question.

Sub-agents run in-process (same Python process, not separate HTTP calls).
Session state is shared, so FHIR credentials extracted by this agent's
before_model_callback are available to the healthcare sub-agent's tools.

Sub-agents registered:
  healthcare_fhir_agent      — patient demographics, medications, conditions, observations
  general_agent              — date/time queries, ICD-10 code lookups
  cardiovascular_risk_agent  — 10-year ASCVD risk calculation and cardiovascular guidance

To add another sub-agent:
  1. Create a new agent package (copy healthcare_agent or general_agent as a template).
  2. Import its root_agent here.
  3. Add AgentTool(agent=your_new_agent) to the tools list.
  4. Update the instruction to describe when to use it.
"""
from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from healthcare_agent.agent import root_agent as healthcare_agent
from general_agent.agent import root_agent as general_agent
from cardiovascular_agent.agent import root_agent as cardiovascular_agent
from shared.fhir_hook import extract_fhir_context

root_agent = Agent(
    name="orchestrator",
    model="gemini-2.5-flash",
    description=(
        "A clinical orchestrator that routes questions to the right specialist agent. "
        "Delegates FHIR patient data queries to healthcare_fhir_agent, "
        "cardiovascular risk assessment to cardiovascular_risk_agent, and "
        "general clinical queries to general_agent."
    ),
    instruction=(
        "You are a clinical orchestrator. Your job is to route each question to the "
        "most appropriate specialist agent and return their response.\n\n"
        "Use healthcare_fhir_agent for:\n"
        "  - Patient demographics (name, DOB, gender, contacts)\n"
        "  - Active medications and dosage instructions\n"
        "  - Active conditions and diagnoses (problem list)\n"
        "  - Recent observations — vitals, lab results, social history\n\n"
        "Use cardiovascular_risk_agent for:\n"
        "  - 10-year ASCVD risk assessment (automatic from FHIR or manual what-if)\n"
        "  - Cardiovascular risk factor analysis\n"
        "  - Statin therapy and lifestyle recommendations based on ACC/AHA guidelines\n"
        "  - What-if scenarios (e.g. 'What if BP drops to 130?' or 'What if they quit smoking?')\n\n"
        "Use general_agent for:\n"
        "  - Current date and time in any timezone\n"
        "  - ICD-10-CM code lookups\n\n"
        "Always tell the user which agent you are calling and why. "
        "If a sub-agent returns an error, relay it clearly and suggest a resolution."
    ),
    tools=[
        AgentTool(agent=healthcare_agent),
        AgentTool(agent=general_agent),
        AgentTool(agent=cardiovascular_agent),
    ],
    # The orchestrator extracts FHIR context once into session state.
    # The healthcare and cardiovascular sub-agents' tools read from that same shared state.
    before_model_callback=extract_fhir_context,
)