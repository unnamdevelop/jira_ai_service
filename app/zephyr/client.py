"""
Zephyr Scale Cloud REST API v2 client.
API base: https://api.zephyrscale.smartbear.com/v2
Auth:     Bearer token (generated from Jira Avatar → Zephyr Scale API Access Tokens)
"""

import re

import requests

from app.config import settings
from app.jira.client import extract_description_text, post_comment


# ── Internal helpers ──────────────────────────────────────────────────────────

def _headers() -> dict:
    token = settings.ZEPHYR_API_TOKEN
    if not token:
        print("⚠️  ZEPHYR_API_TOKEN not set — Zephyr API calls will fail with 401")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


def _base() -> str:
    return settings.ZEPHYR_BASE_URL


# ── Folder management ─────────────────────────────────────────────────────────

def _delete_test_cases_in_folder(folder_id: str, project_key: str) -> int:
    """Delete all test cases inside a Zephyr folder. Returns count deleted."""
    deleted = 0
    resp    = requests.get(
        f"{_base()}/testcases?projectKey={project_key}&folderId={folder_id}&maxResults=200",
        headers=_headers(),
    )
    if resp.status_code != 200:
        print(f"  ⚠️  Could not list test cases in folder ({resp.status_code})")
        return 0

    test_cases = resp.json().get("values", [])
    print(f"  🗑️  Deleting {len(test_cases)} existing test case(s) in folder...")

    for tc in test_cases:
        tc_key   = tc.get("key", "")
        del_resp = requests.delete(f"{_base()}/testcases/{tc_key}", headers=_headers())
        if del_resp.status_code in (200, 204):
            print(f"    ✅ Deleted {tc_key}")
            deleted += 1
        else:
            print(f"    ⚠️  Could not delete {tc_key}: {del_resp.status_code}")

    return deleted


def create_or_find_zephyr_folder(
    project_key: str,
    folder_name: str,
    delete_existing: bool = False,
) -> str | None:
    """
    Creates or finds a Zephyr Scale test-case folder named after the story key.
    If delete_existing=True, clears all test cases in the folder before returning it.
    """
    list_resp = requests.get(
        f"{_base()}/folders?projectKey={project_key}&folderType=TEST_CASE&maxResults=500",
        headers=_headers(),
    )

    if list_resp.status_code == 200:
        for folder in list_resp.json().get("values", []):
            if folder.get("name") == folder_name:
                fid = str(folder.get("id", ""))
                if delete_existing:
                    print(f"📁 Folder '{folder_name}' exists (id={fid}) — clearing old test cases...")
                    deleted = _delete_test_cases_in_folder(fid, project_key)
                    print(f"  ✅ Cleared {deleted} old test case(s)")
                else:
                    print(f"📁 Zephyr folder already exists: '{folder_name}' (id={fid})")
                return fid
    else:
        print(f"⚠️  Could not list folders ({list_resp.status_code}): {list_resp.text[:200]}")

    # Create new folder
    create_resp = requests.post(
        f"{_base()}/folders",
        headers=_headers(),
        json={"name": folder_name, "folderType": "TEST_CASE", "projectKey": project_key},
    )

    if create_resp.status_code in (200, 201):
        folder_id = str(create_resp.json().get("id", ""))
        print(f"📁 Zephyr folder created: '{folder_name}' (id={folder_id})")
        return folder_id

    print(f"❌ Folder creation failed: {create_resp.status_code} — {create_resp.text[:300]}")
    return None


# ── Scenario parsing ──────────────────────────────────────────────────────────

