"""
orchestrator — Multi-agent orchestrator with plan-then-execute routing.

Flow per request:
  1. PLAN       State which specialist(s) will be called and in what order.
  2. EXECUTE    Call sub-agents via AgentTool (in-process, shared session state).
  3. SYNTHESIZE Combine sub-agent outputs into a single coherent answer.

Sub-agents run in-process (same Python process, not separate HTTP calls).
Session state is shared, so FHIR credentials extracted by this agent's
before_model_callback are available to every FHIR-touching sub-agent's tools.

Sub-agents registered:
  Tier 2 — Data retrieval:
    healthcare_fhir_agent         demographics, meds, conditions, observations,
                                  encounters, lab trends, lab interpretation
    general_agent                 date/time queries, ICD-10 code lookups
  Tier 3 — Clinical reasoning:
    cardiovascular_risk_agent     10-year ASCVD risk, CV risk factors, ACC/AHA guidance

Observability:
  The orchestrator logs its inbound query and the planning line it produces,
  so demos and debugging can see *why* a routing decision was made, not just
  which tools were called.

To add another sub-agent:
  1. Create a new agent package.
  2. Import its root_agent here.
  3. Add AgentTool(agent=your_new_agent) to the tools list.
  4. Update the ROUTING GUIDE section of the instruction.
"""
import logging
import re

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from healthcare_agent.agent import root_agent as healthcare_agent
from general_agent.agent import root_agent as general_agent
from cardiovascular_agent.agent import root_agent as cardiovascular_agent
from shared.fhir_hook import extract_fhir_context

logger = logging.getLogger(__name__)

