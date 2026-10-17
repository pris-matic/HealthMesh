from django.urls import path
from healthmesh.workflow.auth import session
from healthmesh.scenarios.api import scenarios
from healthmesh.planner.api import request_plan, get_plan

urlpatterns = [
    path('api/v1/session', session),
    path('api/v1/scenarios', scenarios),
    path('api/v1/incidents/<uuid:incident_id>/plan', request_plan),
    path('api/v1/incidents/<uuid:incident_id>/plan-runs/<uuid:run_id>', get_plan),
]
