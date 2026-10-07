import pytest
from django.core import mail

from apps.notifications.provider import DjangoMailerEmailProvider

pytestmark = pytest.mark.django_db


def test_django_mailer_provider_uses_configured_mailer(settings):
    settings.DEFAULT_FROM_EMAIL = "notifications@example.test"
    provider = DjangoMailerEmailProvider()

    provider.send_email(
        subject="Notification test",
        body="Body\n",
        recipient="recipient@example.test",
    )

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == "Notification test"
    assert message.body == "Body\n"
    assert message.from_email == "notifications@example.test"
    assert message.to == ["recipient@example.test"]
