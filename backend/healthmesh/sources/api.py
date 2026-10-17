from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError, RequestDataTooBig, TooManyFilesSent, TooManyFieldsSent
from django.http import JsonResponse
from django.http.multipartparser import MultiPartParserError

from healthmesh.workflow.auth import api, error
from .parsers import SourceTooLarge
from .service import ingest_source


class SourceUploadLimitsMiddleware:
    """Keep multipart-limit failures in the API envelope before CSRF reads POST."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/api/v1/sources' and request.method == 'POST':
            try:
                if int(request.META.get('CONTENT_LENGTH') or 0) > settings.SOURCE_MAX_BYTES + 65536:
                    raise RequestDataTooBig
                # Force Django's bounded parser here; CSRF uses the cached result.
                request.POST
            except (RequestDataTooBig, TooManyFilesSent, TooManyFieldsSent):
                return error('source_too_large', 'The source upload exceeds the allowed limits.', 413,
                             {'file': ['Use one UTF-8 file of at most 1 MiB.']})
            except (MultiPartParserError, ValueError):
                return error('invalid_source', 'Invalid source upload.', 400, {'file': ['Invalid multipart upload.']})
        return self.get_response(request)


@api(['POST'], roles=('coordinator',))
def sources(request):
    try:
        if (set(request.POST) != {'incident_id', 'facility_id'} or set(request.FILES) != {'file'}
                or any(len(request.POST.getlist(key)) != 1 for key in request.POST)
                or len(request.FILES.getlist('file')) != 1):
            raise ValidationError({'file': 'Send incident_id, facility_id and one CSV or text file.'})
        uploaded = request.FILES['file']
        if uploaded.size > settings.SOURCE_MAX_BYTES:
            raise SourceTooLarge({'file': 'The source exceeds the 1 MiB upload limit.'})
        source = ingest_source(request.POST['incident_id'], request.POST['facility_id'],
                               uploaded.name, uploaded.read(settings.SOURCE_MAX_BYTES + 1), request.user)
    except (SourceTooLarge, RequestDataTooBig, TooManyFilesSent, TooManyFieldsSent):
        return error('source_too_large', 'The source upload exceeds the allowed limits.', 413,
                     {'file': ['Use one UTF-8 file of at most 1 MiB.']})
    except (ValidationError, MultiPartParserError) as exc:
        fields = getattr(exc, 'message_dict', {'file': ['Invalid source upload.']})
        return error('invalid_source', 'Source rejected. Check the file and upload scope.', 400, fields)
    except ObjectDoesNotExist:
        return error('not_found', 'Incident or facility not found in this scope.', 404, {'scope': ['Choose a facility in this incident.']})
    except PermissionDenied:
        return error('forbidden', 'An active coordinator is required.', 403)
    return JsonResponse({'id': str(source.id), 'sha256': source.sha256, 'kind': source.kind,
                         'observed_at': source.observed_at.isoformat(), 'duplicate': source.duplicate},
                        status=200 if source.duplicate else 201)
