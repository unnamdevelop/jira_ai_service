"""
JIRA AI Analysis Service — FastAPI entry point.

Webhook routes only. All business logic lives in app/agents, app/jira, app/zephyr, app/services.

Endpoints:
    POST /webhook/jira-ai-trigger      — Story created (Automation Rule 1)
    POST /webhook/jira-zephyr-upload   — Label Approve-Zephyr-Upload added (Automation Rule 2)
    GET  /health
    GET  /
"""

import json

import uvicorn
from fastapi import FastAPI, Request

from app.config import settings
from app.graph.orchestrator import orchestrator
from app.jira.client import (
    add_jira_label,
    append_acceptance_criteria_to_description,
    clear_bdd_from_description,
    find_and_update_ai_comment,
    get_story_reporter_email,
    get_team_comments,
    post_comment,
    remove_label,
)
from app.services.email_service import send_analysis_email, send_zephyr_upload_email
from app.services.report_builder import build_report_file
from app.state import AgentState
from app.zephyr.client import (
    create_or_find_zephyr_folder,
    parse_scenarios_from_description,
    post_zephyr_upload_comment,
    update_label_uploaded_to_zephyr,
    upload_scenarios_to_zephyr,
)

app = FastAPI(title="JIRA AI Analysis Service", version="3.0.0")


# ── Webhook: AI Pipeline ──────────────────────────────────────────────────────

