from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationTopic:
    recipient_field: str
    template_slug: str


NOTIFICATION_TOPICS = {
    "organizations.organization-created": NotificationTopic(
        recipient_field="owner_user_id",
        template_slug="organization-created",
    ),
    "organizations.membership-added": NotificationTopic(
        recipient_field="user_id",
        template_slug="membership-added",
    ),
    "organizations.membership-updated": NotificationTopic(
        recipient_field="user_id",
        template_slug="membership-updated",
    ),
    "organizations.membership-removed": NotificationTopic(
        recipient_field="user_id",
        template_slug="membership-removed",
    ),
    "tickets.ticket-created": NotificationTopic(
        recipient_field="owner_id",
        template_slug="ticket-created",
    ),
    "tickets.ticket-updated": NotificationTopic(
        recipient_field="owner_id",
        template_slug="ticket-updated",
    ),
    "tickets.attachment-added": NotificationTopic(
        recipient_field="owner_id",
        template_slug="ticket-attachment-added",
    ),
}

SUPPORTED_NOTIFICATION_TOPICS = tuple(NOTIFICATION_TOPICS)
