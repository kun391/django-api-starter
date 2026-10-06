from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    OrganizationMemberDetailView,
    OrganizationMembersView,
    OrganizationViewSet,
)

router = SimpleRouter()
router.register("organizations", OrganizationViewSet, basename="organization")

urlpatterns = [
    *router.urls,
    path(
        "organizations/<uuid:organization_id>/members/",
        OrganizationMembersView.as_view(),
        name="organization-member-list",
    ),
    path(
        "organizations/<uuid:organization_id>/members/<int:user_id>/",
        OrganizationMemberDetailView.as_view(),
        name="organization-member-detail",
    ),
]