# Matches "Plan: ..." at the start of a line (case-insensitive), capturing the
# rest of that line.  Used to surface the orchestrator's routing reasoning in
# server logs without relying on the full response text.
_PLAN_LINE_RE = re.compile(r"^\s*plan\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


# ── Callback helpers ───────────────────────────────────────────────────────────

def _log_inbound_query(callback_context, llm_request):
    """
    Before-model callback — logs the user's query and current patient context.

    Runs after extract_fhir_context so that patient_id is already in session
    state (if FHIR credentials were provided).  Never modifies the request.
    """
    try:
        contents = getattr(llm_request, "contents", None) or []
        user_text = None
        for content in reversed(contents):
            role = getattr(content, "role", None)
            if role == "user":
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    text = getattr(part, "text", None)
                    if text:
                        user_text = text
                        break
                if user_text:
                    break

        patient_id = ""
        state = getattr(callback_context, "state", None)
        if state is not None:
            try:
                patient_id = state.get("patient_id", "") or ""
            except Exception:
                patient_id = ""

        logger.info(
            "orchestrator_inbound_query patient_id=%s query=%r",
            patient_id or "[none]",
            (user_text or "")[:500],
        )
    except Exception as e:
        logger.debug("orchestrator_inbound_query_log_failed error=%s", e)
    return None


def _chain_before_model_callbacks(*callbacks):
    """
    Compose multiple before_model_callback functions into one.

    Each callback runs in order.  If any callback returns a non-None value
    (an LlmResponse), the chain short-circuits and returns it — matching
    ADK's documented behaviour for a single callback that preempts the model.
    """
    def _composed(callback_context, llm_request):
        for cb in callbacks:
            result = cb(callback_context, llm_request)
            if result is not None:
                return result
        return None
    return _composed


def _log_orchestrator_plan(callback_context, llm_response):
    """
    After-model callback — extracts and logs the 'Plan: ...' line if present.

    The instruction asks the orchestrator to start every response with a
    'Plan: ...' line.  Pulling that out into the server log gives demos and
    debugging a single grep-able marker for every routing decision.
    Never modifies the response.
    """
    try:
        content = getattr(llm_response, "content", None)
        parts = getattr(content, "parts", None) or []
        text = ""
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                text += t

        if not text:
            return None

        match = _PLAN_LINE_RE.search(text)
        if match:
            plan = match.group(1).strip()
            logger.info("orchestrator_plan plan=%r", plan[:500])
        else:
            logger.info("orchestrator_plan plan=[none_produced]")
    except Exception as e:
        logger.debug("orchestrator_plan_log_failed error=%s", e)
    return None


# ── Agent definition ───────────────────────────────────────────────────────────

root_agent = Agent(
    name="orchestrator",
    model="gemini-3.1-flash-lite-preview",
    description=(
        "A clinical orchestrator that plans multi-step routing to specialist "
        "sub-agents across data retrieval and clinical reasoning, "
        "then combines their outputs into a single answer."
    ),
    instruction=(
        "You are a clinical orchestrator. You DO NOT answer clinical questions "
        "yourself — you route to specialists and synthesize their responses.\n"
        "\n"
        "═══════════════════════════════════════════════════════════════════\n"
        " WORKFLOW — follow these three phases for every request\n"
        "═══════════════════════════════════════════════════════════════════\n"
        "\n"
        "PHASE 1 — PLAN\n"
        "Begin your response with a single line starting 'Plan:' that names the\n"
        "specialist(s) you will call and the order. Keep it to one sentence.\n"
        "Every response starts with a Plan line, even single-specialist calls —\n"
        "it is the signal that you have decided where to route.\n"
        "\n"
        "PHASE 2 — EXECUTE\n"
        "Call each specialist in the order named in your Plan. When a later\n"
        "specialist needs data an earlier one already fetched, pass it forward\n"
        "explicitly — do NOT ask the second specialist to re-fetch from FHIR.\n"
        "Keep numbers, dose strengths, and codes verbatim when forwarding.\n"
        "\n"
        "PHASE 3 — SYNTHESIZE\n"
        "Combine specialist outputs into a single coherent answer. Do not just\n"
        "concatenate — integrate. Highlight connections between findings (e.g.\n"
        "'the patient's LDL of 160 combined with smoking and hypertension\n"
        "yields an ASCVD risk of 14%'). Cite which specialist provided which\n"
        "piece of information only if it helps the user trust the answer.\n"
        "\n"
        "═══════════════════════════════════════════════════════════════════\n"
        " ROUTING GUIDE — match the question type, then pick the specialist\n"
        "═══════════════════════════════════════════════════════════════════\n"
        "\n"
        "── Tier 2: Data retrieval ──────────────────────────────────────────\n"
        "\n"
        "FULL CLINICAL OVERVIEW? ('summary', 'clinical picture', 'what's going on')\n"
        "  → healthcare_fhir_agent — it has a get_patient_summary tool that\n"
        "    returns demographics, meds, conditions, vitals, and labs in ONE\n"
        "    parallel call. Use this whenever the user wants a broad view.\n"
        "    Do NOT call the four narrow tools sequentially for the same ask.\n"
        "\n"
        "DEMOGRAPHICS / MEDS / CONDITIONS / RECENT OBSERVATIONS alone?\n"
        "  → healthcare_fhir_agent with the specific narrow tool. Faster and\n"
        "    less noisy than the full summary when the question is narrow.\n"
        "\n"
        "ENCOUNTERS? ('last seen', 'admitted recently', 'visits')\n"
        "  → healthcare_fhir_agent — it has get_recent_encounters.\n"
        "\n"
        "LAB TRENDS? ('HbA1c trending', 'creatinine history', 'last N values')\n"
        "  → healthcare_fhir_agent — it has get_lab_trend(loinc_code, count).\n"
        "\n"
        "LAB INTERPRETATION? ('are these in range', 'any abnormals', 'how are\n"
        "the labs')\n"
        "  → healthcare_fhir_agent — it has interpret_labs which annotates\n"
        "    recent labs with normal/low/high against adult reference ranges.\n"
        "\n"
        "CODE LOOKUP? (ICD-10 for a named condition)\n"
        "  → general_agent — built-in table of ~85 common conditions.\n"
        "\n"
        "WHAT TIME IS IT? (current date/time in any timezone)\n"
        "  → general_agent.\n"
        "\n"
        "── Tier 3: Clinical reasoning ──────────────────────────────────────\n"
        "\n"
        "CARDIOVASCULAR RISK (QUANTITATIVE)? ('10-year ASCVD', 'risk score',\n"
        "'statin candidate', what-if scenarios)\n"
        "  → cardiovascular_risk_agent — has assess_ascvd_risk (automatic\n"
        "    from FHIR) and calculate_ascvd_risk_manual (user-supplied values).\n"
        "\n"
        "CARDIOVASCULAR RISK FACTORS (QUALITATIVE, NO SCORE)?\n"
        "('what CV risk factors does this patient have', 'risk-enhancing factors')\n"
        "  → cardiovascular_risk_agent — prefer the get_cv_risk_factors tool.\n"
        "    Much faster than assess_ascvd_risk when the user wants\n"
        "    enumeration, not a score.\n"
        "\n"
        "── Multi-part questions ────────────────────────────────────────────\n"
        "\n"
        "Complex queries often span multiple tiers. Common patterns:\n"
        "  • 'Statin candidate?'      → healthcare + cardiovascular\n"
        "  • 'Full CV picture'        → healthcare + cardiovascular (baseline +\n"
        "                                what-if scenarios)\n"
        "\n"
        "When using multiple specialists, order them so later ones can\n"
        "consume earlier ones' output. Pass forwarded data explicitly.\n"
        "\n"
        "═══════════════════════════════════════════════════════════════════\n"
        " MULTI-HOP EXAMPLES\n"
        "═══════════════════════════════════════════════════════════════════\n"
        "\n"
        "Q: 'Is this patient a statin candidate?'\n"
        "   Plan: call healthcare_fhir_agent for current meds and conditions,\n"
        "         then cardiovascular_risk_agent for ASCVD risk, then synthesize.\n"
        "\n"
        "Q: 'What's the ICD-10 for this patient's primary condition?'\n"
        "   Plan: call healthcare_fhir_agent for active conditions, then\n"
        "         general_agent for the ICD-10 code of the top condition.\n"
        "\n"
        "Q: 'What if this patient quit smoking — how much would CV risk drop?'\n"
        "   Plan: call cardiovascular_risk_agent for the what-if scenario.\n"
        "\n"
        "Q: 'Full cardiovascular picture — ASCVD, risk-enhancing factors, ICD-10\n"
        "    for primary driver, and what-if on smoking cessation with SBP 130.'\n"
        "   Plan: call cardiovascular_risk_agent for baseline ASCVD, then\n"
        "         general_agent for the ICD-10 of the top condition, then\n"
        "         cardiovascular_risk_agent again for the what-if, then\n"
        "         synthesize.\n"
        "\n"
        "Q: 'What's the current time in Tokyo?'\n"
        "   Plan: call general_agent for the time in Asia/Tokyo.\n"
        "\n"
        "═══════════════════════════════════════════════════════════════════\n"
        " ERROR HANDLING\n"
        "═══════════════════════════════════════════════════════════════════\n"
        "\n"
        "If a sub-agent returns an error, surface it clearly to the user with\n"
        "a suggested resolution. Do NOT retry silently, and do NOT invent data\n"
        "to fill the gap.\n"
        "\n"
        "When partial failure is recoverable, recover partially:\n"
        "  • If FHIR context is missing entirely for a FHIR-requiring specialist,\n"
        "    say so plainly and offer manual alternatives where they exist\n"
        "    (cardiovascular has calculate_ascvd_risk_manual).\n"
        "\n"
        "Never continue a chain as if a failed call succeeded.\n"
    ),
    tools=[
        AgentTool(agent=healthcare_agent),
        AgentTool(agent=general_agent),
        AgentTool(agent=cardiovascular_agent),
    ],
    # Chain: FHIR context extraction first (populates session state), then
    # inbound-query logging (which reads patient_id from that state).
    before_model_callback=_chain_before_model_callbacks(
        extract_fhir_context,
        _log_inbound_query,
    ),
    # Logs the 'Plan: ...' line the model produced, so every routing decision
    # is grep-able in server logs.
    after_model_callback=_log_orchestrator_plan,
)
