# Fork addition: announcement banner routes.
from django.urls import path

from plane.app.views import (
    AnnouncementDismissEndpoint,
    AnnouncementListEndpoint,
    AnnouncementManageViewSet,
)

urlpatterns = [
    path("announcements/", AnnouncementListEndpoint.as_view(), name="announcements"),
    path("announcements/<uuid:pk>/dismiss/", AnnouncementDismissEndpoint.as_view(), name="announcement-dismiss"),
    path(
        "announcements/manage/",
        AnnouncementManageViewSet.as_view({"get": "list", "post": "create"}),
        name="announcement-manage",
    ),
    path(
        "announcements/manage/<uuid:pk>/",
        AnnouncementManageViewSet.as_view({"patch": "partial_update", "delete": "destroy"}),
        name="announcement-manage-detail",
    ),
]
