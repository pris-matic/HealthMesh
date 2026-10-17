from django.urls import path
from healthmesh.workflow.auth import session, api_not_found
from healthmesh.scenarios.api import scenarios
from healthmesh.planner.api import request_plan, get_plan
from healthmesh.sources.api import sources
from healthmesh.ai_gateway.api import extract as amd_extract, health as amd_health

urlpatterns = [
    path('api/v1/amd/health', amd_health),
    path('api/v1/sources', sources),
    path('api/v1/sources/<uuid:source_id>/extract', amd_extract),
    path('api/v1/session', session),
    path('api/v1/scenarios', scenarios),
    path('api/v1/incidents/<uuid:incident_id>/plan', request_plan),
    path('api/v1/incidents/<uuid:incident_id>/plan-runs/<uuid:run_id>', get_plan),
    path('api/v1/<path:unmatched_path>', api_not_found),
]
