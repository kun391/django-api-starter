from rest_framework import serializers

from apps.notifications.models import NotificationDelivery
from apps.notifications.topics import SUPPORTED_NOTIFICATION_TOPICS


class NotificationPreferenceSerializer(serializers.Serializer):
    topic = serializers.ChoiceField(choices=SUPPORTED_NOTIFICATION_TOPICS)
    email_enabled = serializers.BooleanField()
    is_override = serializers.BooleanField(read_only=True)


class NotificationPreferenceUpdateSerializer(serializers.Serializer):
    email_enabled = serializers.BooleanField()


class NotificationDeliverySerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = NotificationDelivery
        fields = [
            "id",
            "source_event_id",
            "event_topic",
            "recipient_email",
            "template_slug",
            "status",
            "attempts",
            "next_attempt_at",
            "last_attempt_at",
            "delivered_at",
            "failed_at",
            "cancelled_at",
            "last_error_code",
            "created_at",
        ]
        read_only_fields = fields

    def get_status(self, obj) -> str:
        if obj.delivered_at:
            return "delivered"
        if obj.failed_at:
            return "failed"
        if obj.cancelled_at:
            return "cancelled"
        if obj.attempts:
            return "retrying"
        return "pending"
