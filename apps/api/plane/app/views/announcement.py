# Fork addition: read/dismiss/manage endpoints for the announcement banner.
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, When
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from plane.app.permissions import ROLE
from plane.app.serializers.announcement import AnnouncementSerializer, AnnouncementWriteSerializer
from plane.app.views.base import BaseAPIView, BaseViewSet
from plane.db.models import (
    Announcement,
    AnnouncementAudience,
    AnnouncementDismissal,
    ProjectMember,
    TeamMember,
    WorkspaceMember,
)
from plane.license.models import InstanceAdmin

# Guests (role 5) are deliberately excluded — a leadership announcement should not
# reach an external collaborator. Members and admins only.
MIN_ROLE = ROLE.MEMBER.value
MAX_BANNERS = 3


def visible_announcements(user):
    """Announcements this user should currently see, most severe first."""
    now = timezone.now()

    workspace_ids = WorkspaceMember.objects.filter(
        member=user, is_active=True, role__gte=MIN_ROLE
    ).values_list("workspace_id", flat=True)
    project_ids = ProjectMember.objects.filter(
        member=user, is_active=True, role__gte=MIN_ROLE
    ).values_list("project_id", flat=True)
    team_ids = TeamMember.objects.filter(member=user, deleted_at__isnull=True).values_list("team_id", flat=True)

    audience = (
        Q(audience=AnnouncementAudience.INSTANCE)
        | Q(audience=AnnouncementAudience.WORKSPACE, workspace_id__in=workspace_ids)
        | Q(audience=AnnouncementAudience.PROJECT, project_id__in=project_ids)
        | Q(audience=AnnouncementAudience.TEAM, team_id__in=team_ids)
        # a team's lead sees their own team's announcements even if they were
        # never added as an explicit member
        | Q(audience=AnnouncementAudience.TEAM, team__lead=user)
    )

    # A dismissal only silences the revision it was made against, so bumping the
    # revision brings a corrected announcement back.
    dismissed = AnnouncementDismissal.objects.filter(
        announcement_id=OuterRef("pk"),
        user=user,
        deleted_at__isnull=True,
        revision__gte=OuterRef("revision"),
    )

    return (
        Announcement.objects.filter(is_enabled=True)
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .filter(audience)
        .annotate(is_dismissed=Exists(dismissed))
        .filter(is_dismissed=False)
        # never order by the level CharField: alphabetically critical < info < warning
        .annotate(
            severity=Case(
                When(level="critical", then=0),
                When(level="warning", then=1),
                default=2,
                output_field=IntegerField(),
            )
        )
        .order_by("severity", "-created_at")
        .distinct()
    )


class AnnouncementListEndpoint(BaseAPIView):
    """Banners for the signed-in user. Anonymous callers get 401, which the
    injected script treats as 'render nothing'."""

    def get(self, request):
        announcements = visible_announcements(request.user)[:MAX_BANNERS]
        return Response(AnnouncementSerializer(announcements, many=True).data, status=status.HTTP_200_OK)


class AnnouncementDismissEndpoint(BaseAPIView):
    def post(self, request, pk):
        announcement = Announcement.objects.filter(pk=pk, is_dismissible=True).first()
        if not announcement:
            return Response({"error": "Announcement not found or not dismissible"}, status=status.HTTP_404_NOT_FOUND)
        AnnouncementDismissal.objects.update_or_create(
            announcement=announcement,
            user=request.user,
            deleted_at__isnull=True,
            defaults={"revision": announcement.revision},
        )
        return Response({"dismissed": True}, status=status.HTTP_200_OK)


class AnnouncementManageViewSet(BaseViewSet):
    """Authoring. Instance-wide banners require an instance admin; the scoped
    ones require workspace admin on the target."""

    model = Announcement
    serializer_class = AnnouncementWriteSerializer

    def get_queryset(self):
        return Announcement.objects.all().order_by("-created_at")

    def _may_write(self, request, audience, workspace_id):
        if InstanceAdmin.objects.filter(user=request.user).exists():
            return True
        if audience == AnnouncementAudience.INSTANCE:
            return False
        return WorkspaceMember.objects.filter(
            member=request.user, workspace_id=workspace_id, is_active=True, role=ROLE.ADMIN.value
        ).exists()

    def create(self, request):
        serializer = AnnouncementWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = serializer.resolved_workspace_id()
        if not self._may_write(request, serializer.validated_data.get("audience"), target):
            return Response(
                {"error": "You do not have permission to publish this announcement"},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer.save(author=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk):
        announcement = Announcement.objects.filter(pk=pk).first()
        if not announcement:
            return Response({"error": "Announcement not found"}, status=status.HTTP_404_NOT_FOUND)
        if not self._may_write(request, announcement.audience, announcement.workspace_id):
            return Response({"error": "You do not have permission"}, status=status.HTTP_403_FORBIDDEN)
        serializer = AnnouncementWriteSerializer(announcement, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # opt-in: bring this back for people who already dismissed it
        if str(request.data.get("reshow", "")).lower() in ("1", "true", "yes"):
            serializer.save(revision=announcement.revision + 1)
        else:
            serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def destroy(self, request, pk):
        announcement = Announcement.objects.filter(pk=pk).first()
        if not announcement:
            return Response({"error": "Announcement not found"}, status=status.HTTP_404_NOT_FOUND)
        if not self._may_write(request, announcement.audience, announcement.workspace_id):
            return Response({"error": "You do not have permission"}, status=status.HTTP_403_FORBIDDEN)
        announcement.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
