"""Explicit query allowlists and stable ordering; no arbitrary ORM lookups."""

from django.core.exceptions import ImproperlyConfigured
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.settings import api_settings


class StrictDjangoFilterBackend(DjangoFilterBackend):
    def filter_queryset(self, request, queryset, view):
        filterset_class = self.get_filterset_class(view, queryset)
        allowed = set(filterset_class.base_filters) if filterset_class else set()
        if getattr(view, "search_fields", None):
            allowed.add(api_settings.SEARCH_PARAM)
        if getattr(view, "ordering_fields", None):
            allowed.add(api_settings.ORDERING_PARAM)
        paginator = getattr(view, "paginator", None)
        if paginator:
            allowed.update(
                name for name in (
                    getattr(paginator, "page_query_param", None),
                    getattr(paginator, "page_size_query_param", None),
                ) if name
            )
        if api_settings.URL_FORMAT_OVERRIDE:
            allowed.add(api_settings.URL_FORMAT_OVERRIDE)
        allowed.update(getattr(view, "extra_query_params", ()))
        errors = {}
        for name in request.query_params:
            if name not in allowed:
                errors[name] = "Unknown query parameter."
            elif len(request.query_params.getlist(name)) != 1:
                errors[name] = "Supply this query parameter only once."
        if errors:
            raise ValidationError(errors)
        return super().filter_queryset(request, queryset, view)


class StableOrderingFilter(OrderingFilter):
    def get_valid_fields(self, queryset, view, context=None):
        fields = getattr(view, "ordering_fields", None)
        if fields == "__all__":
            raise ImproperlyConfigured("Declare an explicit ordering_fields allowlist.")
        if not fields:
            return []
        return super().get_valid_fields(queryset, view, context)

    def get_ordering(self, request, queryset, view):
        value = request.query_params.get(self.ordering_param)
        if value is not None:
            ordering = [field.strip() for field in value.split(",")]
            allowed = {field[0] for field in self.get_valid_fields(queryset, view)}
            if any(field.removeprefix("-") not in allowed for field in ordering):
                raise ValidationError({self.ordering_param: "Unsupported ordering field."})
        else:
            default = (
                self.get_default_ordering(view)
                or queryset.query.order_by
                or queryset.model._meta.ordering
                or ()
            )
            ordering = [default] if isinstance(default, str) else list(default)
        pk = queryset.model._meta.pk.name
        if not any(field.removeprefix("-") in {"pk", pk} for field in ordering):
            ordering.append(pk)
        return ordering
