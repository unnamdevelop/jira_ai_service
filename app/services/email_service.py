"""
Email notification service.
Sends HTML emails to story reporters on analysis completion and Zephyr upload.
"""

import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings


def send_analysis_email(
    issue_key: str,
    reporter_email: str,
    quality_passed: bool,
    score: int,
    report_file: str,
) -> bool:
    """Send HTML analysis email to the story creator."""
    try:
        if not all([settings.EMAIL_SENDER, settings.EMAIL_PASSWORD]):
            print("⚠️  Email credentials not configured — skipping email")
            return False

        story_url    = f"{settings.JIRA_URL}/browse/{issue_key}"
        status       = "PASSED" if quality_passed else "FAILED"
        status_color = "#2e7d32" if quality_passed else "#c62828"
        status_emoji = "✅" if quality_passed else "❌"
        subject      = f"AI Analysis {'Completed ✅' if quality_passed else 'Needs Review ⚠️'} - {issue_key}"

        if quality_passed:
            outcome_message = (
                "Your story meets quality standards and is <strong>ready for development</strong>.<br>"
                "Acceptance Criteria (BDD scenarios) have been added to the "
                "<strong>Description</strong> field in JIRA.<br><br>"
                "<em>Please find the attachment for the detailed user story analysis and scenarios.</em>"
            )
        else:
            outcome_message = (
                "Your story needs <strong>refinement</strong> before development can begin.<br>"
                "Please review the Gap Analysis and recommendations posted as a JIRA comment.<br><br>"
                "<em>Please find the attachment for the detailed user story analysis report.</em>"
            )

        html_body = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f4f5f7;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f5f7;padding:32px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background-color:#ffffff;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden;">
        <tr>
          <td style="background-color:#0052cc;padding:24px 32px;">
            <h1 style="margin:0;color:#ffffff;font-size:20px;font-weight:700;">AI Story Analysis Report</h1>
            <p style="margin:4px 0 0;color:#b3d1ff;font-size:13px;">Automated DoR Quality Gate</p>
          </td>
        </tr>
        <tr>
          <td style="padding:32px;">
            <p style="margin:0 0 20px;color:#172b4d;font-size:15px;">Hello,</p>
            <p style="margin:0 0 24px;color:#172b4d;font-size:15px;">
              The AI analysis for user story <strong style="color:#0052cc;">{issue_key}</strong> has been completed.
            </p>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background-color:#f4f5f7;border-radius:6px;border-left:4px solid {status_color};margin-bottom:24px;">
              <tr><td style="padding:16px 20px;">
                <p style="margin:0 0 8px;font-size:13px;color:#6b778c;text-transform:uppercase;">Analysis Results</p>
                <table cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="padding:3px 0;font-size:14px;color:#172b4d;width:160px;">Status</td>
                    <td style="padding:3px 0;font-size:14px;color:{status_color};font-weight:700;">{status_emoji}&nbsp;{status}</td>
                  </tr>
                  <tr>
                    <td style="padding:3px 0;font-size:14px;color:#172b4d;">DoR Score</td>
                    <td style="padding:3px 0;font-size:14px;color:#172b4d;font-weight:700;">{score}/25</td>
                  </tr>
                  <tr>
                    <td style="padding:3px 0;font-size:14px;color:#172b4d;">Quality Gate Threshold</td>
                    <td style="padding:3px 0;font-size:14px;color:#172b4d;">{settings.QG_THRESHOLD}/25</td>
                  </tr>
                </table>
              </td></tr>
            </table>
            <p style="margin:0 0 24px;color:#172b4d;font-size:15px;line-height:1.6;">{outcome_message}</p>
            <table cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
              <tr><td style="background-color:#0052cc;border-radius:4px;">
                <a href="{story_url}" target="_blank"
                   style="display:inline-block;padding:12px 24px;color:#ffffff;font-size:14px;font-weight:700;text-decoration:none;">
                  🔗 View Story {issue_key} in JIRA
                </a>
              </td></tr>
            </table>
            <hr style="border:none;border-top:1px solid #dfe1e6;margin:0 0 24px;">
            <p style="margin:0;color:#6b778c;font-size:12px;line-height:1.6;">
              Best regards,<br><strong>AI Analysis Service</strong><br><br>
              <em>This is an automated message. Please do not reply to this email.</em>
            </p>
          </td>
        </tr>
        <tr>
          <td style="background-color:#f4f5f7;padding:16px 32px;border-top:1px solid #dfe1e6;">
            <p style="margin:0;color:#97a0af;font-size:11px;text-align:center;">
              AI Story Analysis Service &bull; Powered by LangGraph &amp; GPT-4o
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(f"AI Analysis {status} for {issue_key}. Score: {score}/25.", "plain"))
        alt_part.attach(MIMEText(html_body, "html"))

        outer           = MIMEMultipart("mixed")
        outer["From"]   = settings.EMAIL_SENDER
        outer["To"]     = reporter_email
        outer["Subject"] = subject
        outer.attach(alt_part)

        if report_file and os.path.exists(report_file):
            with open(report_file, "rb") as f:
                attachment = MIMEBase("application", "octet-stream")
                attachment.set_payload(f.read())
                encoders.encode_base64(attachment)
                attachment.add_header(
                    "Content-Disposition",
                    f"attachment; filename={os.path.basename(report_file)}",
                )
            outer.attach(attachment)

        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.EMAIL_SENDER, settings.EMAIL_PASSWORD)
            server.send_message(outer)

        print(f"✅ Email sent to {reporter_email}")
        return True

    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        return False


