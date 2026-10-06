from rest_framework import serializers

from apps.tickets.models import Ticket, TicketAttachment


class TicketAttachmentSerializer(serializers.ModelSerializer):
    file_id = serializers.UUIDField(read_only=True)
    original_name = serializers.CharField(source="file.original_name", read_only=True)
    media_type = serializers.CharField(source="file.media_type", read_only=True)
    size = serializers.IntegerField(source="file.size", read_only=True)

    class Meta:
        model = TicketAttachment
        fields = ["file_id", "original_name", "media_type", "size", "attached_at"]
        read_only_fields = fields


class TicketSerializer(serializers.ModelSerializer):
    attachments = TicketAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = Ticket
        fields = [
            "id",
            "owner_id",
            "organization_id",
            "title",
            "description",
            "status",
            "priority",
            "attachments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TicketCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    priority = serializers.ChoiceField(
        choices=Ticket.Priority.choices,
        default=Ticket.Priority.NORMAL,
    )


class TicketUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120, required=False)
    description = serializers.CharField(
        max_length=4000,
        required=False,
        allow_blank=True,
    )
    priority = serializers.ChoiceField(
        choices=Ticket.Priority.choices,
        required=False,
    )
    status = serializers.ChoiceField(
        choices=Ticket.Status.choices,
        required=False,
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Supply at least one field to update.")
        return attrs


class TicketAttachmentCreateSerializer(serializers.Serializer):
    file_id = serializers.UUIDField()


class TicketSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField(read_only=True)
    open = serializers.IntegerField(read_only=True)
    in_progress = serializers.IntegerField(read_only=True)
    resolved = serializers.IntegerField(read_only=True)
    attachments = serializers.IntegerField(read_only=True)
