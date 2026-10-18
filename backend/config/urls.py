from django.urls import path
from healthmesh.workflow.auth import session, api_not_found
from healthmesh.scenarios.api import scenarios
from healthmesh.planner.api import request_plan, get_plan
from healthmesh.sources.api import sources
from healthmesh.ai_gateway.api import (extract as amd_extract, health as amd_health,
                                       explain_conflict, draft_verification)
from healthmesh.evidence.api import evidence, verification_tasks
from healthmesh.workflow.api import respond, review_claim, resolve_conflict

urlpatterns = [
    path('api/v1/amd/health', amd_health),
    path('api/v1/sources', sources),
    path('api/v1/sources/<uuid:source_id>/extract', amd_extract),
    path('api/v1/conflicts/<uuid:conflict_id>/explain', explain_conflict),
    path('api/v1/conflicts/<uuid:conflict_id>/resolve', resolve_conflict),
    path('api/v1/verification-tasks/<uuid:task_id>/draft', draft_verification),
    path('api/v1/verification-tasks/<uuid:task_id>/respond', respond),
    path('api/v1/claims/<uuid:claim_id>/review', review_claim),
    path('api/v1/session', session),
    path('api/v1/scenarios', scenarios),
    path('api/v1/incidents/<uuid:incident_id>/plan', request_plan),
    path('api/v1/incidents/<uuid:incident_id>/evidence', evidence),
    path('api/v1/incidents/<uuid:incident_id>/verification-tasks', verification_tasks),
    path('api/v1/incidents/<uuid:incident_id>/plan-runs/<uuid:run_id>', get_plan),
    path('api/v1/<path:unmatched_path>', api_not_found),
]
