"""Bounded, explicit page-number pagination using the native DRF envelope."""

import re

from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    last_page_strings = ()

    def _integer(self, request, name, default, maximum=None):
        values = request.query_params.getlist(name)
        if not values:
            return default
        if len(values) != 1 or not re.fullmatch(r"[0-9]{1,10}", values[0]):
            raise ValidationError({name: "Expected a single positive integer."})
        value = int(values[0])
        if value < 1 or (maximum is not None and value > maximum):
            message = f"Must be between 1 and {maximum}." if maximum else "Must be at least 1."
            raise ValidationError({name: message})
        return value

    def get_page_number(self, request, paginator):
        return self._integer(request, self.page_query_param, 1)

    def get_page_size(self, request):
        return self._integer(
            request, self.page_size_query_param, self.page_size, self.max_page_size
        )

    def get_schema_operation_parameters(self, view):
        parameters = super().get_schema_operation_parameters(view)
        for parameter in parameters:
            parameter["schema"]["minimum"] = 1
            if parameter["name"] == self.page_size_query_param:
                parameter["schema"].update(default=self.page_size, maximum=self.max_page_size)
            else:
                parameter["schema"]["default"] = 1
        return parameters
