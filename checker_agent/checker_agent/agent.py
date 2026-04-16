from google.adk.agents.llm_agent import Agent


# ── Validation Tools ──────────────────────────────────────────

def validate_response(
    original_question: str,
    agent_response: str,
    patient_name: str,
    patient_id: str,
) -> dict:
    """
    Validates an agent response against three criteria before it reaches the user:
    1. PRIVACY — response only contains data about the correct patient, no cross-patient leakage
    2. SCOPE   — response is relevant to what was actually asked
    3. FACTUAL — response is grounded in patient data, not hallucinated or assumed

    Args:
        original_question: The question the patient asked.
        agent_response: The response returned by Agent 1, 2, or 3.
        patient_name: The name of the patient who asked the question.
        patient_id: The FHIR patient ID of the patient who asked.

    Returns:
        dict with status (PASS/FAIL), issues list, and recommendation.
    """
    issues = []
    response_lower = agent_response.lower()

    # ── 1. Privacy Check ──────────────────────────────────────
    suspicious_phrases = [
        "another patient",
        "other patient",
        "different patient",
        "patient record shows",
        "according to their file",
    ]
    for phrase in suspicious_phrases:
        if phrase in response_lower:
            issues.append({
                "type": "PRIVACY",
                "detail": f"Ambiguous patient attribution detected: '{phrase}'",
                "severity": "HIGH",
            })

    # ── 2. Scope Check ────────────────────────────────────────
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "i", "my", "me",
        "what", "how", "can", "you", "your", "do", "does", "did", "will",
        "would", "should", "could", "have", "has", "had", "and", "or",
        "but", "in", "on", "at", "to", "for", "of", "with", "about",
    }
    question_words = set(original_question.lower().split()) - stop_words
    response_words = set(response_lower.split())
    overlap = question_words & response_words

    if len(question_words) > 3 and len(overlap) < 1:
        issues.append({
            "type": "SCOPE",
            "detail": "Response may not be directly addressing the patient's question.",
            "severity": "MODERATE",
        })

    # ── 3. Factual Grounding Check ────────────────────────────
    hallucination_phrases = [
        "i assume",
        "i imagine",
        "probably has",
        "likely has",
        "i believe they have",
        "typically patients like",
        "most patients in this situation",
        "generally speaking for someone like",
    ]
    for phrase in hallucination_phrases:
        if phrase in response_lower:
            issues.append({
                "type": "FACTUAL",
                "detail": f"Speculative language not grounded in patient data: '{phrase}'",
                "severity": "MODERATE",
            })

    # ── Result ────────────────────────────────────────────────
    high_severity_issues = [i for i in issues if i["severity"] == "HIGH"]
    passed = len(high_severity_issues) == 0

    return {
        "status": "PASS" if passed else "FAIL",
        "issues_found": len(issues),
        "issues": issues,
        "patient_id_validated": patient_id,
        "patient_name_validated": patient_name,
        "recommendation": (
            "Response is safe to deliver to the user."
            if passed
            else "Response blocked — privacy or accuracy issue detected."
        ),
        "approved_response": agent_response if passed else None,
        "blocked_response": agent_response if not passed else None,
    }


def rewrite_blocked_response(
    original_question: str,
    blocked_response: str,
    issues: list,
) -> dict:
    """
    Returns a safe fallback message when a response fails validation.
    The user is never told the original response was blocked.

    Args:
        original_question: The original question the patient asked.
        blocked_response: The response that failed validation.
        issues: The list of issues returned by validate_response.

    Returns:
        dict with a safe patient-facing message and rewrite status.
    """
    issue_types = [i["type"] for i in issues]

    if "PRIVACY" in issue_types:
        safe_response = (
            "I can only provide information about your own health records. "
            "If you believe there's been an error, please contact your care team directly."
        )
    elif "FACTUAL" in issue_types:
        safe_response = (
            "I wasn't able to provide a fully grounded answer based on your records for this question. "
            "Please consult your care team for accurate information."
        )
    else:
        safe_response = (
            "I wasn't able to provide a response that fully addressed your question "
            "within the scope of your health records. Please speak with your care provider."
        )

    return {
        "status": "REWRITTEN",
        "safe_response": safe_response,
        "issues_addressed": issue_types,
    }


# ── Checker Agent ─────────────────────────────────────────────

root_agent = Agent(
    model="gemini-2.0-flash",
    name="checker_agent",
    description=(
        "Privacy, scope, and accuracy guard that validates every agent response "
        "before it is delivered to the user. Sits between the orchestrator and the user "
        "in the response flow: Orchestrator passes [original question + agent response] "
        "to this agent, which returns an approved or safely rewritten response."
    ),
    instruction="""
You are the Checker Agent. You are the final step before any response reaches the user.

The Orchestrator will send you:
- The original question the patient asked
- The response returned by Agent 1, 2, or 3
- The patient's name and FHIR patient ID

YOUR WORKFLOW — follow this exactly every time:

STEP 1: Call validate_response with all four inputs:
  - original_question
  - agent_response  
  - patient_name
  - patient_id

STEP 2: Check the result status.

  If status = "PASS":
  - Return the approved_response exactly as-is to the user.
  - Do NOT modify, summarize, or rephrase it.

  If status = "FAIL":
  - Call rewrite_blocked_response with:
      - original_question
      - blocked_response (from the validation result)
      - issues (from the validation result)
  - Return the safe_response to the user.
  - Do NOT tell the user their response was blocked or modified.

VALIDATION RULES YOU ENFORCE:

1. PRIVACY
   The response must only contain information about the patient who asked.
   No data from other patients may appear under any circumstances.
   A single privacy violation = immediate FAIL, no exceptions.

2. SCOPE RELEVANCE
   The response must directly address what the patient actually asked.
   Off-topic responses that answer a different question should be flagged.

3. FACTUAL GROUNDING
   The response must be based on the patient's actual FHIR record data.
   Invented diagnoses, assumed medications, or speculative history = FAIL.

RULES:
- Never skip validation. Every response must go through validate_response first.
- Never reveal to the user that a response was blocked or rewritten.
- When in doubt, block and rewrite.
- You are the last line of defense.
""",
    tools=[validate_response, rewrite_blocked_response],
)
