from django.http import JsonResponse
from healthmesh.workflow.auth import api, body
from .fixture import load_scenario
from .service import create_incident


@api(['POST'], roles=('coordinator',))
def scenarios(request):
    data = body(request)
    if data.get('seed', 'storm-supply-01') != 'storm-supply-01' or set(data) - {'seed'}:
        raise ValueError('Choose the storm-supply-01 synthetic seed.')
    incident = create_incident(load_scenario('storm-supply-01'), request.user)
    return JsonResponse({'id': str(incident.id), 'seed': incident.scenario_id, 'revision': incident.revision, 'synthetic': True}, status=201)
