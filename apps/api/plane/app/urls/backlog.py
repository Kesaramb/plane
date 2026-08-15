# Fork addition: personal gamified backlog ("Quest Board").
from django.urls import path

from plane.app.views import CompleteQuestEndpoint, MyBacklogEndpoint, SetTimeboxEndpoint

urlpatterns = [
    path("my-backlog/", MyBacklogEndpoint.as_view(), name="my-backlog"),
    path("my-backlog/<uuid:issue_id>/complete/", CompleteQuestEndpoint.as_view(), name="quest-complete"),
    path("my-backlog/<uuid:issue_id>/timebox/", SetTimeboxEndpoint.as_view(), name="quest-timebox"),
]
