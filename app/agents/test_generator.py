"""
Agent 3: BDD Test Scenario Generator
Generates Gherkin-formatted acceptance criteria for stories that pass the quality gate.
"""

from openai import OpenAI

from app.config import settings
from app.state import AgentState

_client = OpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = """
You are a Senior QA Automation Architect. Generate comprehensive BDD test scenarios in standard Gherkin format.

IMPORTANT: Use proper Gherkin syntax with NO numbered lists or bullet points. JIRA will auto-format it.

Generate:
- 3 Functional Scenarios (Happy path and main acceptance criteria)
- 2 Negative Scenarios (Error handling and invalid inputs)
- 2 Edge Cases (Boundary conditions and unusual situations)

Format EXACTLY like this example:

Feature: [Feature Name]
  [Brief feature description]

Background:
  Given [common precondition for all scenarios]
  And [another common precondition]

# Functional Scenarios
Scenario: [Descriptive scenario name]
  Given [precondition]
  When [action]
  Then [expected outcome]

# Negative Scenarios
Scenario: [Negative scenario name]
  Given [precondition]
  When [action]
  Then [expected outcome]

# Edge Cases
Scenario: [Edge case scenario name]
  Given [precondition]
  When [action]
  Then [expected outcome]

CRITICAL FORMATTING RULES:
1. Section markers MUST start with exactly: # Functional Scenarios / # Negative Scenarios / # Edge Cases
2. NO numbered prefixes on section markers
3. NO bullet points (*, -, •)
4. Use ONLY Gherkin keywords: Feature, Background, Scenario, Given, When, Then, And, But
5. Each step on its own line with 2-space indentation
6. Blank line between scenarios
"""


def test_generator_agent(state: AgentState) -> AgentState:
    """Agent 3: Generates BDD test scenarios for stories that pass the quality gate."""
    print(f"🧪 [AGENT 3] Generating BDD Test Scenarios for {state['story_key']}")

    user_prompt = f"Story Summary: {state['summary']}\n\nStory Description: {state['description']}"
    if state.get("team_comments"):
        user_prompt += (
            f"\n\n{'='*60}\n"
            f"TEAM COMMENTS (grooming clarifications & decisions):\n"
            f"Use these to generate more accurate, context-aware scenarios.\n"
            f"Cover any edge cases, constraints, or decisions mentioned in the comments.\n"
            f"{'='*60}\n"
            f"{state['team_comments']}"
        )

    try:
        response = _client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,
        )

        test_scenarios = response.choices[0].message.content
        print("✅ [AGENT 3] Test scenarios generated successfully")

        return {
            **state,
            "test_scenarios": test_scenarios,
            "messages":       [f"✅ Generated {test_scenarios.count('Scenario:')} test scenarios"],
            "error":          "",
        }

    except Exception as e:
        print(f"❌ [AGENT 3] Error: {e}")
        return {
            **state,
            "test_scenarios": f"Test generation failed: {e}",
            "messages":       [f"❌ Test generation failed: {str(e)}"],
            "error":          str(e),
        }
