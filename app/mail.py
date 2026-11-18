"""Outgoing email over SMTP (Gmail with an app password, Brevo, or any relay).

One message per team, addressed to every member. The access code is in the body and
nowhere else: not in the API response, not in the log (email_log keeps recipients, subject
and the outcome).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from app.config import get_settings

log = logging.getLogger(__name__)


def configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_user and s.smtp_password and s.smtp_from)


def _send_sync(to: list[str], subject: str, text: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=(s.smtp_from or "x@perplexed").split("@")[-1].strip(">"))
    msg.set_content(text)
    context = ssl.create_default_context()
    if s.smtp_port == 465:
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context, timeout=20) as smtp:
            smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
            smtp.send_message(msg)


# tests replace this with a recorder; everything else calls send()
_transport = _send_sync


async def send(to: list[str], subject: str, text: str) -> tuple[str, str | None]:
    """Returns (status, error): 'sent', 'failed' or 'not_configured'. Never raises."""
    if not configured():
        return "not_configured", "SMTP settings are missing in .env"
    try:
        await asyncio.to_thread(_transport, to, subject, text)
        return "sent", None
    except Exception as exc:  # network, auth, quota: the organisers see it and can resend
        log.warning("email to %s failed: %s", to, exc)
        return "failed", f"{type(exc).__name__}: {exc}"[:500]


def access_code_email(
    team_name: str, team_code: str, access_code: str, url: str
) -> tuple[str, str]:
    subject = f"PERPLEXED 2.0: {team_name}'s access code"
    text = f"""Hello,

Your team "{team_name}" ({team_code}) is registered for PERPLEXED 2.0.

Your team's access code:

    {access_code}

Log in at {url}
Everyone on the team uses the same code. Up to two laptops can be logged in at once.
Keep the code within your team: anyone with it can play as you.

If you didn't expect this email, someone may have registered you by mistake. Reply to
this address and the organisers will remove you.

PERPLEXED 2.0 · PLEX
"""
    return subject, text
