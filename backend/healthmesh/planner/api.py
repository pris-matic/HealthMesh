from django.db import transaction
from django.http import JsonResponse
from healthmesh.domain.snapshot import build_snapshot
from healthmesh.models import Incident, PlanRun, PlanProposal
from healthmesh.workflow.auth import api, error
from .service import plan


def serialize(run):
    result = dict(run.result)
    result.update(id=str(run.id), created_at=run.created_at.isoformat(), as_of=run.input_snapshot['as_of'])
    result['branches'] = [dict(p.branch, id=str(p.id)) for p in run.proposals.order_by('position')]
    return result


@api(['POST'], roles=('coordinator',))
@transaction.atomic
def request_plan(request, incident_id):
    if not Incident.objects.filter(pk=incident_id).exists():
        return error('not_found', 'Incident not found.', 404)
    snapshot = build_snapshot(incident_id)
    result = plan(snapshot)
    run = PlanRun.objects.create(incident_id=incident_id, created_by=request.user,
        revision=snapshot.revision, snapshot_id=result.snapshot_id,
        input_snapshot=snapshot.model_dump(mode='json'), result=result.model_dump(mode='json'))
    for position, branch in enumerate(result.branches):
        PlanProposal.objects.create(run=run, position=position, branch=branch.model_dump(mode='json'))
    return JsonResponse(serialize(run), status=201)


@api(['GET'])
def get_plan(request, incident_id, run_id):
    run = PlanRun.objects.filter(pk=run_id, incident_id=incident_id).first()
    if run is None:
        return error('not_found', 'Plan run not found.', 404)
    return JsonResponse(serialize(run))
