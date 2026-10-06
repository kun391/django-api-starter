from rest_framework import serializers

from apps.files.models import PrivateFile


class UploadSerializer(serializers.Serializer):
    purpose = serializers.CharField(max_length=48, default="document")
    file = serializers.FileField(allow_empty_file=False, max_length=120)


class ReplaceSerializer(serializers.Serializer):
    file = serializers.FileField(allow_empty_file=False, max_length=120)


class PrivateFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrivateFile
        fields = ["id", "purpose", "original_name", "size", "media_type", "sha256", "created_at"]
        read_only_fields = fields


class DownloadURLSerializer(serializers.Serializer):
    url = serializers.URLField(read_only=True)
    expires_in = serializers.IntegerField(read_only=True)
