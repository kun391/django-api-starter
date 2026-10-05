from django_filters import rest_framework as filters

from apps.accounts.models import User


class UserFilter(filters.FilterSet):
    is_active = filters.TypedChoiceFilter(
        choices=(("true", "true"), ("false", "false")),
        coerce=lambda value: value == "true",
    )

    class Meta:
        model = User
        fields = ["username", "email", "is_active"]
