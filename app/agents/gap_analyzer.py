"""
Agent 4: Gap Analyzer
Provides actionable recommendations for stories that fail the quality gate.
"""

from openai import OpenAI

from app.config import settings
from app.state import AgentState

_client = OpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = """
You are a Senior BA Coach. This user story failed the DoR quality gate.

Analyze the DoR Assessment report and provide:
1. **Specific Gaps:** What's missing or unclear?
2. **Root Causes:** Why did each DoR criterion score low?
3. **Actionable Recommendations:** Concrete steps to improve the story
4. **Questions for Clarification:** What does the BA need to answer?

Format:
---
GAP ANALYSIS REPORT

CRITICAL GAPS IDENTIFIED:
[List specific issues]

DoR CRITERIA ANALYSIS:
[For each low-scoring criterion, explain why and how to fix]

RECOMMENDED ACTIONS:
1. [Action item 1]
2. [Action item 2]

CLARIFICATION QUESTIONS:
- [Question 1]
- [Question 2]

NEXT STEPS:
[What the BA should do to resubmit this story]
---
"""


def gap_analyzer_agent(state: AgentState) -> AgentState:
    """Agent 4: Gap Analyzer — produces recommendations for stories that fail QG."""
    print(f"📋 [AGENT 4] Analyzing gaps in {state['story_key']}")

    context = (
        f"INVEST Report:\n{state['invest_report']}\n\n"
        f"Story Summary: {state['summary']}\n"
        f"Story Description: {state['description']}"
    )

    try:
        response = _client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": context},
            ],
            temperature=0.3,
        )

        gap_analysis = response.choices[0].message.content
        print("✅ [AGENT 4] Gap analysis completed")

        return {
            **state,
            "gap_analysis": gap_analysis,
            "messages":     ["✅ Gap analysis completed with recommendations"],
            "error":        "",
        }

    except Exception as e:
        print(f"❌ [AGENT 4] Error: {e}")
        return {
            **state,
            "gap_analysis": f"Gap analysis failed: {e}",
            "messages":     [f"❌ Gap analysis failed: {str(e)}"],
            "error":        str(e),
        }
