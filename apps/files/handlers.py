from uuid import UUID

from apps.core.outbox import outbox_handler

from .services import delete_stored_object


@outbox_handler("files.deletion-requested")
def on_deletion_requested(event):
    if event["version"] != 1:
        raise ValueError("Unsupported private file deletion event version.")
    delete_stored_object(UUID(event["payload"]["file_id"]))
