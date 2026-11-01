"""The ways a notification leaves the system: e-mail and WhatsApp, behind one small interface.

`console` only logs (development and tests, and the safe default until credentials exist). `smtp`
sends mail through any SMTP service (the credentials come from the server settings). `cloud` sends
a WhatsApp Business Cloud API template message with two text variables (a heading and the message),
the template and its language coming from the settings. A sender raises when the message could not
be handed over; the delivery job decides when to retry. Nothing here ever logs a password or token.
"""

import asyncio
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

import httpx

from heyyerp_api.config import get_settings

log = logging.getLogger("heyyerp.notify")
GRAPH = "https://graph.facebook.com/v21.0"


class Sender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleSender:
    """Logs the message instead of sending it; keeps what it was given so tests can look."""

    def __init__(self, channel: str) -> None:
        self.channel, self.sent = channel, []  # type: ignore[var-annotated]

    async def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))
        log.info("[%s to %s] %s", self.channel, to, subject)


class SmtpSender:
    async def send(self, to: str, subject: str, body: str) -> None:
        cfg = get_settings()
        if not cfg.smtp_host:
            raise RuntimeError("SMTP_HOST is not set")
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = cfg.mail_from, to, subject
        message.set_content(body)
        await asyncio.to_thread(self._deliver, message)

    @staticmethod
    def _deliver(message: EmailMessage) -> None:
        cfg = get_settings()
        assert cfg.smtp_host
        if cfg.smtp_port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=20,
                                                    context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20)
            server.starttls(context=ssl.create_default_context())
        with server:
            if cfg.smtp_user and cfg.smtp_password:
                server.login(cfg.smtp_user, cfg.smtp_password)
            server.send_message(message)


class WhatsAppCloudSender:
    async def send(self, to: str, subject: str, body: str) -> None:
        cfg = get_settings()
        if not (cfg.whatsapp_token and cfg.whatsapp_phone_number_id):
            raise RuntimeError("WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID are not set")
        payload = {
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": cfg.whatsapp_template,
                         "language": {"code": cfg.whatsapp_template_language},
                         "components": [{"type": "body", "parameters": [
                             {"type": "text", "text": subject[:60]},
                             {"type": "text", "text": body[:500].replace("\n", " ")}]}]}}
        async with httpx.AsyncClient(timeout=20) as client:
            answer = await client.post(
                f"{GRAPH}/{cfg.whatsapp_phone_number_id}/messages", json=payload,
                headers={"Authorization": f"Bearer {cfg.whatsapp_token}"})
        if answer.status_code >= 300:
            raise RuntimeError(f"WhatsApp refused the message ({answer.status_code}): "
                               f"{answer.text[:200]}")


_console: dict[str, ConsoleSender] = {}
override: dict[str, Sender] = {}  # tests and special set-ups put their own sender here


def sender_for(channel: str) -> Sender:
    """The sender for EMAIL or WHATSAPP, by the server settings."""
    if channel in override:
        return override[channel]
    cfg = get_settings()
    if channel == "EMAIL" and cfg.mail_provider == "smtp":
        return SmtpSender()
    if channel == "WHATSAPP" and cfg.whatsapp_provider == "cloud":
        return WhatsAppCloudSender()
    return _console.setdefault(channel, ConsoleSender(channel))


def whatsapp_number(raw: str | None) -> str | None:
    """A WhatsApp number in international form without a plus, or None if it cannot be one.

    A 10 digit Indian mobile number gets the 91 prefix."""
    digits = re.sub(r"[\s\-()+]", "", raw or "")
    if not digits.isdigit():
        return None
    if len(digits) == 10:
        digits = "91" + digits
    return digits if 11 <= len(digits) <= 15 else None
