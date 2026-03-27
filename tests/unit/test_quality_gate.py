"""
Unit tests for the quality gate agent.
Run with: pytest tests/
"""

import pytest
from app.agents.quality_gate import quality_gate_agent, route_after_quality_gate


def _base_state(score: int) -> dict:
    return {
        "story_key":           "TEST-1",
        "summary":             "Test story",
        "description":         "Test description",
        "team_comments":       "",
        "invest_report":       "DoR SCORE: {}/25".format(score),
        "invest_score":        score,
        "quality_gate_passed": False,
        "test_scenarios":      "",
        "gap_analysis":        "",
        "messages":            [],
        "retry_count":         0,
        "error":               "",
    }


def test_quality_gate_passes_at_threshold():
    state = _base_state(18)
    result = quality_gate_agent(state)
    assert result["quality_gate_passed"] is True


def test_quality_gate_fails_below_threshold():
    state = _base_state(17)
    result = quality_gate_agent(state)
    assert result["quality_gate_passed"] is False


def test_quality_gate_passes_at_max():
    state = _base_state(25)
    result = quality_gate_agent(state)
    assert result["quality_gate_passed"] is True


def test_router_pass_path():
    state = _base_state(20)
    state["quality_gate_passed"] = True
    assert route_after_quality_gate(state) == "generate_tests"


def test_router_fail_path():
    state = _base_state(10)
    state["quality_gate_passed"] = False
    assert route_after_quality_gate(state) == "analyze_gaps"
