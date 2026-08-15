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
    AnnouncementReceipt,
    AnnouncementVote,
    ProjectMember,
    TeamMember,
    User,
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
        Q(audience=AnnouncementAudience.WORKSPACE, workspace_id__in=workspace_ids)
        | Q(audience=AnnouncementAudience.PROJECT, project_id__in=project_ids)
        | Q(audience=AnnouncementAudience.TEAM, team_id__in=team_ids)
        # a team's lead sees their own team's announcements even if they were
        # never added as an explicit member
        | Q(audience=AnnouncementAudience.TEAM, team__lead=user)
    )
    # "Everyone on the instance" still means everyone who actually works here:
    # an active member (not a guest) of at least one workspace, and not a bot.
    if not getattr(user, "is_bot", False) and WorkspaceMember.objects.filter(
        member=user, is_active=True, role__gte=MIN_ROLE
    ).exists():
        audience |= Q(audience=AnnouncementAudience.INSTANCE)

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


def audience_users(announcement):
    """Every user this announcement targets — the inverse of visible_announcements().

    Kept adjacent to that function on purpose: if one changes, the other must.
    """
    a = announcement
    if a.audience == AnnouncementAudience.INSTANCE:
        member_ids = WorkspaceMember.objects.filter(is_active=True, role__gte=MIN_ROLE).values_list(
            "member_id", flat=True
        )
        return User.objects.filter(id__in=member_ids, is_active=True, is_bot=False).distinct()
    if a.audience == AnnouncementAudience.WORKSPACE:
        ids = WorkspaceMember.objects.filter(
            workspace_id=a.workspace_id, is_active=True, role__gte=MIN_ROLE
        ).values_list("member_id", flat=True)
        return User.objects.filter(id__in=ids, is_active=True, is_bot=False).distinct()
    if a.audience == AnnouncementAudience.PROJECT:
        ids = ProjectMember.objects.filter(
            project_id=a.project_id, is_active=True, role__gte=MIN_ROLE
        ).values_list("member_id", flat=True)
        return User.objects.filter(id__in=ids, is_active=True, is_bot=False).distinct()
    # team: explicit members plus the lead, who is not necessarily a member row
    ids = set(
        TeamMember.objects.filter(team_id=a.team_id, deleted_at__isnull=True).values_list("member_id", flat=True)
    )
    if a.team and a.team.lead_id:
        ids.add(a.team.lead_id)
    return User.objects.filter(id__in=ids, is_active=True, is_bot=False).distinct()


def engagement_for(announcement):
    """Reach and engagement counts for the dashboard."""
    audience = audience_users(announcement)
    total = audience.count()
    receipts = AnnouncementReceipt.objects.filter(announcement=announcement, deleted_at__isnull=True)
    seen_ids = set(receipts.filter(seen_count__gt=0).values_list("user_id", flat=True))
    clicked = receipts.filter(clicked_at__isnull=False).count()
    emailed = receipts.filter(emailed_at__isnull=False).count()
    dismissed = AnnouncementDismissal.objects.filter(
        announcement=announcement, deleted_at__isnull=True
    ).count()
    not_seen = [u.display_name or u.email for u in audience.exclude(id__in=seen_ids)[:25]]
    # one query for every vote, grouped in Python — not one query per option
    votes = list(
        AnnouncementVote.objects.filter(announcement=announcement, deleted_at__isnull=True)
        .select_related("user")
        .values_list("choice_index", "user__display_name", "user__email", "user_id")
    )
    by_choice = {}
    voter_ids = set()
    for idx, display_name, email, uid in votes:
        by_choice.setdefault(idx, []).append(display_name or email)
        voter_ids.add(uid)
    tally = []
    for idx, label in enumerate(announcement.poll_options or []):
        names = sorted(by_choice.get(idx, []), key=str.lower)
        tally.append({"index": idx, "label": label, "votes": len(names), "voters": names})
    total_votes = sum(t["votes"] for t in tally)
    for t in tally:
        t["pct"] = round(100 * t["votes"] / total_votes) if total_votes else 0
    not_voted = (
        [u.display_name or u.email for u in audience.exclude(id__in=voter_ids)[:25]]
        if announcement.poll_options
        else []
    )

    return {
        "audience_size": total,
        "poll_question": announcement.poll_question,
        "votes_total": total_votes,
        "tally": tally,
        "seen": len(seen_ids),
        "seen_pct": round(100 * len(seen_ids) / total) if total else 0,
        "clicked": clicked,
        "emailed": emailed,
        "dismissed": dismissed,
        "not_seen_sample": not_seen,
        "not_voted_sample": not_voted,
    }


