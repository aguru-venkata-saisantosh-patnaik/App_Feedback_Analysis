"""Sends emails via Gmail's SMTP relay: a "your run has started" notice at
job kickoff, a failure notice on crash, and the finished report. The started/
failure notices matter as much as the report itself -- the async path has
no UI left watching it once the user closes the tab, so a missing
completion email needs to read as an unambiguous "retry" signal.

Gmail instead of a transactional-email API (Resend, etc.): those all
require verifying a custom domain before they'll send to anyone other
than the account owner's own address -- a real blocker for a free,
no-infrastructure hobby tool with no domain. Gmail's SMTP relay sends
from the operator's own Gmail address to any recipient with no domain
verification at all, since it's authenticating as an address already
owned and controlled by that Gmail account. Needs GMAIL_ADDRESS and a
Gmail App Password (not the account password) in GMAIL_APP_PASSWORD --
Google Account -> Security -> App passwords, requires 2-Step Verification
enabled first."""

import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .report import ReportData

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587


def _send(to_email: str, subject: str, html: str, attachment: tuple[str, bytes] | None = None) -> None:
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))

    if attachment:
        filename, content = attachment
        part = MIMEApplication(content, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)

    with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=20) as server:
        server.starttls()
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, [to_email], msg.as_string())


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
