from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

handler400 = "apps.core.api.errors.bad_request"
handler403 = "apps.core.api.errors.permission_denied"
handler404 = "apps.core.api.errors.not_found"
handler500 = "apps.core.api.errors.server_error"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.accounts.api.urls")),
    path("api/v1/files/", include("apps.files.api.urls")),
    path("api/v1/", include("apps.organizations.api.urls")),
    path("api/v1/", include("apps.tickets.api.urls")),
    path("api/v1/", include("apps.webhooks.api.urls")),
    path("api/v1/", include("apps.notifications.api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("health/", include("apps.core.health.urls")),
]

if settings.DEBUG:
    # User uploads are private even in development. Never expose MEDIA_ROOT.
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