def _queue_announcement_email(announcement, request):
    from plane.bgtasks.announcement_email_task import send_announcement_email

    site = request.build_absolute_uri("/").rstrip("/")
    send_announcement_email.delay(str(announcement.id), site)


class AnnouncementReceiptEndpoint(BaseAPIView):
    """Telemetry from the banner: it was actually rendered, or its link was clicked."""

    def post(self, request, pk, action):
        if action not in ("seen", "clicked"):
            return Response({"error": "unknown action"}, status=status.HTTP_400_BAD_REQUEST)
        if not Announcement.objects.filter(pk=pk).exists():
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        now = timezone.now()
        receipt, _ = AnnouncementReceipt.objects.get_or_create(
            announcement_id=pk, user=request.user, deleted_at__isnull=True
        )
        if action == "seen":
            receipt.seen_count = (receipt.seen_count or 0) + 1
            receipt.last_seen_at = now
            if not receipt.first_seen_at:
                receipt.first_seen_at = now
            receipt.save(update_fields=["seen_count", "last_seen_at", "first_seen_at"])
        else:
            if not receipt.clicked_at:
                receipt.clicked_at = now
                receipt.save(update_fields=["clicked_at"])
        return Response({"ok": True}, status=status.HTTP_200_OK)


class AnnouncementVoteEndpoint(BaseAPIView):
    """Cast or change a vote. Restricted to announcements visible to the caller,
    so the audience rules also gate who can influence the result."""

    def post(self, request, pk):
        announcement = visible_announcements(request.user).filter(pk=pk).first()
        if not announcement:
            # already dismissed but still voting from an open tab: allow it if the
            # announcement targets them at all, otherwise refuse
            candidate = Announcement.objects.filter(pk=pk, is_enabled=True).first()
            if not candidate or not audience_users(candidate).filter(pk=request.user.id).exists():
                return Response({"error": "Not available to you"}, status=status.HTTP_404_NOT_FOUND)
            announcement = candidate
        options = announcement.poll_options or []
        try:
            idx = int(request.data.get("choice_index"))
        except (TypeError, ValueError):
            return Response({"error": "choice_index required"}, status=status.HTTP_400_BAD_REQUEST)
        if idx < 0 or idx >= len(options):
            return Response({"error": "choice_index out of range"}, status=status.HTTP_400_BAD_REQUEST)

        AnnouncementVote.objects.update_or_create(
            announcement=announcement,
            user=request.user,
            deleted_at__isnull=True,
            defaults={"choice_index": idx, "choice_label": str(options[idx])[:60]},
        )
        payload = {"ok": True, "your_choice": idx}
        if announcement.poll_show_results:
            votes = AnnouncementVote.objects.filter(announcement=announcement, deleted_at__isnull=True)
            tally = [votes.filter(choice_index=i).count() for i in range(len(options))]
            payload["tally"] = tally
            payload["total"] = sum(tally)
        return Response(payload, status=status.HTTP_200_OK)


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

    def list(self, request):
        rows = []
        for a in self.get_queryset()[:50]:
            data = AnnouncementWriteSerializer(a).data
            data["engagement"] = engagement_for(a)
            data["author_name"] = a.author.display_name if a.author else ""
            rows.append(data)
        return Response(rows, status=status.HTTP_200_OK)

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
        if str(request.data.get("send_email", "")).lower() in ("1", "true", "yes"):
            _queue_announcement_email(serializer.instance, request)
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

    def email(self, request, pk):
        """Send (or re-send) this announcement by email to its audience."""
        announcement = Announcement.objects.filter(pk=pk).first()
        if not announcement:
            return Response({"error": "Announcement not found"}, status=status.HTTP_404_NOT_FOUND)
        if not self._may_write(request, announcement.audience, announcement.workspace_id):
            return Response({"error": "You do not have permission"}, status=status.HTTP_403_FORBIDDEN)
        count = audience_users(announcement).exclude(email="").count()
        _queue_announcement_email(announcement, request)
        return Response({"ok": True, "queued_for": count}, status=status.HTTP_200_OK)

    def destroy(self, request, pk):
        announcement = Announcement.objects.filter(pk=pk).first()
        if not announcement:
            return Response({"error": "Announcement not found"}, status=status.HTTP_404_NOT_FOUND)
        if not self._may_write(request, announcement.audience, announcement.workspace_id):
            return Response({"error": "You do not have permission"}, status=status.HTTP_403_FORBIDDEN)
        announcement.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
