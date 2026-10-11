from uuid import UUID

from .services import delete_stored_object


def on_deletion_requested(event):
    if event["version"] != 1:
        raise ValueError("Unsupported private file deletion event version.")
    delete_stored_object(UUID(event["payload"]["file_id"]))
