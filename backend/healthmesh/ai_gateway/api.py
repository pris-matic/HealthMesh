from django.http import JsonResponse

from healthmesh.workflow.auth import api
from .service import get_amd_adapter


@api(['GET'])
def health(request):
    return JsonResponse(get_amd_adapter().health())
