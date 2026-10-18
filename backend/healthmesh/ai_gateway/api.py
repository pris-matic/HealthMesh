from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.http import JsonResponse

from healthmesh.workflow.auth import api, error
from .service import extract_source, get_amd_adapter, explain_conflict as explain_conflict_service, draft_verification as draft_verification_service
from .grounding import GroundingUnavailable


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


@api(['POST'], roles=('coordinator',))
def explain_conflict(request, conflict_id):
    try:
        result = explain_conflict_service(conflict_id, request.user)
    except ObjectDoesNotExist:
        return error('not_found', 'Conflict not found.', 404)
    except GroundingUnavailable:
        return error('grounding_unavailable', 'Conflict lacks two issued source spans.', 409)
    return JsonResponse({'conflict_id': str(conflict_id), **result})


@api(['POST'], roles=('coordinator',))
def draft_verification(request, task_id):
    try:
        result = draft_verification_service(task_id, request.user)
    except ObjectDoesNotExist:
        return error('not_found', 'Selected task not found.', 404)
    except GroundingUnavailable:
        return error('grounding_unavailable', 'Selected task lacks issued source spans.', 409)
    return JsonResponse({'task_id': str(task_id), **result})
