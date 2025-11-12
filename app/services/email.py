# app/services/email.py
from __future__ import annotations
from typing import List, Union, Optional
import resend
from app.core.config import settings

resend.api_key = settings.RESEND_API_KEY or ""

def send_email_resend(
    to: Union[str, List[str]],
    subject: str,
    html: str,
    text: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> bool:
    if not settings.RESEND_API_KEY:
        print("WARN: RESEND_API_KEY no configurada, no se envía correo.")
        return False

    recipients = [to] if isinstance(to, str) else [x for x in (to or []) if x]
    if not recipients:
        print("WARN: lista de destinatarios vacía.")
        return False

    sender = settings.EMAIL_FROM or "Colegio Médico <onboarding@resend.dev>"

    try:
        payload: dict = {
            "from": sender,
            "to": recipients,
            "subject": subject,
            "html": html,
        }
        if text:
            payload["text"] = text
        if reply_to:
            payload["reply_to"] = reply_to

        resp = resend.Emails.send(payload)
        # resp puede ser dict o Email; intentamos detectar id o error
        rid = getattr(resp, "id", None) or (isinstance(resp, dict) and resp.get("id"))
        err = getattr(resp, "error", None) or (isinstance(resp, dict) and resp.get("error"))
        if not rid or err:
            print("EMAIL_SEND_FAILED:", err or resp)
            return False
        return True
    except Exception as e:
        print("EMAIL_SEND_FAILED:", e)
        return False
