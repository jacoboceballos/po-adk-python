# Prompt Agent Template
### Built with Google ADK · A2A Protocol · Python

A production-ready starting point for building **external agents that connect to [Prompt Opinion](https://promptopinion.ai)** — the multi-agent platform for healthcare and enterprise workflows.

Clone this template, replace the example tools with your own, and you have a fully authenticated, observable agent that Prompt Opinion can discover and call.

---

## Contents

- [Prompt Agent Template](#prompt-agent-template)
    - [Built with Google ADK · A2A Protocol · Python](#built-with-google-adk--a2a-protocol--python)
  - [Contents](#contents)
  - [What this template gives you](#what-this-template-gives-you)
  - [How it works](#how-it-works)
  - [Quick start](#quick-start)
    - [Prerequisites](#prerequisites)
    - [1 — Clone the repository](#1--clone-the-repository)
    - [2 — Create a virtual environment and install dependencies](#2--create-a-virtual-environment-and-install-dependencies)
    - [3 — Configure environment variables](#3--configure-environment-variables)
    - [4 — Run the server](#4--run-the-server)
    - [5 — Verify it's running](#5--verify-its-running)
  - [Project structure](#project-structure)
    - [Which files to change](#which-files-to-change)
  - [Customisation guide](#customisation-guide)
    - [Scenario A — Simple agent, no FHIR](#scenario-a--simple-agent-no-fhir)
    - [Scenario B — FHIR-connected healthcare agent](#scenario-b--fhir-connected-healthcare-agent)
    - [Adding more tools](#adding-more-tools)
  - [FHIR context (optional)](#fhir-context-optional)
    - [How credentials flow](#how-credentials-flow)
    - [Metadata key](#metadata-key)
    - [What if FHIR context is not sent?](#what-if-fhir-context-is-not-sent)
    - [Log markers to watch](#log-markers-to-watch)
  - [Configuration reference](#configuration-reference)
  - [API security](#api-security)
    - [Default keys (change before deploying)](#default-keys-change-before-deploying)
    - [Endpoints](#endpoints)
    - [Error responses](#error-responses)
  - [Testing locally](#testing-locally)
    - [Test cases covered](#test-cases-covered)
  - [Connecting to Prompt Opinion](#connecting-to-prompt-opinion)
    - [Registration steps](#registration-steps)
    - [What Prompt Opinion provides](#what-prompt-opinion-provides)
  - [License](#license)

---

## What this template gives you

| Feature | Detail |
|---|---|
| **Agent framework** | [Google ADK](https://google.github.io/adk-docs/) with Gemini 2.0 Flash |
| **Transport protocol** | [A2A](https://google.github.io/A2A/) (Agent-to-Agent) over JSON-RPC / HTTP |
| **Security** | `X-API-Key` middleware — every request is authenticated |
| **FHIR integration** | Optional — FHIR credentials flow from Prompt Opinion into tool calls without touching the prompt |
| **Structured logging** | ANSI-colour logs with request payloads, security events, and FHIR fingerprints |
| **Agent card** | Published at `/.well-known/agent-card.json` for automatic discovery |
| **Example tools** | Four FHIR R4 query tools (demographics, medications, conditions, observations) |

> **The FHIR tools are examples.** The template works equally well for non-healthcare use cases — replace them with whatever tools your agent needs.

---

## How it works

```
Prompt Opinion                    Your agent (this template)
────────────                    ──────────────────────────
  │                                       │
  │  POST /                               │
  │  X-API-Key: <key>                     │
  │  {                              ApiKeyMiddleware
  │    "method": "message/stream",        │  validates key
  │    "params": {                        │  extracts + bridges
  │      "message": {                     │  FHIR metadata
  │        "metadata": { FHIR creds }     │
  │        "parts":    [ user text  ]     │
  │      }                                │
  │    }                          extract_fhir_context()
  │  }                                    │  stores credentials
  │                                       │  in session state
  │                                Gemini 2.0 Flash
  │                                       │  decides which tool
  │                                       │  to call
  │                              tools/fhir.py
  │                                       │  reads creds from state
  │                                       │  calls FHIR server
  │                                       │  returns structured data
  │                                       │
  │  ←── streaming SSE response ──────────│
```

**Key design principle:** FHIR credentials travel in the A2A message metadata — they never appear in the LLM prompt. The `extract_fhir_context` callback intercepts them before the model is called and stores them in session state, where tools can read them at call time.

---

## Quick start

### Prerequisites

- Python 3.11 or later
- A [Google AI Studio](https://aistudio.google.com/app/apikey) API key (free)
- Git

### 1 — Clone the repository

```bash
git clone https://github.com/your-org/my-adk-project.git
cd my-adk-project
```

### 2 — Create a virtual environment and install dependencies

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

### 3 — Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and set your Google API key:

```env
GOOGLE_API_KEY=your-google-api-key-here
```

### 4 — Run the server

```bash
uvicorn multi_tool_agent.app:a2a_app --host 0.0.0.0 --port 8001
```

### 5 — Verify it's running

```bash
curl http://localhost:8001/.well-known/agent-card.json
```

You should see the agent card JSON describing your agent's capabilities and security requirements.

---

## Project structure

```
my-adk-project/
│
├── multi_tool_agent/          # The agent package
│   │
│   ├── agent.py               # ★ START HERE — the only file you need to edit
│   │                          #   Defines root_agent: model, instruction, tools list
│   │
│   ├── tools/                 # Tool functions registered with the agent
│   │   ├── __init__.py        #   Catalogue + re-exports (add new tools here)
│   │   └── fhir.py            #   Example: FHIR R4 query tools
│   │
│   ├── fhir_hook.py           # before_model_callback — extracts FHIR credentials
│   │                          # from A2A metadata into session state
│   │
│   ├── middleware.py          # Starlette middleware — API key enforcement
│   │
│   ├── logging_utils.py       # ANSI colour logger, shared helpers
│   │
│   ├── app.py                 # Wires agent + middleware into an A2A ASGI app
│   │                          # (uvicorn entry point)
│   │
│   └── __init__.py            # Package bootstrap: loads .env, configures logging
│
├── scripts/
│   └── test_fhir_hook.sh      # End-to-end curl tests for the full pipeline
│
├── .env.example               # Environment variable template
├── requirements.txt           # Python dependencies
└── README.md
```

### Which files to change

| File | When to change it |
|---|---|
| `agent.py` | Always — update model, instruction, and tools list |
| `tools/fhir.py` | Replace FHIR queries with your own domain logic |
| `tools/__init__.py` | Add imports when you create new tool files |
| `middleware.py` | Update `VALID_API_KEYS` with your real keys |
| `app.py` | Update `agent_card` (name, description, URL, FHIR extension URI) |
| `fhir_hook.py` | Only if you need to change how context is extracted |

> `logging_utils.py` and `__init__.py` rarely need changing.

---

## Customisation guide

### Scenario A — Simple agent, no FHIR

Building a general-purpose agent that doesn't need patient data? Remove the FHIR-specific parts and write your own tools.

**`agent.py`** — swap the tools and rewrite the instruction:

```python
from google.adk.agents import Agent
from .tools import search_knowledge_base, send_notification   # your tools

root_agent = Agent(
    name="my_agent",
    model="gemini-2.0-flash",
    description="An agent that searches our knowledge base and sends notifications.",
    instruction=(
        "You are a helpful assistant. Use the available tools to answer questions "
        "and take actions. Never make up information."
    ),
    tools=[search_knowledge_base, send_notification],
    # No before_model_callback needed if you don't use session-injected context
)
```

**`app.py`** — update the agent card and remove the FHIR extension:

```python
agent_card = AgentCard(
    name="my_agent",
    description="An agent that searches our knowledge base.",
    url="http://localhost:8001",
    version="1.0.0",
    capabilities=AgentCapabilities(streaming=True, pushNotifications=False),
    # No extensions needed
    securitySchemes={ "apiKey": SecurityScheme(...) },
    security=[{"apiKey": []}],
)
```

---

### Scenario B — FHIR-connected healthcare agent

This is the default configuration of the template. Prompt Opinion passes FHIR credentials (server URL, bearer token, patient ID) in the A2A message metadata. The `extract_fhir_context` callback intercepts them before the model is called, and your tools read them from `tool_context.state`.

No changes needed to run the FHIR scenario — it works out of the box. To point it at a real FHIR server, your Prompt Opinion configuration must send the correct metadata (see [FHIR context](#fhir-context-optional)).

**Prompt Opinion will send credentials like this:**

```json
{
  "jsonrpc": "2.0",
  "method": "message/stream",
  "params": {
    "message": {
      "metadata": {
        "http://localhost:5139/schemas/a2a/v1/fhir-context": {
          "fhirUrl":   "https://your-fhir-server.example.org/r4",
          "fhirToken": "<bearer-token-from-your-identity-provider>",
          "patientId": "patient-uuid-here"
        }
      },
      "parts": [{ "kind": "text", "text": "What medications is this patient on?" }],
      "role": "user"
    }
  }
}
```

Your tools then read from state — no changes needed:

```python
def get_active_medications(tool_context: ToolContext) -> dict:
    fhir_url   = tool_context.state.get("fhir_url")    # set by extract_fhir_context
    fhir_token = tool_context.state.get("fhir_token")
    patient_id = tool_context.state.get("patient_id")
    # ... call the FHIR server
```

---

### Adding more tools

**Step 1** — Create a new file in `tools/` with your tool functions:

```python
# tools/scheduling.py
import logging
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

def get_upcoming_appointments(tool_context: ToolContext) -> dict:
    """Returns the patient's upcoming appointments."""
    patient_id = tool_context.state.get("patient_id", "unknown")
    logger.info("tool_get_upcoming_appointments patient_id=%s", patient_id)

    # Your implementation here
    return {
        "status": "success",
        "appointments": [ ... ]
    }
```

> **Convention:** always accept `tool_context: ToolContext` as the last parameter. Read any context you need from `tool_context.state`.

**Step 2** — Export it from `tools/__init__.py`:

```python
from .fhir import (
    get_patient_demographics,
    get_active_medications,
    get_active_conditions,
    get_recent_observations,
)
from .scheduling import get_upcoming_appointments   # ← add this

__all__ = [
    "get_patient_demographics",
    # ...
    "get_upcoming_appointments",                    # ← and this
]
```

**Step 3** — Register it in `agent.py`:

```python
from .tools import (
    get_active_conditions,
    get_active_medications,
    get_patient_demographics,
    get_recent_observations,
    get_upcoming_appointments,    # ← add this
)

root_agent = Agent(
    ...
    tools=[
        get_patient_demographics,
        get_active_medications,
        get_active_conditions,
        get_recent_observations,
        get_upcoming_appointments,    # ← and this
    ],
)
```

That's it. The Gemini model will automatically use the new tool when it's relevant.

---

## FHIR context (optional)

FHIR context is **completely optional**. Your agent works without it — tools that don't need patient data simply don't read those state values.

### How credentials flow

```
A2A request
  └── params.message.metadata
        └── "http://.../fhir-context": { fhirUrl, fhirToken, patientId }
              │
              ▼ (middleware bridges to params.metadata)
              │
              ▼ (ADK calls extract_fhir_context before every LLM call)
              │
              ▼
        session state
              ├── fhir_url   → tool_context.state["fhir_url"]
              ├── fhir_token → tool_context.state["fhir_token"]
              └── patient_id → tool_context.state["patient_id"]
```

### Metadata key

The FHIR context is keyed by the URI declared in your agent card's extension list. The default is:

```
http://localhost:5139/schemas/a2a/v1/fhir-context
```

Update this URI in `app.py` (`AgentExtension.uri`) to match your Prompt Opinion workspace URL before deploying.

### What if FHIR context is not sent?

If the metadata is missing or malformed, `extract_fhir_context` writes nothing to session state. Tools calling `_get_fhir_context()` will return a clear error message explaining that credentials were not provided. The agent will pass that message back to the caller rather than hallucinating data.

### Log markers to watch

| Log marker | Meaning |
|---|---|
| `FHIR_URL_FOUND` | FHIR server URL was received |
| `FHIR_TOKEN_FOUND fingerprint=len=N sha256=X` | Token received (value is never logged) |
| `FHIR_PATIENT_FOUND` | Patient ID was received |
| `hook_called_fhir_found` | All three credentials stored in session state |
| `hook_called_no_metadata` | Request had no metadata at all |
| `hook_called_fhir_not_found` | Metadata present but no FHIR key found |
| `hook_called_fhir_malformed` | FHIR key found but value was not a JSON object |

---

## Configuration reference

Copy `.env.example` to `.env` and set values before starting the server.

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_API_KEY` | **Yes** | — | Google AI Studio key for Gemini access |
| `LOG_FULL_PAYLOAD` | No | `true` | Log full JSON-RPC request body on each request |
| `LOG_HOOK_RAW_OBJECTS` | No | `false` | Dump raw ADK callback objects — enable only when debugging |

---

## API security

All endpoints except the agent card require an `X-API-Key` header.

### Default keys (change before deploying)

Open `middleware.py` and update `VALID_API_KEYS`:

```python
VALID_API_KEYS: set = {
    "my-secret-key-123",   # ← replace with your real keys
    "another-valid-key",
}
```

In production, load these from environment variables or a secrets manager:

```python
import os

VALID_API_KEYS: set = {
    k for k in [
        os.getenv("API_KEY_PRIMARY"),
        os.getenv("API_KEY_SECONDARY"),
    ]
    if k
}
```

### Endpoints

| Endpoint | Auth required | Description |
|---|---|---|
| `GET /.well-known/agent-card.json` | No | Agent discovery — callers read this first |
| `POST /` | Yes (`X-API-Key`) | A2A JSON-RPC — all agent interactions |

### Error responses

| Status | Meaning |
|---|---|
| `401 Unauthorized` | `X-API-Key` header missing |
| `403 Forbidden` | `X-API-Key` header present but key not recognised |

---

## Testing locally

A shell script is included that exercises the full pipeline with `curl`:

```bash
# Start the server first (in a separate terminal)
uvicorn multi_tool_agent.app:a2a_app --host 127.0.0.1 --port 8001 --log-level info

# Run all test cases
bash scripts/test_fhir_hook.sh
```

### Test cases covered

| Case | Description | Expected log marker |
|---|---|---|
| A | Missing API key | `security_rejected_missing_api_key` |
| B | Valid key, no metadata | `hook_called_no_metadata` |
| C | Valid key, wrong metadata key | `hook_called_fhir_not_found` |
| D | Valid key + FHIR context — clinical summary | `hook_called_fhir_found` |
| D2 | Valid key + FHIR context — vital signs query | `tool_get_recent_observations` |
| E | Valid key + malformed FHIR value | `hook_called_fhir_malformed` |

To test against a real FHIR server, update the `fhirUrl`, `fhirToken`, and `patientId` values in `payload_valid_fhir` inside the script.

---

## Connecting to Prompt Opinion

[Prompt Opinion](https://promptopinion.ai) is a multi-agent platform that orchestrates agents like this one — routing conversations, passing patient context, and composing results across multiple specialised agents.

### Registration steps

1. **Deploy your agent** to a publicly reachable URL (e.g. `https://my-agent.example.com`).

2. **Update the agent card URL** in `app.py`:
   ```python
   agent_card = AgentCard(
       ...
       url="https://my-agent.example.com",   # ← your public URL
       ...
   )
   ```

3. **Update the FHIR extension URI** in `app.py` to match your Prompt Opinion workspace:
   ```python
   AgentExtension(
       uri="https://your-promptopinion-workspace.example.com/schemas/a2a/v1/fhir-context",
       ...
   )
   ```

4. **Register the agent in Prompt Opinion** by providing:
   - Your agent card URL: `https://my-agent.example.com/.well-known/agent-card.json`
   - Your `X-API-Key` value (Prompt Opinion will send this on every request)

5. **Prompt Opinion discovers your agent** by fetching the agent card, learns that an API key is required, and begins routing requests to it.

### What Prompt Opinion provides

When your agent is called from Prompt Opinion, the platform automatically injects context into the A2A message metadata:

- The patient's **FHIR server URL** for your workspace
- A **short-lived bearer token** scoped to the current user session
- The **patient ID** selected in the active encounter

Your tools receive this context transparently from `tool_context.state` — you never need to handle authentication with the FHIR server yourself.

---

## License

MIT

---

*Built on [Google ADK](https://google.github.io/adk-docs/) and the [A2A protocol](https://google.github.io/A2A/). Designed for the [Prompt Opinion](https://promptopinion.ai) multi-agent platform.*
