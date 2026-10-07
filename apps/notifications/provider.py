from __future__ import annotations

from typing import Protocol

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils.module_loading import import_string


class NotificationProviderError(RuntimeError):
    pass


class EmailProvider(Protocol):
    def send_email(self, *, subject: str, body: str, recipient: str) -> None: ...


class DjangoMailerEmailProvider:
    def send_email(self, *, subject: str, body: str, recipient: str) -> None:
        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )
        sent = message.send(using=settings.NOTIFICATION_MAILER_ALIAS)
        if sent != 1:
            raise NotificationProviderError("email_not_sent")


def get_email_provider() -> EmailProvider:
    provider_class = import_string(settings.NOTIFICATION_EMAIL_PROVIDER)
    return provider_class()
