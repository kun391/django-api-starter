from rest_framework import serializers


class QueueStateSerializer(serializers.Serializer):
    pending = serializers.IntegerField()
    retrying = serializers.IntegerField()
    terminal_failures = serializers.IntegerField()
    active_leases = serializers.IntegerField()
    stale_leases = serializers.IntegerField()
    oldest_pending_age_seconds = serializers.IntegerField(allow_null=True)
    status = serializers.ChoiceField(choices=["ok", "warning", "critical"])


class OperationsSnapshotSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "warning", "critical"])
    generated_at = serializers.DateTimeField()
    queues = serializers.DictField(child=QueueStateSerializer())