def parse_scenarios_from_description(issue_key: str) -> tuple[list[dict], str]:
    """
    Parses every Gherkin Scenario / Scenario Outline as a separate test case.
    Background steps are prepended to every scenario (self-contained TCs).
    Returns (scenarios, feature_name).
    """
    raw_text = extract_description_text(issue_key)
    if not raw_text:
        print(f"Empty description for {issue_key}")
        return [], ""

    feature_match = re.search(r'Feature:\s*(.+)', raw_text, re.IGNORECASE)
    feature_name  = feature_match.group(1).strip() if feature_match else ""
    if feature_name:
        print(f"Feature: '{feature_name}'")

    background_steps: list[str] = []
    bg_match = re.search(
        r'(?:^|\n)\s*Background:\s*\n(.*?)(?=\n\s*(?:Scenario(?:\s+Outline)?:|Feature:|$))',
        raw_text, re.DOTALL | re.IGNORECASE,
    )
    if bg_match:
        bg_block = bg_match.group(1)
        background_steps = [
            line.strip() for line in bg_block.splitlines()
            if re.match(r'^\s*(Given|When|Then|And|But)\s', line, re.IGNORECASE)
        ]
        if background_steps:
            print(f"Background: {len(background_steps)} step(s) will be prepended to each scenario")

    parts = re.split(r'(?=\n\s*Scenario(?:\s+Outline)?:\s)', raw_text)

    scenarios = []
    for part in parts:
        part = part.strip()
        if not re.match(r'Scenario(?:\s+Outline)?:', part, re.IGNORECASE):
            continue

        first_line = part.splitlines()[0]
        name = re.sub(r'^Scenario(?:\s+Outline)?:\s*', '', first_line,
                      flags=re.IGNORECASE).strip() or "Unnamed Scenario"

        scenario_steps = [
            line.strip() for line in part.splitlines()[1:]
            if re.match(r'^\s*(Given|When|Then|And|But)\s', line, re.IGNORECASE)
        ]

        all_steps = background_steps + scenario_steps
        if not all_steps:
            print(f"  No steps found for '{name}' — skipping")
            continue

        scenarios.append({"name": name, "steps": all_steps})
        print(f"  Parsed: '{name}' — {len(all_steps)} total steps")

    print(f"\n{len(scenarios)} scenario(s) ready from {issue_key}")
    return scenarios, feature_name


# ── Upload ────────────────────────────────────────────────────────────────────

def _build_zephyr_steps(step_lines: list[str]) -> list[dict]:
    """Converts Gherkin step strings into Zephyr Scale Cloud API v2 step objects."""
    zephyr_steps = []
    after_then   = False

    for line in step_lines:
        keyword = line.split()[0].lower() if line.split() else ""
        if keyword == "then":
            after_then = True
        elif keyword in ("given", "when"):
            after_then = False

        zephyr_steps.append({
            "inline": {
                "description":    line,
                "testData":       "",
                "expectedResult": line if after_then else "",
            }
        })
    return zephyr_steps


def upload_scenarios_to_zephyr(
    project_key: str,
    issue_key: str,
    folder_name: str,
    scenarios: list[dict],
) -> tuple[int, int, list[dict]]:
    """
    Creates one Zephyr Scale test case per BDD scenario.
    Returns (success_count, fail_count, uploaded_test_cases).
    """
    success, fail = 0, 0
    uploaded_tcs: list[dict] = []

    folder_id = create_or_find_zephyr_folder(project_key, folder_name, delete_existing=True)
    if not folder_id:
        print("⚠️  No folder id — aborting upload")
        return 0, len(scenarios), []

    for idx, scenario in enumerate(scenarios, start=1):
        name  = scenario["name"]
        steps = scenario["steps"]

        print(f"\n  📝 [{idx}/{len(scenarios)}] {name}  ({len(steps)} steps)")

        create_payload = {
            "projectKey": project_key,
            "name":       f"[{issue_key}] {name}",
            "statusName": "Draft",
            "labels":     [issue_key],
            "issueLinks": [issue_key],
            "folderId":   int(folder_id),
            "testScript": {"type": "bdd"},
        }

        resp = requests.post(f"{_base()}/testcases", headers=_headers(), json=create_payload)

        if resp.status_code not in (200, 201):
            print(f"  ❌ Create failed '{name}': {resp.status_code} — {resp.text[:500]}")
            fail += 1
            continue

        tc_key = resp.json().get("key", "?")
        print(f"  ✅ Created {tc_key}: [{issue_key}] {name}")

        # GET full TC to retrieve fields required by PUT
        get_resp = requests.get(f"{_base()}/testcases/{tc_key}", headers=_headers())
        if get_resp.status_code != 200:
            print(f"  ⚠️  Could not GET {tc_key}: {get_resp.status_code}")
            success += 1
            continue

        tc_data = get_resp.json()
        put_body = {
            "id":         tc_data.get("id"),
            "key":        tc_key,
            "name":       f"[{issue_key}] {name}",
            "project":    tc_data.get("project", {}),
            "status":     tc_data.get("status", {}),
            "priority":   tc_data.get("priority", {}),
            "folder":     tc_data.get("folder") or {"id": int(folder_id)},
            "labels":     [issue_key],
            "issueLinks": [issue_key],
            "testScript": {"type": "bdd"},
        }
        requests.put(f"{_base()}/testcases/{tc_key}", headers=_headers(), json=put_body)

        # POST BDD Gherkin script
        script_resp = requests.post(
            f"{_base()}/testcases/{tc_key}/testscript",
            headers=_headers(),
            json={"type": "bdd", "text": "\n".join(steps)},
        )
        if script_resp.status_code in (200, 201, 204):
            print(f"  ✅ BDD script uploaded to {tc_key}")
        else:
            print(f"  ⚠️  BDD script failed {tc_key}: {script_resp.status_code} — {script_resp.text[:200]}")

        jira_base = settings.JIRA_URL
        tc_url    = (
            f"{jira_base}/projects/{project_key}"
            f"?selectedItem=com.atlassian.plugins.atlassian-connect-plugin:"
            f"com.kanoah.test-manager__main-project-page"
            f"#!/v2/testCase/{tc_key}"
        )
        uploaded_tcs.append({"name": name, "tc_key": tc_key, "tc_url": tc_url})
        success += 1

    return success, fail, uploaded_tcs


