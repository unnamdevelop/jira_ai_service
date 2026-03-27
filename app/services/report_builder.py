"""
Local report file builder.
Writes a plain-text analysis report to the reports/ directory.
"""

import os


REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "reports")


def build_report_file(
    key: str,
    invest_score: int,
    quality_gate_passed: bool,
    invest_report: str,
    test_scenarios: str,
    gap_analysis: str,
) -> str | None:
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        safe_key        = key.replace("-", "_")
        report_filename = os.path.join(REPORTS_DIR, f"report_{safe_key}.txt")

        if quality_gate_passed:
            content = (
                f"╔════════════════════════════════════════════════════════════════╗\n"
                f"║  AI ANALYSIS REPORT - {key}\n"
                f"║  Status: ✅ QUALITY GATE PASSED (Score: {invest_score}/25)\n"
                f"╚════════════════════════════════════════════════════════════════╝\n\n"
                f"{invest_report}\n\n"
                f"{'='*70}\n"
                f"ACCEPTANCE CRITERIA — BDD TEST SCENARIOS\n"
                f"{'='*70}\n\n"
                f"{test_scenarios}\n\n"
                f"{'='*70}\n"
                f"✅ Story passed quality gate.\n"
                f"   Acceptance Criteria have been added to the Description field in JIRA.\n"
            )
        else:
            content = (
                f"╔════════════════════════════════════════════════════════════════╗\n"
                f"║  AI ANALYSIS REPORT - {key}\n"
                f"║  Status: ❌ QUALITY GATE FAILED (Score: {invest_score}/25)\n"
                f"╚════════════════════════════════════════════════════════════════╝\n\n"
                f"{invest_report}\n\n"
                f"{'='*70}\n"
                f"❌ QUALITY GATE FAILED - ACTION REQUIRED\n"
                f"{'='*70}\n\n"
                f"Story score:        {invest_score}/25\n"
                f"Required threshold: 18/25 (70%)\n\n"
                f"{gap_analysis}\n\n"
                f"{'='*70}\n"
                f"⚠️  Story requires refinement before development can proceed.\n"
            )

        with open(report_filename, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"\n✅ Report saved to: {report_filename}")
        return report_filename

    except Exception as e:
        print(f"\n⚠️  Could not save report file: {e}")
        return None
