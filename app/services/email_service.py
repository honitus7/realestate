"""Minimal SMTP email sender for transactional notifications."""

from email.message import EmailMessage
import smtplib
import json
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


def send_email(
    smtp_host,
    smtp_port,
    smtp_username,
    smtp_password,
    smtp_use_tls,
    from_email,
    from_name,
    to_email,
    subject,
    text_body,
    html_body=None,
    brevo_api_key=None,
):
    if not from_email or not to_email:
        raise ValueError("from_email and to_email are required")

    # Prefer Brevo API when key is configured.
    if brevo_api_key:
        payload = {
            "sender": {"name": from_name or "", "email": from_email},
            "to": [{"email": to_email}],
            "subject": subject or "",
            "textContent": text_body or "",
        }
        if html_body:
            payload["htmlContent"] = html_body
        body_bytes = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=body_bytes,
            method="POST",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "api-key": brevo_api_key,
            },
        )
        try:
            with urlrequest.urlopen(req, timeout=20) as resp:
                if resp.status >= 300:
                    raise RuntimeError(f"Brevo API failed with status {resp.status}")
            return
        except HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="ignore")
            except Exception:
                detail = str(e)
            raise RuntimeError(f"Brevo API error: {e.code} {detail}") from e
        except URLError as e:
            raise RuntimeError(f"Brevo API connection error: {e}") from e

    if not smtp_host:
        raise ValueError("SMTP is not configured")

    # Fallback to SMTP when Brevo API key is not configured.
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    msg.set_content(text_body or "")
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(smtp_host, int(smtp_port), timeout=20) as server:
        if smtp_use_tls:
            server.starttls()
        if smtp_username:
            server.login(smtp_username, smtp_password or "")
        server.send_message(msg)
