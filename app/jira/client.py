"""
Jira REST API client.
All Jira interactions live here — no Jira calls in agents or route handlers.
"""

import json
import re

import requests
from jira import JIRA
from requests.auth import HTTPBasicAuth

from app.config import settings
from app.jira.adf_helpers import (
    adf_paragraph,
    build_acceptance_criteria_adf,
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(settings.JIRA_USER, settings.JIRA_API_TOKEN)


def _headers() -> dict:
    return {"Accept": "application/json", "Content-Type": "application/json"}


def _jira_sdk() -> JIRA:
    return JIRA(
        server=settings.JIRA_URL,
        basic_auth=(settings.JIRA_USER, settings.JIRA_API_TOKEN),
    )


# ── Description ───────────────────────────────────────────────────────────────

def get_existing_description_adf(issue_key: str) -> dict:
    """
    Fetches the current issue description as a native ADF document via REST API v3.
    Returns an empty ADF doc if the description is missing or the request fails.
    """
    url      = f"{settings.JIRA_URL}/rest/api/3/issue/{issue_key}?fields=description"
    response = requests.get(url, headers={"Accept": "application/json"}, auth=_auth())

    if response.status_code != 200:
        print(f"⚠️  Could not fetch description (status {response.status_code}).")
        return {"version": 1, "type": "doc", "content": []}

    description = response.json().get("fields", {}).get("description")

    if description is None:
        return {"version": 1, "type": "doc", "content": []}

    if isinstance(description, dict):
        return description

    lines   = str(description).splitlines()
    content = [adf_paragraph(line) if line.strip() else adf_paragraph(" ") for line in lines]
    return {"version": 1, "type": "doc", "content": content}


def append_acceptance_criteria_to_description(issue_key: str, test_scenarios: str) -> bool:
    """Appends BDD test scenarios to the Jira issue Description field using ADF."""
    try:
        print(f"📥 Fetching existing description for {issue_key}...")
        existing_adf = get_existing_description_adf(issue_key)

        ac_nodes         = build_acceptance_criteria_adf(test_scenarios)
        existing_content = existing_adf.get("content", [])

        updated_adf = {
            "version": 1,
            "type":    "doc",
            "content": existing_content + ac_nodes,
        }

        url      = f"{settings.JIRA_URL}/rest/api/3/issue/{issue_key}"
        payload  = {"fields": {"description": updated_adf}}
        response = requests.put(url, headers=_headers(), auth=_auth(), data=json.dumps(payload))

        if response.status_code == 204:
            print(f"✅ Acceptance Criteria (ADF) appended to Description of {issue_key}")
            return True

        print(f"❌ Failed to update description. Status: {response.status_code}")
        return False

    except Exception as e:
        print(f"❌ Failed to update description: {e}")
        return False


def clear_bdd_from_description(issue_key: str) -> bool:
    """
    Remove only the AI-generated BDD block previously appended to the description.
    Everything before the AI horizontal-rule marker is preserved.
    """
    try:
        resp = requests.get(
            f"{settings.JIRA_URL}/rest/api/3/issue/{issue_key}?fields=description",
            auth=_auth(),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            print(f"⚠️  Could not fetch description for BDD clear: {resp.status_code}")
            return False

        adf = resp.json().get("fields", {}).get("description")
        if not adf or not adf.get("content"):
            return True

        nodes     = adf["content"]
        cut_index = None
        for i, node in enumerate(nodes):
            if node.get("type") == "rule" and i + 1 < len(nodes):
                if "Acceptance Criteria" in json.dumps(nodes[i + 1]):
                    cut_index = i
                    break

        if cut_index is None:
            print(f"ℹ️  No AI BDD block found in description of {issue_key} — nothing to clear")
            return True

        preserved = nodes[:cut_index] or [
            {"type": "paragraph", "content": [{"type": "text", "text": " "}]}
        ]
        new_adf = {"type": "doc", "version": 1, "content": preserved}

        put_resp = requests.put(
            f"{settings.JIRA_URL}/rest/api/3/issue/{issue_key}",
            auth=_auth(),
            headers=_headers(),
            json={"fields": {"description": new_adf}},
        )
        if put_resp.status_code in (200, 204):
            print(f"✅ AI BDD block cleared from description of {issue_key}")
            return True

        print(f"⚠️  Failed to clear description: {put_resp.status_code}")
        return False

    except Exception as e:
        print(f"❌ Error clearing BDD from description: {e}")
        return False


def extract_description_text(issue_key: str) -> str:
    """
    Fetches the Jira issue description via REST API v3 and reconstructs
    properly newline-separated plain text from the ADF JSON structure.
    """
    url  = f"{settings.JIRA_URL}/rest/api/3/issue/{issue_key}?fields=description"
    resp = requests.get(url, headers={"Accept": "application/json"}, auth=_auth())

    if resp.status_code != 200:
        print(f"Cannot fetch description ({resp.status_code})")
        return ""

    BLOCK_NODES = {
        "paragraph", "codeBlock", "blockquote", "bulletList",
        "orderedList", "listItem", "rule", "heading",
        "tableRow", "tableCell", "tableHeader",
    }

    def _walk(node) -> str:
        if not isinstance(node, dict):
            return ""
        node_type = node.get("type", "")
        if node_type == "text":
            return node.get("text", "")
        if node_type == "hardBreak":
            return "\n"
        children_text = "".join(_walk(c) for c in node.get("content", []))
        if node_type in BLOCK_NODES:
            return children_text + "\n"
        return children_text

    description = resp.json().get("fields", {}).get("description") or {}
    raw = _walk(description)
    raw = re.sub(r'\n{3,}', '\n\n', raw)

    print(f"\n--- DESCRIPTION TEXT ({len(raw)} chars) ---\n{raw[:1000]}\n---")
    return raw


# ── Labels ────────────────────────────────────────────────────────────────────

_AI_LABELS = {
    "AI-Ready", "AI-NeedsRefinement",
    "AI_Analysis_Passed", "AI_Analysis_Failed",
    "Request-ReAnalysis",
}


def add_jira_label(issue_key: str, label: str) -> bool:
    """Replace all AI workflow labels on the issue with the given label."""
    try:
        jira  = _jira_sdk()
        issue = jira.issue(issue_key)

        cleaned = [l for l in issue.fields.labels if l not in _AI_LABELS]
        issue.update(fields={"labels": cleaned + [label]})

        print(f"🏷️  Label '{label}' added to {issue_key}")
        return True

    except Exception as e:
        print(f"⚠️  Failed to add label: {e}")
        return False


def remove_label(issue_key: str, label: str) -> bool:
    """Remove a specific label from a Jira issue."""
    try:
        jira  = _jira_sdk()
        issue = jira.issue(issue_key)
        cleaned = [l for l in issue.fields.labels if l != label]
        issue.update(fields={"labels": cleaned})
        print(f"🏷️  Label '{label}' removed from {issue_key}")
        return True
    except Exception as e:
        print(f"⚠️  Could not remove label '{label}': {e}")
        return False


# ── Comments ──────────────────────────────────────────────────────────────────

_AI_COMMENT_SIGNATURES = (
    "AI DoR ASSESSMENT REPORT",
    "AI ANALYSIS REPORT",
    "DRAFT BDD SCENARIOS",
)


def get_team_comments(issue_key: str) -> str:
    """
    Fetch all human-authored comments from the Jira issue.
    Filters out AI-generated comments. Returns formatted string or empty string.
    """
    try:
        jira     = _jira_sdk()
        comments = jira.comments(issue_key)

        team_comments = []
        for c in comments:
            body = c.body or ""
            if any(sig in body for sig in _AI_COMMENT_SIGNATURES):
                continue
            author = getattr(c, "author", None)
            name   = getattr(author, "displayName", "Team Member") if author else "Team Member"
            body_trimmed = body.strip()[:1500]
            if len(body.strip()) > 1500:
                body_trimmed += "\n[... truncated ...]"
            team_comments.append(f"[{name}]: {body_trimmed}")

        if not team_comments:
            return ""

        formatted = "\n\n".join(
            f"Comment {i+1} — {tc}" for i, tc in enumerate(team_comments)
        )
        print(f"💬 [{issue_key}] {len(team_comments)} team comment(s) found")
        return formatted

    except Exception as e:
        print(f"⚠️  Could not fetch comments for {issue_key}: {e}")
        return ""


def post_comment(issue_key: str, comment_text: str) -> bool:
    """Post plain-text report as a Jira comment."""
    try:
        jira = _jira_sdk()
        jira.add_comment(issue_key, comment_text)
        print(f"✅ Comment posted to {issue_key}")
        return True
    except Exception as e:
        print(f"❌ Failed to post comment: {e}")
        return False


def find_and_update_ai_comment(issue_key: str, new_text: str) -> bool:
    """
    Update the most recent AI DoR Assessment comment in place.
    Falls back to posting a new comment if none exists.
    """
    try:
        jira       = _jira_sdk()
        comments   = jira.comments(issue_key)
        ai_comment = None
        for c in reversed(comments):
            if any(sig in (c.body or "") for sig in _AI_COMMENT_SIGNATURES):
                ai_comment = c
                break

        if ai_comment:
            ai_comment.update(body=new_text)
            print(f"✅ AI comment updated in place on {issue_key} (id={ai_comment.id})")
            return True

        print(f"⚠️  No prior AI comment found on {issue_key} — posting as new")
        return post_comment(issue_key, new_text)

    except Exception as e:
        print(f"❌ Failed to update AI comment: {e}")
        return False


# ── Reporter ──────────────────────────────────────────────────────────────────

def get_story_reporter_email(issue_key: str) -> str | None:
    """Return the email address of the story creator."""
    try:
        jira  = _jira_sdk()
        issue = jira.issue(issue_key)
        email = issue.fields.reporter.emailAddress
        print(f"📧 Story reporter: {email}")
        return email
    except Exception as e:
        print(f"⚠️  Could not get reporter email: {e}")
        return None
