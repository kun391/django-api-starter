from django_filters import rest_framework as filters
from rest_framework import serializers

from apps.core.models import AuditEvent


class AuditEventFilter(filters.FilterSet):
    occurred_after = filters.IsoDateTimeFilter(
        field_name="occurred_at",
        lookup_expr="gte",
    )
    occurred_before = filters.IsoDateTimeFilter(
        field_name="occurred_at",
        lookup_expr="lte",
    )

    class Meta:
        model = AuditEvent
        fields = ["action", "subject_type", "subject_id", "actor_id"]


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "action",
            "subject_type",
            "subject_id",
            "actor_id",
            "organization_id",
            "request_id",
            "metadata",
            "occurred_at",
        ]
        read_only_fields = fields
