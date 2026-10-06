from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.http import Http404

from .models import Organization, OrganizationMembership


@dataclass(frozen=True)
class OrganizationAccess:
    organization: Organization
    membership: OrganizationMembership

    @property
    def role(self) -> str:
        return self.membership.role

    @property
    def can_manage_tickets(self) -> bool:
        return self.role in {
            OrganizationMembership.Role.OWNER,
            OrganizationMembership.Role.ADMIN,
        }


def resolve_access(*, actor, organization_id) -> OrganizationAccess:
    if not actor.is_authenticated or not actor.is_active or actor.pk is None:
        raise Http404()
    membership = (
        OrganizationMembership.objects.select_related("organization")
        .filter(organization_id=organization_id, user_id=actor.pk)
        .first()
    )
    if membership is None:
        raise Http404()
    return OrganizationAccess(
        organization=membership.organization,
        membership=membership,
    )


def require_roles(access: OrganizationAccess, *roles: str) -> None:
    if access.role not in roles:
        raise PermissionDenied()
