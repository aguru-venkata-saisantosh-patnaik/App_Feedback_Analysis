"""Sends emails via Resend: a "your run has started" notice at job
kickoff, a failure notice on crash, and the finished report. The started/
failure notices matter as much as the report itself -- the async path has
no UI left watching it once the user closes the tab, so a missing
completion email needs to read as an unambiguous "retry" signal."""

import base64
import os

from . import config
from .report import ReportData


def _client():
    import resend

    resend.api_key = os.environ["RESEND_API_KEY"]
    return resend


def send_started_notice(to_email: str, app_title: str, eta_minutes: int) -> None:
    resend = _client()
    resend.Emails.send(
        {
            "from": config.RESEND_FROM_ADDRESS,
            "to": [to_email],
            "subject": f"Your review diagnostic for {app_title} has started",
            "html": (
                f"<p>Your run for <b>{app_title}</b> has started. "
                f"Expect results in about {eta_minutes} minute(s). "
                f"You can close this tab -- the result will be emailed here.</p>"
                f"<p style='color:#8A8270;font-size:12px;'>If you don't hear back within "
                f"about {eta_minutes * 3} minutes, please retry -- something went wrong.</p>"
            ),
        }
    )


def send_failure_notice(to_email: str, package_id: str, error: str) -> None:
    resend = _client()
    resend.Emails.send(
        {
            "from": config.RESEND_FROM_ADDRESS,
            "to": [to_email],
            "subject": f"Review diagnostic for {package_id} failed",
            "html": (
                f"<p>Something went wrong running the diagnostic for <b>{package_id}</b>:</p>"
                f"<p style='color:#B4472A;'>{error}</p><p>Please try again.</p>"
            ),
        }
    )


def send_report(to_email: str, data: ReportData) -> None:
    resend = _client()
    subject, html = data.to_html_email()
    csv_b64 = base64.b64encode(data.to_csv_bytes()).decode("ascii")
    resend.Emails.send(
        {
            "from": config.RESEND_FROM_ADDRESS,
            "to": [to_email],
            "subject": subject,
            "html": html,
            "attachments": [{"filename": "ranked_categories.csv", "content": csv_b64}],
        }
    )
