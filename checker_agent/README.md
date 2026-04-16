# Checker Agent

Privacy, scope, and accuracy guard for the healthcare multi-agent system.

## Flow

```
User message
    ↓
Orchestrator routes to Agent 1, 2, or 3
    ↓
Agent returns response to Orchestrator
    ↓
Orchestrator passes [original question + agent response] to Checker
    ↓
Checker returns approved/modified response
    ↓
User sees final answer
```

## What it validates

1. PRIVACY — response only contains the correct patient's data
2. SCOPE — response directly addresses what was asked
3. FACTUAL — response is grounded in actual FHIR data, not hallucinated

## Setup

```bash
pip install google-adk
cp checker_agent/.env.example checker_agent/.env
# Add your GOOGLE_API_KEY to .env
adk run checker_agent
```

## Tools

- `validate_response(original_question, agent_response, patient_name, patient_id)`
  Runs all three checks. Returns PASS or FAIL with issues list.

- `rewrite_blocked_response(original_question, blocked_response, issues)`
  Returns a safe fallback message for blocked responses. User never sees the original was blocked.
