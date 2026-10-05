from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.services.register_user import register_user


class UserSerializer(serializers.ModelSerializer):
    """Public representation of a user."""

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "bio",
            "birth_date",
            "date_joined",
        ]
        read_only_fields = fields


class UserCreateSerializer(serializers.ModelSerializer):
    """Registration input."""

    password = serializers.CharField(write_only=True, trim_whitespace=False)

    class Meta:
        model = User
        fields = ["username", "email", "password", "first_name", "last_name"]

    def create(self, validated_data):
        return register_user(**validated_data)


class UserUpdateSerializer(serializers.ModelSerializer):
    """Fields a user may update on their own profile."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "bio", "birth_date"]
