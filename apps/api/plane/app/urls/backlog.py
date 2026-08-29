# Fork addition: personal gamified backlog ("Quest Board").
from django.urls import path

from plane.app.views import (
    CompleteQuestEndpoint,
    EndFocusEndpoint,
    MyBacklogEndpoint,
    PauseFocusEndpoint,
    SetTimeboxEndpoint,
    StartFocusEndpoint,
)

urlpatterns = [
    path("my-backlog/", MyBacklogEndpoint.as_view(), name="my-backlog"),
    path("my-backlog/<uuid:issue_id>/complete/", CompleteQuestEndpoint.as_view(), name="quest-complete"),
    path("my-backlog/<uuid:issue_id>/timebox/", SetTimeboxEndpoint.as_view(), name="quest-timebox"),
    path("my-backlog/<uuid:issue_id>/focus/", StartFocusEndpoint.as_view(), name="quest-focus-start"),
    path("my-backlog/focus/end/", EndFocusEndpoint.as_view(), name="quest-focus-end"),
    path("my-backlog/focus/pause/", PauseFocusEndpoint.as_view(), name="quest-focus-pause"),
]
