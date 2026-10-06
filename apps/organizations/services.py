from django.db import IntegrityError, transaction

from apps.core.outbox import record_outbox_event

from .access import require_roles, resolve_access
from .models import Organization, OrganizationMembership


class OrganizationSlugConflict(Exception):
    pass


class MembershipConflict(Exception):
    pass


def create_organization(*, actor, name: str, slug: str) -> Organization:
    try:
        with transaction.atomic():
            organization = Organization.objects.create(name=name, slug=slug)
            OrganizationMembership.objects.create(
                organization=organization,
                user=actor,
                role=OrganizationMembership.Role.OWNER,
            )
            record_outbox_event(
                topic="organizations.organization-created",
                payload={
                    "organization_id": str(organization.pk),
                    "owner_user_id": actor.pk,
                },
            )
    except IntegrityError as exc:
        raise OrganizationSlugConflict() from exc
    return organization


def rename_organization(*, actor, organization_id, name: str) -> Organization:
    with transaction.atomic():
        access = resolve_access(actor=actor, organization_id=organization_id)
        require_roles(access, OrganizationMembership.Role.OWNER)
        organization = Organization.objects.select_for_update().get(
            pk=access.organization.pk,
        )
        if organization.name != name:
            organization.name = name
            organization.save(update_fields=["name"])
            record_outbox_event(
                topic="organizations.organization-updated",
                payload={
                    "organization_id": str(organization.pk),
                    "changed_fields": ["name"],
                },
            )
        return organization


def add_member(*, actor, organization_id, user, role: str) -> OrganizationMembership:
    with transaction.atomic():
        access = resolve_access(actor=actor, organization_id=organization_id)
        require_roles(access, OrganizationMembership.Role.OWNER)
        if OrganizationMembership.objects.filter(
            organization_id=organization_id, user_id=user.pk
        ).exists():
            raise MembershipConflict("The user is already a member.")
        membership = OrganizationMembership.objects.create(
            organization_id=organization_id,
            user=user,
            role=role,
        )
        record_outbox_event(
            topic="organizations.membership-added",
            payload={
                "organization_id": str(organization_id),
                "user_id": user.pk,
                "role": role,
            },
        )
        return membership


def _locked_owner_ids(organization_id) -> list[int]:
    return list(
        OrganizationMembership.objects.select_for_update()
        .filter(
            organization_id=organization_id,
            role=OrganizationMembership.Role.OWNER,
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def change_member_role(*, actor, organization_id, user_id: int, role: str):
    with transaction.atomic():
        access = resolve_access(actor=actor, organization_id=organization_id)
        require_roles(access, OrganizationMembership.Role.OWNER)
        owner_ids = _locked_owner_ids(organization_id)
        membership = (
            OrganizationMembership.objects.select_for_update()
            .filter(organization_id=organization_id, user_id=user_id)
            .first()
        )
        if membership is None:
            raise MembershipConflict("Membership does not exist.")
        if membership.role == role:
            return membership
        if (
            membership.role == OrganizationMembership.Role.OWNER
            and role != OrganizationMembership.Role.OWNER
            and owner_ids == [membership.pk]
        ):
            raise MembershipConflict("An organization must retain at least one owner.")
        previous_role = membership.role
        membership.role = role
        membership.save(update_fields=["role"])
        record_outbox_event(
            topic="organizations.membership-updated",
            payload={
                "organization_id": str(organization_id),
                "user_id": user_id,
                "previous_role": previous_role,
                "role": role,
            },
        )
        return membership


def remove_member(*, actor, organization_id, user_id: int) -> None:
    with transaction.atomic():
        access = resolve_access(actor=actor, organization_id=organization_id)
        require_roles(access, OrganizationMembership.Role.OWNER)
        owner_ids = _locked_owner_ids(organization_id)
        membership = (
            OrganizationMembership.objects.select_for_update()
            .filter(organization_id=organization_id, user_id=user_id)
            .first()
        )
        if membership is None:
            return
        if (
            membership.role == OrganizationMembership.Role.OWNER
            and owner_ids == [membership.pk]
        ):
            raise MembershipConflict("An organization must retain at least one owner.")
        membership.delete()
        record_outbox_event(
            topic="organizations.membership-removed",
            payload={"organization_id": str(organization_id), "user_id": user_id},
        )
