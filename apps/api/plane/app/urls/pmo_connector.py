# Fork addition: Plane sign-in for PMO's admin surfaces (the Claude connector and the
# manager dashboard). See views/pmo_connector.py.
#
# Not under any path containing "instances": Plane's session middleware switches to the
# god-mode admin cookie for those, and these views must see the ordinary app session.
# The connector/ paths are shared by both surfaces; they are named for the first user.
from django.urls import path

from plane.app.views.pmo_connector import (
    connector_gate,
    dashboard_gate,
    denied,
    resume,
    sign_in,
)

urlpatterns = [
    path("pmo/connector/gate/", connector_gate, name="pmo-connector-gate"),
    path("pmo/dashboard/gate/", dashboard_gate, name="pmo-dashboard-gate"),
    path("pmo/connector/sign-in/", sign_in, name="pmo-sign-in"),
    path("pmo/connector/resume/", resume, name="pmo-resume"),
    path("pmo/connector/denied/", denied, name="pmo-denied"),
]
