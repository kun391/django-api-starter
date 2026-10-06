from rest_framework.routers import SimpleRouter

from .views import OrganizationTicketViewSet, TicketViewSet

personal_router = SimpleRouter()
personal_router.register("tickets", TicketViewSet, basename="ticket")

organization_router = SimpleRouter()
organization_router.register(
    r"organizations/(?P<organization_id>[0-9a-f-]{36})/tickets",
    OrganizationTicketViewSet,
    basename="organization-ticket",
)

urlpatterns = [*personal_router.urls, *organization_router.urls]
