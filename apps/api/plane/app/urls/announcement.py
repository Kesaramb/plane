# Fork addition: announcement banner routes.
from django.urls import path

from plane.app.views import (
    AnnouncementDismissEndpoint,
    AnnouncementListEndpoint,
    AnnouncementManageViewSet,
    AnnouncementReceiptEndpoint,
    AnnouncementVoteEndpoint,
)

urlpatterns = [
    path("announcements/", AnnouncementListEndpoint.as_view(), name="announcements"),
    path("announcements/<uuid:pk>/dismiss/", AnnouncementDismissEndpoint.as_view(), name="announcement-dismiss"),
    path(
        "announcements/<uuid:pk>/<str:action>/receipt/",
        AnnouncementReceiptEndpoint.as_view(),
        name="announcement-receipt",
    ),
    path("announcements/<uuid:pk>/vote/", AnnouncementVoteEndpoint.as_view(), name="announcement-vote"),
    path(
        "announcements/manage/",
        AnnouncementManageViewSet.as_view({"get": "list", "post": "create"}),
        name="announcement-manage",
    ),
    path(
        "announcements/manage/<uuid:pk>/email/",
        AnnouncementManageViewSet.as_view({"post": "email"}),
        name="announcement-email",
    ),
    path(
        "announcements/manage/<uuid:pk>/",
        AnnouncementManageViewSet.as_view({"patch": "partial_update", "delete": "destroy"}),
        name="announcement-manage-detail",
    ),
]
