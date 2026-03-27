"""
Shared state definition for the LangGraph orchestration pipeline.
All agents read from and write to this TypedDict.
"""

import operator
from typing import TypedDict, Annotated


class AgentState(TypedDict):
    # ── Input ─────────────────────────────────────────────────────────────
    story_key: str
    summary: str
    description: str
    team_comments: str          # Human-authored Jira comments (AI comments excluded)

    # ── Agent outputs ─────────────────────────────────────────────────────
    invest_report: str
    invest_score: int
    quality_gate_passed: bool
    test_scenarios: str
    gap_analysis: str

    # ── Orchestration metadata ────────────────────────────────────────────
    messages: Annotated[list[str], operator.add]
    retry_count: int
    error: str
