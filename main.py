"""
JIRA AI Analysis Service — FastAPI entry point.

Webhook routes only. All business logic lives in app/agents, app/jira, app/zephyr, app/services.

Endpoints:
    POST /webhook/jira-ai-trigger      — Story created (Automation Rule 1)
    POST /webhook/jira-zephyr-upload   — Label Approve-Zephyr-Upload added (Automation Rule 2)
    GET  /health
    GET  /
    GET  /dashboard                    — Team dashboard
    GET  /api/stats                    — Statistics API
    GET  /api/stories                  — Story history API
    GET  /api/logs                     — Live logs API
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

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
import logging

# Write logs to file so dashboard can read them
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(),           # console
        logging.FileHandler("app.log"),    # file
    ]
)

app = FastAPI(title="JIRA AI Analysis Service", version="3.0.0")

# ── Story history file ────────────────────────────────────────────────────────

STORIES_FILE = Path("stories.json")

def load_stories():
    if STORIES_FILE.exists():
        with open(STORIES_FILE, "r") as f:
            return json.load(f)
    return []

def save_story(entry: dict):
    stories = load_stories()
    # update if exists, else prepend
    for i, s in enumerate(stories):
        if s["key"] == entry["key"]:
            stories[i] = entry
            with open(STORIES_FILE, "w") as f:
                json.dump(stories, f, indent=2)
            return
    stories.insert(0, entry)
    with open(STORIES_FILE, "w") as f:
        json.dump(stories, f, indent=2)


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

        # ── Save to dashboard history ──
        save_story({
            "key":          key,
            "summary":      summary,
            "status":       "PASS" if scenarios_ok else "FAIL",
            "score":        final_state["invest_score"],
            "label":        label,
            "email":        reporter_email or "",
            "reanalysis":   is_reanalysis,
            "email_sent":   email_sent,
            "zephyr":       False,
            "analysed_at":  datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        })

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

        # ── Update dashboard history with Zephyr status ──
        stories = load_stories()
        for s in stories:
            if s["key"] == issue_key:
                s["zephyr"]        = success > 0
                s["zephyr_count"]  = success
                s["zephyr_at"]     = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                break
        with open(STORIES_FILE, "w") as f:
            json.dump(stories, f, indent=2)

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


# ── Dashboard API ─────────────────────────────────────────────────────────────

@app.get("/api/stories")
async def api_stories():
    return load_stories()


@app.get("/api/stats")
async def api_stats():
    stories = load_stories()
    total   = len(stories)
    passed  = sum(1 for s in stories if s.get("status") == "PASS")
    failed  = total - passed
    zephyr  = sum(1 for s in stories if s.get("zephyr"))
    rate    = round((passed / total) * 100) if total > 0 else 0
    return {
        "total":       total,
        "passed":      passed,
        "failed":      failed,
        "zephyr":      zephyr,
        "pass_rate":   rate,
    }


@app.get("/api/logs")
async def api_logs():
    try:
        log_file = Path("app.log")
        if log_file.exists():
            with open(log_file, "r") as f:
                lines = f.readlines()
            return {"logs": [l.rstrip() for l in lines[-80:]]}
        return {"logs": ["No logs available yet."]}
    except Exception as e:
        return {"logs": [f"Error reading logs: {str(e)}"]}

# ── Dashboard Page ────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JIRA AI Analysis Service — Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #f0f4ff; color: #1e293b; }

  .header {
    background: linear-gradient(135deg, #1e3a8a, #2563eb);
    color: white; padding: 20px 32px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .header h1 { font-size: 22px; font-weight: 700; }
  .header p  { font-size: 13px; opacity: 0.8; margin-top: 2px; }
  .live-badge {
    background: rgba(255,255,255,0.2);
    border: 1px solid rgba(255,255,255,0.4);
    padding: 5px 14px; border-radius: 20px;
    font-size: 12px; font-weight: 600;
    display: flex; align-items: center; gap: 6px;
  }
  .pulse {
    width: 8px; height: 8px; background: #4ade80;
    border-radius: 50%; animation: pulse 1.5s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  .main { padding: 24px 32px; }

  /* Stats */
  .stats { display: grid; grid-template-columns: repeat(5,1fr); gap: 16px; margin-bottom: 24px; }
  .stat-card {
    background: white; border-radius: 14px;
    padding: 20px; text-align: center;
    border: 2px solid #e2e8f0;
    transition: transform 0.2s;
  }
  .stat-card:hover { transform: translateY(-2px); }
  .stat-num  { font-size: 36px; font-weight: 800; margin-bottom: 4px; }
  .stat-label{ font-size: 12px; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
  .blue  { color: #2563eb; }
  .green { color: #16a34a; }
  .red   { color: #dc2626; }
  .teal  { color: #0891b2; }
  .amber { color: #d97706; }

  /* Grid */
  .grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 20px; margin-bottom: 20px; }

  /* Cards */
  .card { background: white; border-radius: 14px; border: 2px solid #e2e8f0; overflow: hidden; }
  .card-header {
    padding: 14px 20px;
    border-bottom: 2px solid #f1f5f9;
    font-size: 14px; font-weight: 700; color: #1e293b;
    display: flex; align-items: center; justify-content: space-between;
  }
  .card-header span { font-size: 11px; color: #94a3b8; font-weight: 500; }

  /* Table */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th {
    padding: 10px 16px; text-align: left;
    font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.06em;
    color: #64748b; background: #f8fafc;
    border-bottom: 2px solid #e2e8f0;
  }
  td { padding: 12px 16px; font-size: 13px; border-bottom: 1px solid #f1f5f9; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #f8fafc; }

  .badge {
    display: inline-block; padding: 3px 10px;
    border-radius: 20px; font-size: 11px; font-weight: 700;
  }
  .badge-pass  { background: #dcfce7; color: #166534; }
  .badge-fail  { background: #fee2e2; color: #991b1b; }
  .badge-zeph  { background: #ccfbf1; color: #134e4a; }
  .badge-no    { background: #f1f5f9; color: #94a3b8; }

  .story-key {
    font-family: monospace; font-weight: 700;
    color: #2563eb; font-size: 13px;
  }

  /* Logs */
  .log-box {
    background: #0f172a; color: #e2e8f0;
    font-family: 'Courier New', monospace; font-size: 12px;
    padding: 16px; height: 280px; overflow-y: auto;
    line-height: 1.6;
  }
  .log-line { padding: 1px 0; }
  .log-line.info   { color: #93c5fd; }
  .log-line.pass   { color: #86efac; }
  .log-line.fail   { color: #fca5a5; }
  .log-line.warn   { color: #fcd34d; }
  .log-line.agent  { color: #c4b5fd; }
  .log-line.normal { color: #e2e8f0; }

  /* Empty state */
  .empty { padding: 40px; text-align: center; color: #94a3b8; font-size: 14px; }

  /* Refresh bar */
  .refresh-bar {
    background: #eff6ff; border: 1px solid #bfdbfe;
    border-radius: 8px; padding: 8px 16px; margin-bottom: 20px;
    font-size: 12px; color: #1d4ed8;
    display: flex; align-items: center; justify-content: space-between;
  }
  .refresh-btn {
    background: #2563eb; color: white; border: none;
    padding: 5px 14px; border-radius: 6px; cursor: pointer;
    font-size: 12px; font-weight: 600;
  }
  .refresh-btn:hover { background: #1d4ed8; }

  /* Score bar */
  .score-bar-wrap { display: flex; align-items: center; gap: 8px; }
  .score-bar { height: 6px; border-radius: 3px; background: #e2e8f0; flex: 1; }
  .score-fill { height: 100%; border-radius: 3px; }
  .score-text { font-size: 12px; font-weight: 700; min-width: 40px; }
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>🤖 JIRA AI Analysis Service</h1>
    <p>Real-time story analysis dashboard</p>
  </div>
  <div class="live-badge">
    <div class="pulse"></div>
    Live — auto refresh 10s
  </div>
</div>

<div class="main">

  <div class="refresh-bar">
    <span id="last-refresh">Loading...</span>
    <button class="refresh-btn" onclick="loadAll()">Refresh Now</button>
  </div>

  <!-- Stats -->
  <div class="stats">
    <div class="stat-card">
      <div class="stat-num blue"  id="stat-total">—</div>
      <div class="stat-label">Total Analysed</div>
    </div>
    <div class="stat-card">
      <div class="stat-num green" id="stat-pass">—</div>
      <div class="stat-label">Passed</div>
    </div>
    <div class="stat-card">
      <div class="stat-num red"   id="stat-fail">—</div>
      <div class="stat-label">Failed</div>
    </div>
    <div class="stat-card">
      <div class="stat-num teal"  id="stat-zeph">—</div>
      <div class="stat-label">Zephyr Uploaded</div>
    </div>
    <div class="stat-card">
      <div class="stat-num amber" id="stat-rate">—</div>
      <div class="stat-label">Pass Rate %</div>
    </div>
  </div>

  <!-- Stories + Logs -->
  <div class="grid">

    <!-- Stories table -->
    <div class="card">
      <div class="card-header">
        📋 Recent Stories
        <span id="story-count"></span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Story</th>
              <th>Summary</th>
              <th>Status</th>
              <th>Score</th>
              <th>Zephyr</th>
              <th>Analysed</th>
            </tr>
          </thead>
          <tbody id="stories-body">
            <tr><td colspan="6" class="empty">Loading stories...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Live logs -->
    <div class="card">
      <div class="card-header">
        📜 Live Logs
        <span>last 80 lines</span>
      </div>
      <div class="log-box" id="log-box">
        Loading logs...
      </div>
    </div>

  </div>

</div>

<script>
async function loadStats() {
  const r = await fetch('/api/stats');
  const d = await r.json();
  document.getElementById('stat-total').textContent = d.total;
  document.getElementById('stat-pass').textContent  = d.passed;
  document.getElementById('stat-fail').textContent  = d.failed;
  document.getElementById('stat-zeph').textContent  = d.zephyr;
  document.getElementById('stat-rate').textContent  = d.pass_rate + '%';
}

async function loadStories() {
  const r = await fetch('/api/stories');
  const stories = await r.json();
  const tbody = document.getElementById('stories-body');
  document.getElementById('story-count').textContent = stories.length + ' stories';

  if (stories.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No stories analysed yet. Create a Jira story with label ReadyForAIAnalysis to get started!</td></tr>';
    return;
  }

  tbody.innerHTML = stories.slice(0, 30).map(s => {
    const statusBadge = s.status === 'PASS'
      ? '<span class="badge badge-pass">✅ PASS</span>'
      : '<span class="badge badge-fail">❌ FAIL</span>';

    const zephyrBadge = s.zephyr
      ? `<span class="badge badge-zeph">✅ ${s.zephyr_count || ''} tests</span>`
      : '<span class="badge badge-no">Pending</span>';

    const pct   = Math.round((s.score / 25) * 100);
    const color = s.score >= 18 ? '#16a34a' : '#dc2626';
    const scoreBar = `
      <div class="score-bar-wrap">
        <div class="score-bar">
          <div class="score-fill" style="width:${pct}%;background:${color}"></div>
        </div>
        <span class="score-text" style="color:${color}">${s.score}/25</span>
      </div>`;

    const summary = s.summary ? s.summary.substring(0, 35) + (s.summary.length > 35 ? '...' : '') : '—';
    const time    = s.analysed_at ? s.analysed_at.replace(' UTC','') : '—';

    return `<tr>
      <td><span class="story-key">${s.key}</span>${s.reanalysis ? ' 🔄' : ''}</td>
      <td title="${s.summary}">${summary}</td>
      <td>${statusBadge}</td>
      <td>${scoreBar}</td>
      <td>${zephyrBadge}</td>
      <td style="color:#94a3b8;font-size:11px">${time}</td>
    </tr>`;
  }).join('');
}

async function loadLogs() {
  const r = await fetch('/api/logs');
  const d = await r.json();
  const box = document.getElementById('log-box');

  const html = d.logs.map(line => {
    let cls = 'normal';
    if (line.includes('INFO:') && line.includes('200')) cls = 'info';
    else if (line.includes('PASS') || line.includes('✅')) cls = 'pass';
    else if (line.includes('FAIL') || line.includes('❌') || line.includes('ERROR')) cls = 'fail';
    else if (line.includes('⚠️') || line.includes('WARNING')) cls = 'warn';
    else if (line.includes('AGENT') || line.includes('Agent') || line.includes('🔍')) cls = 'agent';
    return `<div class="log-line ${cls}">${escapeHtml(line)}</div>`;
  }).join('');

  box.innerHTML = html || '<div class="log-line normal">No logs available yet.</div>';
  box.scrollTop = box.scrollHeight;
}

function escapeHtml(text) {
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function loadAll() {
  await Promise.all([loadStats(), loadStories(), loadLogs()]);
  document.getElementById('last-refresh').textContent =
    'Last refreshed: ' + new Date().toLocaleTimeString();
}

loadAll();
setInterval(loadAll, 10000);
</script>

</body>
</html>""")


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
            "dashboard":      "/dashboard",
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
    print("📊 Dashboard      : /dashboard")
    print("─"*80)
    missing = settings.validate()
    if missing:
        print(f"⚠️  Missing env vars: {', '.join(missing)}")
    else:
        print("✅ All required env vars are set")
    print("="*80 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)