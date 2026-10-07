from rest_framework import serializers


class QueueStateSerializer(serializers.Serializer):
    pending = serializers.IntegerField()
    retrying = serializers.IntegerField()
    terminal_failures = serializers.IntegerField()
    active_leases = serializers.IntegerField()
    stale_leases = serializers.IntegerField()
    oldest_pending_age_seconds = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()


class OperationsSnapshotSerializer(serializers.Serializer):
    status = serializers.CharField()
    generated_at = serializers.DateTimeField()
    queues = serializers.DictField(child=QueueStateSerializer())
