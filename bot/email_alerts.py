"""
Gmail SMTP failure alerts.

Sends a plain-text email to the account owner when an automated stage fails.
Credentials come from the environment (GitHub Actions secrets):

    GMAIL_FROM           sender address (also the recipient — sends to self)
    GMAIL_APP_PASSWORD   Google *app password*, NOT the account password
                         (https://myaccount.google.com/apppasswords)

If either variable is unset the call is a silent no-op — automation must never
fail because alerting is unconfigured. Nothing raises out of this module.

Security: the app password is never logged, echoed, or included in any error
message. Only the SMTP exception class/message is printed.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT = 30


def send_alert(subject: str, body: str) -> bool:
    """Send an alert email via Gmail SMTP over SSL.

    Parameters
    ----------
    subject:
        Subject line. ``"[FPL Auto] "`` is prefixed automatically.
    body:
        Plain-text message body.

    Returns
    -------
    bool
        True if the message was handed to Gmail, False if skipped or failed.
        Never raises.
    """
    try:
        sender = (os.environ.get("GMAIL_FROM") or "").strip()
        password = (os.environ.get("GMAIL_APP_PASSWORD") or "").strip()

        if not sender or not password:
            log.info("Email alert skipped — GMAIL_FROM / GMAIL_APP_PASSWORD not set.")
            return False

        msg = EmailMessage()
        msg["Subject"] = f"[FPL Auto] {subject}"
        msg["From"] = sender
        msg["To"] = sender
        msg.set_content(body)

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)

        log.info("Email alert sent: %s", subject)
        return True

    except Exception as exc:  # noqa: BLE001 — alerting must never crash the caller
        # Print the exception type/message only; credentials are never included.
        log.warning("Email alert failed (%s: %s)", type(exc).__name__, exc)
        print(f"[email_alerts] send failed ({type(exc).__name__}: {exc})")
        return False
