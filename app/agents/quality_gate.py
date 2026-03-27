"""
Agent 2: Quality Gate
Compares the DoR score against the configured threshold and sets quality_gate_passed.
"""

from typing import Literal

from app.config import settings
from app.state import AgentState


def quality_gate_agent(state: AgentState) -> AgentState:
    """Agent 2: Quality Gate Decision (threshold defined in settings.QG_THRESHOLD)."""
    print("🚦 [AGENT 2] Evaluating Quality Gate")

    threshold = settings.QG_THRESHOLD
    score     = state["invest_score"]
    passed    = score >= threshold
    status    = "PASSED ✅" if passed else "FAILED ❌"

    print(f"🚦 [AGENT 2] Score: {score}/25 (Threshold: {threshold}/25) → {status}")

    return {
        **state,
        "quality_gate_passed": passed,
        "messages": [f"🚦 Quality gate {status} (Score: {score}/25, Threshold: {threshold}/25)"],
    }


def route_after_quality_gate(state: AgentState) -> Literal["generate_tests", "analyze_gaps"]:
    if state["quality_gate_passed"]:
        print("🔀 [ROUTER] Quality gate PASSED → Routing to Test Generator")
        return "generate_tests"
    print("🔀 [ROUTER] Quality gate FAILED → Routing to Gap Analyzer")
    return "analyze_gaps"
