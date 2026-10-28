# Fork addition: Plane sign-in for PMO's admin surfaces (the Claude connector and the
# manager dashboard), and for the member connector. See views/pmo_connector.py and
# views/pmo_member_connector.py.
#
# Not under any path containing "instances": Plane's session middleware switches to the
# god-mode admin cookie for those, and these views must see the ordinary app session.
# The connector/ paths are shared by both surfaces; they are named for the first user.
from django.urls import path

from plane.app.views.pmo_connector import (
    connector_gate,
    consent,
    dashboard_gate,
    denied,
    resume,
    sign_in,
)
from plane.app.views.pmo_member_connector import (
    member_approve,
    member_consent,
    member_denied,
    member_gate,
    member_landing,
    member_start,
    member_token,
)

urlpatterns = [
    path("pmo/connector/gate/", connector_gate, name="pmo-connector-gate"),
    path("pmo/connector/consent/", consent, name="pmo-connector-consent"),
    path("pmo/dashboard/gate/", dashboard_gate, name="pmo-dashboard-gate"),
    path("pmo/connector/sign-in/", sign_in, name="pmo-sign-in"),
    path("pmo/connector/resume/", resume, name="pmo-resume"),
    path("pmo/connector/denied/", denied, name="pmo-denied"),
    # member connector: see the flow at the top of views/pmo_member_connector.py
    path("pmo/member/gate/", member_gate, name="pmo-member-gate"),
    path("pmo/member/start/", member_start, name="pmo-member-start"),
    path("pmo/member/denied/", member_denied, name="pmo-member-denied"),
    path("pmo/member/approve/", member_approve, name="pmo-member-approve"),
    path("pmo/member/consent/", member_consent, name="pmo-member-consent"),
    path("pmo/member/landing/", member_landing, name="pmo-member-landing"),
    path("pmo/member/token/", member_token, name="pmo-member-token"),
]