def send_zephyr_upload_email(
    issue_key: str,
    reporter_email: str,
    folder_name: str,
    uploaded_tcs: list,
    project_key: str,
) -> bool:
    """Send a rich HTML email summarising the Zephyr Scale upload."""
    try:
        if not all([settings.EMAIL_SENDER, settings.EMAIL_PASSWORD]):
            print("⚠️  Email credentials not configured — skipping Zephyr upload email")
            return False

        jira_base         = settings.JIRA_URL
        story_url         = f"{jira_base}/browse/{issue_key}"
        tc_count          = len(uploaded_tcs)
        subject           = f"🧪 Test Cases Uploaded to Zephyr Scale — {issue_key} ({tc_count} scenarios)"
        zephyr_folder_url = (
            f"{jira_base}/projects/{project_key}"
            f"?selectedItem=com.atlassian.plugins.atlassian-connect-plugin"
            f":com.kanoah.test-manager__main-project-page"
        )

        scenario_rows = ""
        for i, tc in enumerate(uploaded_tcs, start=1):
            tc_key  = tc["tc_key"]
            name    = tc["name"]
            tc_link = (
                f"{jira_base}/projects/{project_key}"
                f"?selectedItem=com.atlassian.plugins.atlassian-connect-plugin"
                f":com.kanoah.test-manager__main-project-page"
                f"#!/testCase/{tc_key}"
            )
            scenario_rows += f"""
            <tr style="border-bottom:1px solid #dfe1e6;">
              <td style="padding:10px 12px;font-size:13px;color:#6b778c;text-align:center;">{i}</td>
              <td style="padding:10px 12px;font-size:13px;">
                <a href="{tc_link}" target="_blank"
                   style="color:#0052cc;font-weight:600;text-decoration:none;">{tc_key}</a>
              </td>
              <td style="padding:10px 12px;font-size:13px;color:#172b4d;">{name}</td>
              <td style="padding:10px 12px;text-align:center;">
                <span style="background-color:#e3fcef;color:#006644;font-size:11px;font-weight:700;
                             padding:3px 8px;border-radius:3px;">BDD-Gherkin ✓</span>
              </td>
            </tr>"""

        html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f4f5f7;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0;background:#f4f5f7;">
    <tr><td align="center">
      <table width="650" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <tr><td style="background:linear-gradient(135deg,#0052cc,#0065ff);padding:28px 32px;">
          <h1 style="margin:0;color:#fff;font-size:20px;">🧪 Zephyr Scale Upload Complete</h1>
          <p style="margin:5px 0 0;color:#b3d1ff;font-size:13px;">BDD test cases are live and ready for execution</p>
        </td></tr>
        <tr><td style="padding:28px 32px;">
          <p style="margin:0 0 16px;font-size:14px;color:#172b4d;font-weight:700;">📋 Uploaded Test Scenarios</p>
          <table width="100%" style="border:1px solid #dfe1e6;border-radius:6px;border-collapse:collapse;">
            <thead><tr style="background:#f4f5f7;">
              <th style="padding:10px 12px;font-size:11px;color:#6b778c;text-transform:uppercase;width:40px;">#</th>
              <th style="padding:10px 12px;font-size:11px;color:#6b778c;text-transform:uppercase;width:100px;text-align:left;">TC Key</th>
              <th style="padding:10px 12px;font-size:11px;color:#6b778c;text-transform:uppercase;text-align:left;">Scenario</th>
              <th style="padding:10px 12px;font-size:11px;color:#6b778c;text-transform:uppercase;width:130px;">Script</th>
            </tr></thead>
            <tbody>{scenario_rows}</tbody>
          </table>
        </td></tr>
        <tr><td style="padding:0 32px 28px;">
          <table cellpadding="0" cellspacing="0"><tr>
            <td style="background:#0052cc;border-radius:4px;">
              <a href="{story_url}" style="display:inline-block;padding:12px 22px;color:#fff;font-size:13px;font-weight:700;text-decoration:none;">🔗 View Story in JIRA</a>
            </td>
            <td width="12"></td>
            <td style="background:#006644;border-radius:4px;">
              <a href="{zephyr_folder_url}" style="display:inline-block;padding:12px 22px;color:#fff;font-size:13px;font-weight:700;text-decoration:none;">🧪 View in Zephyr Scale</a>
            </td>
          </tr></table>
        </td></tr>
        <tr><td style="background:#f4f5f7;padding:16px 32px;border-top:1px solid #dfe1e6;">
          <p style="margin:0;color:#97a0af;font-size:11px;text-align:center;">
            AI Story Analysis Service &bull; Powered by LangGraph &amp; GPT-4o &bull; Zephyr Scale Cloud REST API v2<br>
            <em>This is an automated message. Please do not reply.</em>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

        msg             = MIMEMultipart("alternative")
        msg["From"]     = settings.EMAIL_SENDER
        msg["To"]       = reporter_email
        msg["Subject"]  = subject
        msg.attach(MIMEText(
            f"Zephyr upload complete for {issue_key}: {tc_count} BDD test cases created in folder '{folder_name}'.",
            "plain",
        ))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.EMAIL_SENDER, settings.EMAIL_PASSWORD)
            server.send_message(msg)

        print(f"✅ Zephyr upload email sent to {reporter_email}")
        return True

    except Exception as e:
        print(f"❌ Failed to send Zephyr upload email: {e}")
        return False
