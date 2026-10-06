from django.core.files.storage import FileSystemStorage


class PrivateFileSystemStorage(FileSystemStorage):
    """Django-native filesystem storage without a public URL contract."""

    def __init__(self, *args, **kwargs):
        # UUID object names never get reused by replacement. Exact names also
        # let a durable upload manifest reconcile an ambiguous failed write.
        kwargs.update(allow_overwrite=True, file_permissions_mode=0o600, directory_permissions_mode=0o700)
        super().__init__(*args, **kwargs)

    def url(self, name):
        raise NotImplementedError("Private files must be downloaded through authorization.")
