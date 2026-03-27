# JIRA AI Analysis Service

AI-powered Jira story analyser using LangGraph multi-agent orchestration.

## Project Structure

```
jira_ai_service/
├── main.py                        ← FastAPI app + webhook routes
├── requirements.txt
├── Dockerfile
├── .env.example                   ← Copy to .env and fill in values
├── .gitignore
│
├── app/
│   ├── config.py                  ← All env var loading (single source of truth)
│   ├── state.py                   ← AgentState TypedDict
│   │
│   ├── agents/
│   │   ├── invest_analyzer.py     ← Agent 1: DoR Analyzer (GPT-4o)
│   │   ├── quality_gate.py        ← Agent 2: Quality Gate + Router
│   │   ├── test_generator.py      ← Agent 3: BDD Test Scenario Generator
│   │   └── gap_analyzer.py        ← Agent 4: Gap Analyzer
│   │
│   ├── graph/
│   │   └── orchestrator.py        ← LangGraph StateGraph wiring
│   │
│   ├── jira/
│   │   ├── client.py              ← All Jira REST API calls
│   │   └── adf_helpers.py         ← Atlassian Document Format builders
│   │
│   ├── zephyr/
│   │   └── client.py              ← All Zephyr Scale Cloud API v2 calls
│   │
│   └── services/
│       ├── email_service.py       ← HTML email notifications (SMTP)
│       └── report_builder.py      ← Local .txt report file writer
│
├── tests/
│   ├── unit/
│   │   └── test_quality_gate.py   ← Unit tests (no API calls)
│   └── integration/               ← Integration tests (requires .env)
│
├── reports/                       ← Generated report files (gitignored)
└── scripts/                       ← Utility scripts
```

## Local Setup

```bash
# 1. Clone and enter the project
cd jira_ai_service

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and fill in all values

# 5. Run the server
uvicorn main:app --reload --port 8000
```

## Running Tests

```bash
pytest tests/unit/
```

## Jira Automation Rules

Configure two webhook rules in Jira Automation → point to:
- Rule 1 (story created): `POST http://localhost:8000/webhook/jira-ai-trigger`
- Rule 2 (label added):   `POST http://localhost:8000/webhook/jira-zephyr-upload`

For local development, use [ngrok](https://ngrok.com) to expose localhost:
```bash
ngrok http 8000
# Copy the https URL and use it in Jira Automation Rules
```

## Pipeline Flow

```
Story Created
    │
    ▼
Agent 1: DoR Analyzer (GPT-4o) — scores 1-25
    │
    ▼
Agent 2: Quality Gate (threshold: 18/25)
    │
    ├── PASS ──► Agent 3: BDD Test Generator
    │                │
    │                └── Append Gherkin to Jira Description (ADF)
    │                    Add label: AI-Ready
    │                    Email reporter
    │
    └── FAIL ──► Agent 4: Gap Analyzer
                     │
                     └── Post Gap Analysis as Jira comment
                         Add label: AI-NeedsRefinement
                         Email reporter

BA Reviews Scenarios
    │
    └── Adds label: Approve-Zephyr-Upload
            │
            ▼
    Zephyr Upload Webhook
        │
        └── Create folder, upload test cases, update label → UploadedToZephyr
```
