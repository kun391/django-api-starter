from apps.core.outbox import outbox_handler


@outbox_handler("organizations.organization-created")
def handle_organization_created(_envelope):
    return None


@outbox_handler("organizations.organization-updated")
def handle_organization_updated(_envelope):
    return None


@outbox_handler("organizations.membership-added")
def handle_membership_added(_envelope):
    return None


@outbox_handler("organizations.membership-updated")
def handle_membership_updated(_envelope):
    return None


@outbox_handler("organizations.membership-removed")
def handle_membership_removed(_envelope):
    return None