@app.post("/webhook/jira-ai-trigger")
async def receive_jira_webhook(request: Request):
    """
    Triggers the AI agent orchestration pipeline when a Jira story is created
    or when the 'Request-ReAnalysis' label is added.
    """
    payload = await request.json()

    try:
        print("\n🔍 RAW PAYLOAD KEYS:", list(payload.keys()))
        print("🔍 PAYLOAD PREVIEW :", json.dumps(payload, indent=2)[:600])

        # Normalise: support both Jira payload formats
        if "key" in payload:
            issue  = payload
            fields = payload.get("fields", {})
        else:
            issue  = payload.get("issue", {})
            fields = issue.get("fields", {})

        key            = issue.get("key", "Unknown-Key")
        summary        = fields.get("summary", "No Summary")
        description    = str(fields.get("description", "No Description"))
        current_labels = [
            l if isinstance(l, str) else l.get("name", "")
            for l in fields.get("labels", [])
        ]
        is_reanalysis = "Request-ReAnalysis" in current_labels
        if is_reanalysis:
            print(f"🔄 RE-ANALYSIS requested for {key}")

        print("\n" + "="*80)
        print(f"📥 WEBHOOK RECEIVED: {key}")
        print("="*80)

        # Remove Request-ReAnalysis label immediately to prevent re-trigger loops
        if is_reanalysis:
            remove_label(key, "Request-ReAnalysis")

        team_comments = get_team_comments(key)

        initial_state: AgentState = {
            "story_key":           key,
            "summary":             summary,
            "description":         description,
            "team_comments":       team_comments,
            "invest_report":       "",
            "invest_score":        0,
            "quality_gate_passed": False,
            "test_scenarios":      "",
            "gap_analysis":        "",
            "messages":            [f"🚀 Started pipeline for {key}"],
            "retry_count":         0,
            "error":               "",
        }

        config      = {"configurable": {"thread_id": key}}
        final_state = orchestrator.invoke(initial_state, config)

        report_filename = build_report_file(
            key=key,
            invest_score=final_state["invest_score"],
            quality_gate_passed=final_state["quality_gate_passed"],
            invest_report=final_state["invest_report"],
            test_scenarios=final_state["test_scenarios"],
            gap_analysis=final_state["gap_analysis"],
        )

        scenarios_ok = (
            final_state["quality_gate_passed"]
            and final_state.get("test_scenarios")
            and not final_state.get("error")
            and not final_state["test_scenarios"].startswith("Test generation failed")
        )

        description_updated = False

        if scenarios_ok:
            if is_reanalysis:
                clear_bdd_from_description(key)
            description_updated = append_acceptance_criteria_to_description(
                issue_key=key, test_scenarios=final_state["test_scenarios"]
            )
            jira_comment = (
                f"╔════════════════════════════════════════════════════════════════╗\n"
                f"║  AI DoR ASSESSMENT REPORT - {key}{'  🔄 RE-ANALYSIS' if is_reanalysis else ''}\n"
                f"║  Status: ✅ QUALITY GATE PASSED (Score: {final_state['invest_score']}/25)\n"
                f"{'║  💬 Team comments included in analysis' + chr(10) if team_comments else ''}"
                f"╚════════════════════════════════════════════════════════════════╝\n\n"
                f"{final_state['invest_report']}\n\n"
                f"{'='*70}\n"
                f"✅ Acceptance Criteria (BDD) have been added to the Description field.\n"
                f"⏳ Next: Review scenarios, then add label 'Approve-Zephyr-Upload' to upload to Zephyr.\n"
            )
        elif final_state["quality_gate_passed"]:
            print(f"⚠️  Quality gate passed but test generation failed for {key}")
            jira_comment = (
                f"╔════════════════════════════════════════════════════════════════╗\n"
                f"║  AI DoR ASSESSMENT REPORT - {key}{'  🔄 RE-ANALYSIS' if is_reanalysis else ''}\n"
                f"║  Status: ✅ QUALITY GATE PASSED (Score: {final_state['invest_score']}/25)\n"
                f"║  ⚠️  BDD scenario generation encountered an error\n"
                f"╚════════════════════════════════════════════════════════════════╝\n\n"
                f"{final_state['invest_report']}\n\n"
                f"{'='*70}\n"
                f"⚠️  Scenario generation failed: {final_state.get('test_scenarios', 'Unknown error')}\n"
                f"Please add label 'Request-ReAnalysis' to retry.\n"
            )
        else:
            jira_comment = (
                f"╔════════════════════════════════════════════════════════════════╗\n"
                f"║  AI ANALYSIS REPORT - {key}{'  🔄 RE-ANALYSIS' if is_reanalysis else ''}\n"
                f"║  Status: ❌ QUALITY GATE FAILED (Score: {final_state['invest_score']}/25)\n"
                f"{'║  💬 Team comments included in analysis' + chr(10) if team_comments else ''}"
                f"╚════════════════════════════════════════════════════════════════╝\n\n"
                f"{final_state['invest_report']}\n\n"
                f"{'='*70}\n"
                f"{final_state['gap_analysis']}\n"
            )

        label      = "AI-Ready" if scenarios_ok else "AI-NeedsRefinement"
        jira_posted = (
            find_and_update_ai_comment(key, jira_comment)
            if is_reanalysis
            else post_comment(key, jira_comment)
        )
        label_added = add_jira_label(key, label)

        reporter_email = get_story_reporter_email(key)
        email_sent     = False
        if reporter_email:
            email_sent = send_analysis_email(
                issue_key=key,
                reporter_email=reporter_email,
                quality_passed=final_state["quality_gate_passed"],
                score=final_state["invest_score"],
                report_file=report_filename,
            )

        return {
            "status":              "success",
            "story_key":           key,
            "quality_gate_passed": final_state["quality_gate_passed"],
            "invest_score":        f"{final_state['invest_score']}/25",
            "jira_comment_posted": jira_posted,
            "description_updated": description_updated,
            "label":               label,
            "email_sent":          email_sent,
            "next_action": (
                "Review BDD scenarios in Description, then add label 'Approve-Zephyr-Upload'"
                if final_state["quality_gate_passed"]
                else "Story needs refinement — see Gap Analysis comment"
            ),
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


# ── Webhook: Zephyr Upload ────────────────────────────────────────────────────

@app.post("/webhook/jira-zephyr-upload")
async def upload_to_zephyr(request: Request):
    """
    Triggered by a JIRA automation rule when the label 'Approve-Zephyr-Upload' is added.
    Parses Gherkin from the story description and uploads to Zephyr Scale.
    """
    payload = await request.json()

    try:
        print("\n🔍 RAW PAYLOAD KEYS:", list(payload.keys()))
        print("🔍 PAYLOAD PREVIEW :", json.dumps(payload, indent=2)[:600])

        if "key" in payload:
            issue = payload
        else:
            issue = payload.get("issue", {})

        issue_key    = issue.get("key", "")
        project_key  = issue_key.rsplit("-", 1)[0] if "-" in issue_key else settings.JIRA_PROJECT_KEY

        print("\n" + "="*70)
        print(f"📤 ZEPHYR UPLOAD TRIGGERED: {issue_key}")
        print(f"   Project: {project_key}")
        print("="*70)

        scenarios, feature_name = parse_scenarios_from_description(issue_key)
        if not scenarios:
            msg = (
                f"No Gherkin scenarios found in the Description of {issue_key}. "
                "Ensure the AI pipeline ran successfully before adding the 'Approve-Zephyr-Upload' label."
            )
            print(f"⚠️  {msg}")
            return {"status": "error", "issue_key": issue_key, "message": msg}

        print(f"✅ Found {len(scenarios)} scenario(s) to upload")

        folder_name = issue_key
        folder_id   = create_or_find_zephyr_folder(project_key, folder_name)
        if not folder_id:
            return {"status": "error", "issue_key": issue_key, "message": "Failed to create or find Zephyr folder."}

        success, fail, uploaded_tcs = upload_scenarios_to_zephyr(
            project_key=project_key,
            issue_key=issue_key,
            folder_name=folder_name,
            scenarios=scenarios,
        )
        print(f"\n📊 Upload result: {success} created, {fail} failed")

        comment_posted = post_zephyr_upload_comment(
            issue_key=issue_key,
            success=success,
            fail=fail,
            folder_name=folder_name,
            project_key=project_key,
        )

        label_updated = False
        if success > 0:
            label_updated = update_label_uploaded_to_zephyr(issue_key)

        email_sent = False
        if success > 0:
            reporter_email = get_story_reporter_email(issue_key)
            if reporter_email:
                email_sent = send_zephyr_upload_email(
                    issue_key=issue_key,
                    reporter_email=reporter_email,
                    folder_name=folder_name,
                    uploaded_tcs=uploaded_tcs,
                    project_key=project_key,
                )

        print("\n" + "="*70)
        print(f"🎯 ZEPHYR UPLOAD COMPLETE — {issue_key}")
        print(f"   Scenarios parsed  : {len(scenarios)}")
        print(f"   Test cases created: {success}")
        print(f"   Test cases failed : {fail}")
        print(f"   Comment posted    : {comment_posted}")
        print(f"   Label updated     : {'UploadedToZephyr' if label_updated else 'NOT updated'}")
        print(f"   Email sent        : {'✅' if email_sent else '❌'}")
        print("="*70)

        return {
            "status":             "success" if success > 0 else "partial_failure",
            "issue_key":          issue_key,
            "project_key":        project_key,
            "folder_name":        folder_name,
            "folder_id":          folder_id,
            "scenarios_parsed":   len(scenarios),
            "test_cases_created": success,
            "test_cases_failed":  fail,
            "comment_posted":     comment_posted,
            "label_updated":      label_updated,
            "label":              "UploadedToZephyr" if label_updated else "not updated",
            "email_sent":         email_sent,
            "next_action":        "Assign test cases to a Test Cycle in Zephyr and begin execution",
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}


# ── Health & Root ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    missing = settings.validate()
    return {
        "status":    "healthy" if not missing else "degraded",
        "service":   "JIRA AI Analysis Service",
        "version":   "3.0.0",
        "missing_config": missing,
    }


@app.get("/")
async def root():
    return {
        "service":  "JIRA AI Analysis Service",
        "version":  "3.0.0",
        "endpoints": {
            "ai_webhook":     "/webhook/jira-ai-trigger",
            "zephyr_webhook": "/webhook/jira-zephyr-upload",
            "health":         "/health",
        },
        "pipeline": {
            "phase_1":  "DoR Assessment (GPT-4o)",
            "phase_2":  f"Quality Gate Decision (threshold {settings.QG_THRESHOLD}/25)",
            "phase_3a": "PASS → Append ADF Acceptance Criteria to Description",
            "phase_3b": "FAIL → Post DoR report + Gap Analysis as comment",
            "phase_4":  "BA reviews scenarios → adds label 'Approve-Zephyr-Upload'",
            "phase_5":  "Zephyr upload → folder created, test cases uploaded, label → UploadedToZephyr",
        },
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*80)
    print("🚀 Starting JIRA AI Analysis Service v3.0.0")
    print("="*80)
    print("📍 Server         : http://0.0.0.0:8000")
    print("🔗 AI Webhook     : /webhook/jira-ai-trigger")
    print("🧪 Zephyr Webhook : /webhook/jira-zephyr-upload")
    print("💚 Health         : /health")
    print("─"*80)
    missing = settings.validate()
    if missing:
        print(f"⚠️  Missing env vars: {', '.join(missing)}")
    else:
        print("✅ All required env vars are set")
    print("="*80 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