# ── Post-upload Jira updates ──────────────────────────────────────────────────

def update_label_uploaded_to_zephyr(issue_key: str) -> bool:
    """Swap workflow labels: remove Approve-Zephyr-Upload / AI-Ready → add UploadedToZephyr."""
    import json as _json

    url  = f"{settings.JIRA_URL}/rest/api/3/issue/{issue_key}?fields=labels"
    resp = requests.get(url, headers={"Accept": "application/json"},
                        auth=__import__("requests").auth.HTTPBasicAuth(
                            settings.JIRA_USER, settings.JIRA_API_TOKEN))

    if resp.status_code != 200:
        print(f"⚠️  Could not fetch labels ({resp.status_code})")
        return False

    raw_labels     = resp.json().get("fields", {}).get("labels", [])
    current_labels = [l["name"] if isinstance(l, dict) else l for l in raw_labels]
    remove         = {"Approve-Zephyr-Upload", "AI-Ready", "AI_Analysis_Passed"}
    updated        = [l for l in current_labels if l not in remove] + ["UploadedToZephyr"]

    from requests.auth import HTTPBasicAuth
    patch_resp = requests.put(
        f"{settings.JIRA_URL}/rest/api/3/issue/{issue_key}",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        auth=HTTPBasicAuth(settings.JIRA_USER, settings.JIRA_API_TOKEN),
        data=_json.dumps({"fields": {"labels": updated}}),
    )

    if patch_resp.status_code == 204:
        print(f"🏷️  Label updated → 'UploadedToZephyr' on {issue_key}")
        return True

    print(f"⚠️  Label update failed: {patch_resp.status_code}")
    return False


def post_zephyr_upload_comment(
    issue_key: str,
    success: int,
    fail: int,
    folder_name: str,
    project_key: str,
) -> bool:
    """Post a summary comment to the Jira issue after Zephyr upload completes."""
    jira_base  = settings.JIRA_URL
    zephyr_url = (
        f"{jira_base}/jira/software/projects/{project_key}/boards"
        f"?selectedItem=com.atlassian.plugins.atlassian-connect-plugin:"
        f"com.kanoah.test-manager__main-project-page#!/v2/testCases"
        f"?projectId={project_key}"
    )

    comment = (
        f"╔════════════════════════════════════════════════════════════════╗\n"
        f"║  🧪 ZEPHYR SCALE UPLOAD COMPLETE — {issue_key}\n"
        f"╚════════════════════════════════════════════════════════════════╝\n\n"
        f"✅ Test cases uploaded successfully to Zephyr Scale.\n\n"
        f"📁 Folder       : /{folder_name}\n"
        f"✅ Created       : {success} test case(s)\n"
        f"{'❌ Failed        : ' + str(fail) + ' test case(s)' + chr(10) if fail > 0 else ''}"
        f"🏷️  Label updated : UploadedToZephyr\n\n"
        f"🔗 View in Zephyr: {zephyr_url}\n\n"
        f"{'='*70}\n"
        f"Next step: Assign test cases to a Test Cycle and begin execution."
    )
    return post_comment(issue_key, comment)
