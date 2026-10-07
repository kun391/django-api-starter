from dataclasses import dataclass

from django.template.loader import render_to_string


@dataclass(frozen=True)
class RenderedNotification:
    subject: str
    body: str


def render_notification(template_slug: str, context: dict) -> RenderedNotification:
    prefix = f"notifications/{template_slug}"
    subject = render_to_string(f"{prefix}/subject.txt", context).strip()
    if "\r" in subject or "\n" in subject:
        raise ValueError("Notification subject must render to exactly one line.")
    body = render_to_string(f"{prefix}/body.txt", context).strip() + "\n"
    return RenderedNotification(subject=subject, body=body)
