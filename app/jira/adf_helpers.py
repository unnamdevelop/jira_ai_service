"""
Atlassian Document Format (ADF) helper builders.
These produce ADF node dicts used when writing to Jira description fields.
"""

import re


# ── Primitive node builders ───────────────────────────────────────────────────

def adf_paragraph(text: str) -> dict:
    """Plain text paragraph node."""
    return {
        "type": "paragraph",
        "content": [{"type": "text", "text": text}],
    }


def adf_bold_paragraph(text: str) -> dict:
    """Single-line paragraph where the entire text is bold."""
    return {
        "type": "paragraph",
        "content": [
            {
                "type": "text",
                "text": text,
                "marks": [{"type": "strong"}],
            }
        ],
    }


def adf_code_block(text: str) -> dict:
    """Code block node — preserves Gherkin indentation and keywords exactly."""
    return {
        "type": "codeBlock",
        "attrs": {"language": "gherkin"},
        "content": [{"type": "text", "text": text}],
    }


def adf_rule() -> dict:
    """Horizontal rule node."""
    return {"type": "rule"}


# ── Composite builder ─────────────────────────────────────────────────────────

def build_acceptance_criteria_adf(test_scenarios: str) -> list:
    """
    Converts the plain-text BDD scenario output from Agent 3 into a list of
    ADF nodes ready to be appended to the existing description document.
    """
    section_pattern = re.compile(r'^\s*#\s*(.+)$', re.MULTILINE)
    markers = list(section_pattern.finditer(test_scenarios))

    nodes: list = []
    nodes.append(adf_rule())
    nodes.append(adf_bold_paragraph("Acceptance Criteria:"))

    if not markers:
        nodes.append(adf_code_block(test_scenarios.strip()))
        return nodes

    preamble = test_scenarios[:markers[0].start()].strip()
    if preamble:
        nodes.append(adf_code_block(preamble))

    for i, marker in enumerate(markers):
        raw_heading   = marker.group(1).strip()
        clean_heading = re.sub(r'^\d+[\.\)]\s*', '', raw_heading)

        nodes.append(adf_bold_paragraph(clean_heading))

        start        = marker.end()
        end          = markers[i + 1].start() if i + 1 < len(markers) else len(test_scenarios)
        section_body = test_scenarios[start:end].strip()

        if section_body:
            nodes.append(adf_code_block(section_body))

    return nodes
