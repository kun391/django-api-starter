from django.urls import path

from .views import (
    FileDetailView,
    FileDownloadURLView,
    FileDownloadView,
    FileReplaceView,
    FileUploadView,
)

urlpatterns = [
    path("", FileUploadView.as_view(), name="file-upload"),
    path("<uuid:file_id>/", FileDetailView.as_view(), name="file-detail"),
    path("<uuid:file_id>/download/", FileDownloadView.as_view(), name="file-download"),
    path("<uuid:file_id>/replace/", FileReplaceView.as_view(), name="file-replace"),
    path("<uuid:file_id>/download-url/", FileDownloadURLView.as_view(), name="file-download-url"),
]
