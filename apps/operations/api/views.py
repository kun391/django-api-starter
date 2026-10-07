from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.operations.services import snapshot_dict

from .serializers import OperationsSnapshotSerializer


@extend_schema(
    tags=["operations"],
    responses={200: OperationsSnapshotSerializer},
)
class QueueOperationsView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        response = Response(snapshot_dict())
        response["Cache-Control"] = "no-store"
        return response
