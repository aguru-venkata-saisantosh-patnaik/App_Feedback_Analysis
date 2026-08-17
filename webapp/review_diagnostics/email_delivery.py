"""Sends emails via SendGrid's HTTPS API: a "your run has started" notice at
job kickoff, a failure notice on crash, and the finished report. The started/
failure notices matter as much as the report itself -- the async path has
no UI left watching it once the user closes the tab, so a missing
completion email needs to read as an unambiguous "retry" signal.

HTTPS API instead of SMTP: live testing showed the free-tier host blocks
outbound SMTP entirely (confirmed both 587/STARTTLS and 465/implicit-TLS
hang until timeout with no response at all, the signature of a firewall
silently dropping the traffic rather than rejecting it -- a common,
deliberate anti-spam policy on free-tier PaaS hosts). Outbound HTTPS
(443) isn't blocked -- it's how the app already talks to the Play Store.
SendGrid instead of Resend: Resend's shared free-tier sender can only
deliver to the account owner's own address unless a full custom domain
is verified; SendGrid's free tier supports Single Sender Verification --
proving ownership of one plain email address (a confirmation link, no
DNS records) -- after which it can send to any recipient. Needs
SENDGRID_API_KEY and SENDGRID_FROM_ADDRESS (the verified sender)."""

import base64
import os

import requests

from .report import ReportData

SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"


def _send(to_email: str, subject: str, html: str, attachment: tuple[str, bytes] | None = None) -> None:
    api_key = os.environ["SENDGRID_API_KEY"]
    from_address = os.environ["SENDGRID_FROM_ADDRESS"]

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_address, "name": "Review Diagnostic"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }
    if attachment:
        filename, content = attachment
        payload["attachments"] = [{
            "content": base64.b64encode(content).decode("ascii"),
            "filename": filename,
            "type": "text/csv",
            "disposition": "attachment",
        }]

    resp = requests.post(
        SENDGRID_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"SendGrid returned {resp.status_code}: {resp.text[:500]}")


def send_started_notice(to_email: str, app_title: str, eta_minutes: int) -> None:
    _send(
        to_email,
        f"Your review diagnostic for {app_title} has started",
        (
            f"<p>Your run for <b>{app_title}</b> has started. "
            f"Expect results in about {eta_minutes} minute(s). "
            f"You can close this tab -- the result will be emailed here.</p>"
            f"<p style='color:#8A8270;font-size:12px;'>If you don't hear back within "
            f"about {eta_minutes * 3} minutes, please retry -- something went wrong.</p>"
        ),
    )


def send_failure_notice(to_email: str, package_id: str, error: str) -> None:
    _send(
        to_email,
        f"Review diagnostic for {package_id} failed",
        (
            f"<p>Something went wrong running the diagnostic for <b>{package_id}</b>:</p>"
            f"<p style='color:#B4472A;'>{error}</p><p>Please try again.</p>"
        ),
    )


def send_report(to_email: str, data: ReportData) -> None:
    subject, html = data.to_html_email()
    _send(to_email, subject, html, attachment=("ranked_categories.csv", data.to_csv_bytes()))
