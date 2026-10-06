from apps.core.outbox import outbox_handler


@outbox_handler("tickets.ticket-created")
def handle_ticket_created(_envelope):
    return None


@outbox_handler("tickets.ticket-updated")
def handle_ticket_updated(_envelope):
    return None


@outbox_handler("tickets.attachment-added")
def handle_attachment_added(_envelope):
    return None
