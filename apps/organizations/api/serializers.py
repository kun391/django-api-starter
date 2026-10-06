import re

from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.organizations.models import Organization, OrganizationMembership

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class OrganizationSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="actor_role", read_only=True)

    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "role", "created_at"]
        read_only_fields = fields


class OrganizationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    slug = serializers.CharField(max_length=80)

    def validate_slug(self, value):
        value = value.strip().lower()
        if not _SLUG_RE.fullmatch(value):
            raise serializers.ValidationError(
                "Use lowercase letters, digits and single hyphens."
            )
        return value


class OrganizationUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)


class MembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = ["user_id", "email", "role", "joined_at"]
        read_only_fields = fields


class MembershipCreateSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)
    role = serializers.ChoiceField(choices=OrganizationMembership.Role.choices)

    def validate_user_id(self, value):
        user = get_user_model().objects.filter(pk=value, is_active=True).first()
        if user is None:
            raise serializers.ValidationError("Unknown active user.")
        self.context["target_user"] = user
        return value


class MembershipUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=OrganizationMembership.Role.choices)
