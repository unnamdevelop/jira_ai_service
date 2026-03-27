"""
LangGraph orchestrator — wires all agents into the analysis pipeline.

Flow:
  invest_analyzer → quality_gate → [generate_tests | analyze_gaps] → END
"""

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.state import AgentState
from app.agents.invest_analyzer import invest_analyzer_agent
from app.agents.quality_gate import quality_gate_agent, route_after_quality_gate
from app.agents.test_generator import test_generator_agent
from app.agents.gap_analyzer import gap_analyzer_agent


def build_graph() -> StateGraph:
    print("🏗️  Building orchestrator graph...")

    workflow = StateGraph(AgentState)

    workflow.add_node("invest_analyzer", invest_analyzer_agent)
    workflow.add_node("quality_gate",    quality_gate_agent)
    workflow.add_node("generate_tests",  test_generator_agent)
    workflow.add_node("analyze_gaps",    gap_analyzer_agent)

    workflow.set_entry_point("invest_analyzer")
    workflow.add_edge("invest_analyzer", "quality_gate")
    workflow.add_conditional_edges(
        "quality_gate",
        route_after_quality_gate,
        {"generate_tests": "generate_tests", "analyze_gaps": "analyze_gaps"},
    )
    workflow.add_edge("generate_tests", END)
    workflow.add_edge("analyze_gaps",   END)

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)


# Module-level singleton — compiled once at import time
orchestrator = build_graph()
