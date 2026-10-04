"""Provider boundary. The only implementation here is non-sending and local."""

import hashlib
import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Protocol

from app.schemas.outreach import ProviderMessage, ProviderResult, ProviderStatus


class EmailProvider(Protocol):
    provider_name: str

    async def send(self, message: ProviderMessage) -> ProviderResult: ...


class MockEmailProvider:
    """Simulates delivery outcomes; it has no network or email transport code."""

    provider_name = "mock"
    sends_real_email = False

    def __init__(
        self,
        outcomes: Sequence[ProviderStatus] = (ProviderStatus.SENT,),
        *,
        timestamp: datetime | None = None,
    ) -> None:
        self.outcomes = list(outcomes) or [ProviderStatus.SENT]
        self.timestamp = timestamp or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.calls: list[ProviderMessage] = []

    async def send(self, message: ProviderMessage) -> ProviderResult:
        self.calls.append(message)
        index = len(self.calls) - 1
        status = self.outcomes[min(index, len(self.outcomes) - 1)]
        message_id = None
        if status == ProviderStatus.SENT:
            message_id = "mock-" + hashlib.sha256(message.idempotency_key.encode()).hexdigest()[:16]
        error = None if status == ProviderStatus.SENT else f"Mock provider simulated {status.value}."
        return ProviderResult(
            provider_message_id=message_id, status=status, timestamp=self.timestamp,
            recipient=message.recipient, subject=message.subject, error=error,
            provider_name=self.provider_name,
        )


class SMTPEmailProvider:
    """Optional STARTTLS SMTP sender, called only after the approval gate."""

    provider_name = "smtp"
    sends_real_email = True

    def __init__(self, *, host: str, port: int, from_email: str, from_name: str,
                 username: str | None = None, password: str | None = None,
                 timeout_seconds: float = 15.0) -> None:
        self.host, self.port = host, port
        self.from_email, self.from_name = from_email, from_name
        self.username, self.password = username, password
        self.timeout_seconds = timeout_seconds

    async def send(self, message: ProviderMessage) -> ProviderResult:
        return await asyncio.to_thread(self._send, message)

    def _send(self, message: ProviderMessage) -> ProviderResult:
        email_message = EmailMessage()
        email_message["To"] = message.recipient
        email_message["From"] = formataddr((self.from_name, self.from_email))
        email_message["Subject"] = message.subject
        email_message["Message-ID"] = f"<{message.idempotency_key[:40]}@studentsflow.local>"
        email_message.set_content(message.body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                if self.username:
                    smtp.login(self.username, self.password or "")
                smtp.send_message(email_message)
            return ProviderResult(provider_message_id=email_message["Message-ID"],
                                  status=ProviderStatus.SENT, recipient=message.recipient,
                                  subject=message.subject, provider_name=self.provider_name)
        except smtplib.SMTPAuthenticationError:
            return self._failure(message, ProviderStatus.PERMANENT_FAILURE,
                                 "SMTP authentication failed; verify provider configuration.")
        except smtplib.SMTPRecipientsRefused:
            return self._failure(message, ProviderStatus.PERMANENT_FAILURE,
                                 "SMTP provider refused the recipient.")
        except (TimeoutError, smtplib.SMTPServerDisconnected, OSError):
            return self._failure(message, ProviderStatus.TEMPORARY_FAILURE,
                                 "SMTP provider could not be reached; retry may be safe.")
        except smtplib.SMTPResponseException as error:
            status = (ProviderStatus.TEMPORARY_FAILURE if 400 <= error.smtp_code < 500
                      else ProviderStatus.PERMANENT_FAILURE)
            return self._failure(message, status, "SMTP provider rejected the message.")
        except smtplib.SMTPException:
            return self._failure(message, ProviderStatus.FAILED,
                                 "SMTP provider failed without a safe retry classification.")

    def _failure(self, message: ProviderMessage, status: ProviderStatus,
                 explanation: str) -> ProviderResult:
        return ProviderResult(status=status, recipient=message.recipient,
                              subject=message.subject, error=explanation,
                              provider_name=self.provider_name)
