# Fork addition: Plane sign-in for the PMO Claude connector. See views/pmo_connector.py.
# Not under any path containing "instances": Plane's session middleware switches to the
# god-mode admin cookie for those, and these views must see the ordinary app session.
from django.urls import path

from plane.app.views.pmo_connector import (
    connector_denied,
    connector_gate,
    connector_resume,
    connector_sign_in,
)

urlpatterns = [
    path("pmo/connector/gate/", connector_gate, name="pmo-connector-gate"),
    path("pmo/connector/sign-in/", connector_sign_in, name="pmo-connector-sign-in"),
    path("pmo/connector/resume/", connector_resume, name="pmo-connector-resume"),
    path("pmo/connector/denied/", connector_denied, name="pmo-connector-denied"),
]
