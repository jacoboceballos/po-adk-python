#!/usr/bin/env bash
# test_cardiovascular_agent.sh — end-to-end test for the cardiovascular ASCVD agent
#
# Exercises the full request pipeline: API key enforcement → FHIR metadata
# extraction → session state → ASCVD risk calculation tools.
#
# Usage:
#   ./scripts/test_cardiovascular_agent.sh                        # uses http://127.0.0.1:8004
#   ./scripts/test_cardiovascular_agent.sh http://my-host:8004    # custom host
#   API_KEY=my-key ./scripts/test_cardiovascular_agent.sh         # custom API key
#
# Run the server first:
#   uvicorn cardiovascular_agent.app:a2a_app --host 127.0.0.1 --port 8004 --log-level info
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8004}"
RPC_URL="${BASE_URL%/}/"
API_KEY="${API_KEY:-my-secret-key-123}"

post_json() {
  local label="$1"
  local with_key="$2"
  local payload="$3"

  echo
  echo "===== ${label} ====="
  if [[ "$with_key" == "yes" ]]; then
    curl -sS -i -X POST "$RPC_URL" \
      -H 'Content-Type: application/json' \
      -H "X-API-Key: ${API_KEY}" \
      --data "$payload"
  else
    curl -sS -i -X POST "$RPC_URL" \
      -H 'Content-Type: application/json' \
      --data "$payload"
  fi
  echo
}

# ── Payloads ───────────────────────────────────────────────────────────────────

# Case A — Missing API key → 401
payload_no_key='{
  "jsonrpc": "2.0",
  "id": "case-a",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "message_id": "case-a-message",
      "role": "user",
      "parts": [
        {"kind": "text", "text": "What is this patient'\''s cardiovascular risk?"}
      ]
    }
  }
}'

# Case B — Valid key, no FHIR metadata → should suggest manual calculator
payload_no_fhir='{
  "jsonrpc": "2.0",
  "id": "case-b",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "message_id": "case-b-message",
      "role": "user",
      "parts": [
        {"kind": "text", "text": "Calculate the 10-year ASCVD risk for this patient."}
      ]
    }
  }
}'

# Case C — Valid key + FHIR context → full automatic ASCVD assessment
# Points at a demo FHIR server; the agent will attempt to pull demographics,
# labs, vitals, conditions, medications, and social history.
payload_ascvd_auto='{
  "jsonrpc": "2.0",
  "id": "case-c",
  "method": "message/send",
  "params": {
    "metadata": {
      "http://localhost:5139/schemas/a2a/v1/fhir-context": {
        "fhirUrl": "https://fhir.example.org/r4",
        "fhirToken": "token-sensitive-123456",
        "patientId": "patient-42"
      }
    },
    "message": {
      "kind": "message",
      "message_id": "case-c-message",
      "role": "user",
      "parts": [
        {"kind": "text", "text": "What is this patient'\''s 10-year ASCVD risk? Give me the full cardiovascular risk assessment."}
      ]
    }
  }
}'

# Case D — Manual what-if calculation (no FHIR needed)
# Tests the manual calculator with explicit clinical values.
payload_manual='{
  "jsonrpc": "2.0",
  "id": "case-d",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "message_id": "case-d-message",
      "role": "user",
      "parts": [
        {"kind": "text", "text": "Calculate the ASCVD risk for a 55-year-old White male with total cholesterol 213 mg/dL, HDL 50 mg/dL, systolic BP 140 mmHg, on BP treatment, no diabetes, current smoker."}
      ]
    }
  }
}'

# Case E — What-if scenario: same patient from Case D but quit smoking
payload_whatif='{
  "jsonrpc": "2.0",
  "id": "case-e",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "message_id": "case-e-message",
      "role": "user",
      "parts": [
        {"kind": "text", "text": "Now calculate the same patient'\''s risk if they quit smoking. 55-year-old White male, total cholesterol 213, HDL 50, systolic BP 140, on BP treatment, no diabetes, NOT a smoker. How much does the risk drop?"}
      ]
    }
  }
}'

# Case F — Age out of range (should return a clear error)
payload_out_of_range='{
  "jsonrpc": "2.0",
  "id": "case-f",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "message_id": "case-f-message",
      "role": "user",
      "parts": [
        {"kind": "text", "text": "Calculate the ASCVD risk for a 35-year-old White female with total cholesterol 200, HDL 55, systolic BP 120, no BP treatment, no diabetes, non-smoker."}
      ]
    }
  }
}'

# ── Run tests ──────────────────────────────────────────────────────────────────

echo "Target RPC endpoint: ${RPC_URL}"
echo "Using API key prefix: ${API_KEY:0:6}..."
echo "Run your server separately, for example:"
echo "  uvicorn cardiovascular_agent.app:a2a_app --host 127.0.0.1 --port 8004 --log-level info"

# Case A: no API key → 401
post_json "Case A — Missing API key (expect 401)" "no" "$payload_no_key"

# Case B: valid key, no FHIR context → agent should explain missing context and offer manual calc
post_json "Case B — Valid key, no FHIR metadata (expect missing-context guidance)" "yes" "$payload_no_fhir"

# Case C: valid key + FHIR context → full automatic ASCVD assessment
post_json "Case C — Valid key + FHIR context: automatic ASCVD assessment (expect assess_ascvd_risk tool call)" "yes" "$payload_ascvd_auto"

# Case D: manual calculation with explicit values
post_json "Case D — Manual ASCVD calculation (expect calculate_ascvd_risk_manual tool call)" "yes" "$payload_manual"

# Case E: what-if scenario — same patient quits smoking
post_json "Case E — What-if: quit smoking (expect lower risk than Case D)" "yes" "$payload_whatif"

# Case F: age out of validated range
post_json "Case F — Age out of range, 35yo (expect PCE validation error)" "yes" "$payload_out_of_range"

echo
echo "═══════════════════════════════════════════════════════════════════"
echo " Expected server log markers"
echo "═══════════════════════════════════════════════════════════════════"
echo "  Case A: security_rejected_missing_api_key"
echo "  Case B: hook_called_no_metadata → tool returns FHIR context missing"
echo "  Case C: hook_called_fhir_found → tool_assess_ascvd_risk"
echo "  Case D: tool_calculate_ascvd_risk_manual (with explicit values)"
echo "  Case E: tool_calculate_ascvd_risk_manual (smoking=False)"
echo "  Case F: tool_calculate_ascvd_risk_manual → age out of range error"
echo
echo "═══════════════════════════════════════════════════════════════════"
echo " What to verify"
echo "═══════════════════════════════════════════════════════════════════"
echo "  Case D should return a risk score (expect ~8-12% for this profile)"
echo "  Case E should return a LOWER risk than Case D (smoking cessation benefit)"
echo "  Case F should return a clear error about age 35 being outside 40-79 range"