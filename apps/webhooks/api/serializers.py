from rest_framework import serializers

from apps.webhooks.models import WebhookDelivery, WebhookSubscription
from apps.webhooks.security import validate_webhook_url
from apps.webhooks.services import normalize_events
from apps.webhooks.topics import SUPPORTED_WEBHOOK_TOPICS


class WebhookSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookSubscription
        fields = [
            "id",
            "url",
            "events",
            "is_active",
            "secret_version",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WebhookSubscriptionCreateSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2048)
    events = serializers.ListField(
        child=serializers.ChoiceField(choices=SUPPORTED_WEBHOOK_TOPICS),
        allow_empty=False,
    )

    def validate_url(self, value):
        return validate_webhook_url(value)

    def validate_events(self, value):
        return normalize_events(value)


class WebhookSubscriptionUpdateSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2048, required=False)
    events = serializers.ListField(
        child=serializers.ChoiceField(choices=SUPPORTED_WEBHOOK_TOPICS),
        allow_empty=False,
        required=False,
    )
    is_active = serializers.BooleanField(required=False)

    def validate_url(self, value):
        return validate_webhook_url(value)

    def validate_events(self, value):
        return normalize_events(value)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Supply at least one field to update.")
        return attrs


class WebhookSubscriptionCreatedSerializer(WebhookSubscriptionSerializer):
    signing_secret = serializers.CharField(read_only=True)


class WebhookSecretSerializer(serializers.Serializer):
    secret_version = serializers.IntegerField(read_only=True)
    signing_secret = serializers.CharField(read_only=True)


class WebhookDeliverySerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = WebhookDelivery
        fields = [
            "id",
            "subscription_id",
            "source_event_id",
            "event_topic",
            "event_version",
            "status",
            "attempts",
            "response_status",
            "last_error_code",
            "next_attempt_at",
            "last_attempt_at",
            "delivered_at",
            "failed_at",
            "cancelled_at",
            "replay_of_id",
            "created_at",
        ]
        read_only_fields = fields

    def get_status(self, obj):
        if obj.delivered_at:
            return "delivered"
        if obj.failed_at:
            return "failed"
        if obj.cancelled_at:
            return "cancelled"
        if obj.attempts:
            return "retrying"
        return "pending"
