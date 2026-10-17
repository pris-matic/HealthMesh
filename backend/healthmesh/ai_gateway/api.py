from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.http import JsonResponse

from healthmesh.workflow.auth import api, error
from .service import extract_source, get_amd_adapter


@api(['GET'])
def health(request):
    return JsonResponse(get_amd_adapter().health())


@api(['POST'], roles=('coordinator',))
def extract(request, source_id):
    try:
        result = extract_source(source_id, request.user)
    except ObjectDoesNotExist:
        return error('not_found', 'Source not found.', 404)
    except ValidationError as exc:
        return error('invalid_source', 'Source cannot be extracted.', 400,
                     getattr(exc, 'message_dict', {'source': ['Invalid source.']}))
    except PermissionDenied:
        return error('forbidden', 'An active coordinator is required.', 403)
    return JsonResponse({'source_id': str(source_id), **result.model_dump(mode='json')})
