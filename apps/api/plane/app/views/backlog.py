# Fork addition: a personal, prioritized, gamified backlog ("Quest Board").
#
# Session-authed and same-origin, so any signed-in user reaches it with no extra
# auth. Returns the caller's own open assigned work across every workspace,
# scored so the top of the list is genuinely "what to do next", plus private RPG
# stats (XP, level, streak) derived from their completed work. Nothing here is
# comparative — a user only ever sees their own numbers.
import datetime
import json

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from plane.app.views.base import BaseAPIView
from plane.bgtasks.issue_activities_task import issue_activity
from plane.db.models import EstimatePoint, Issue, IssueAssignee, State

OPEN = ["backlog", "unstarted", "started"]

# how hard a still-open item shouts for attention
PRIORITY_SCORE = {"urgent": 100, "high": 60, "medium": 30, "low": 15, "none": 10}
# how much finishing one is worth
XP_BASE = {"urgent": 25, "high": 15, "medium": 10, "low": 6, "none": 5}


def _hours(issue):
    try:
        return float(issue.estimate_point.value) if issue.estimate_point_id else 0.0
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _score(issue, today):
    s = PRIORITY_SCORE.get(issue.priority, 10)
    if issue.target_date:
        late = (today - issue.target_date).days
        if late > 0:
            s += min(late * 4, 120)          # overdue dominates, but is capped
        elif late <= 3:
            s += 15                            # due within 3 days
    if issue.state and issue.state.group == "started":
        s += 20                                # already in progress: finish it
    age = (today - issue.created_at.date()).days
    s += min(age, 30) * 0.5                    # gentle nudge for stale items
    return round(s, 1)


def _xp_points(priority, hours):
    return XP_BASE.get(priority, 5) + int(hours)


def _level(xp):
    """Triangular curve: level N costs 80*(N-1) more than the last."""
    level, cum, step = 1, 0, 80
    while xp >= cum + level * step:
        cum += level * step
        level += 1
    return {
        "level": level,
        "xp_total": xp,
        "xp_into_level": xp - cum,
        "xp_for_next": level * step,
    }


def _streak(completed_dates):
    """Consecutive days ending today or yesterday with at least one completion."""
    if not completed_dates:
        return 0
    days = set(completed_dates)
    today = timezone.now().date()
    cursor = today if today in days else today - datetime.timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= datetime.timedelta(days=1)
    return streak


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timebox_choices(estimate_ids):
    """The hour values each estimate scale actually offers, with the point to write.

    Plane keeps the number on an EstimatePoint, not on the work item, so a timebox
    is only settable if the project's scale has a matching point. Fetched in one
    query and handed to the board, so it can offer real choices rather than let
    someone pick a value that cannot be saved.
    """
    by_estimate = {}
    for ep in EstimatePoint.objects.filter(estimate_id__in=estimate_ids):
        try:
            hours = float(ep.value)
        except (TypeError, ValueError):
            continue  # a categories/fibonacci scale that does not mean hours
        by_estimate.setdefault(ep.estimate_id, []).append({"hours": hours, "point": str(ep.id)})
    for options in by_estimate.values():
        options.sort(key=lambda o: o["hours"])
    return by_estimate


class MyBacklogEndpoint(BaseAPIView):
    """The signed-in user's scored backlog and RPG stats."""

    def get(self, request):
        user = request.user
        today = timezone.now().date()

        issue_ids = IssueAssignee.objects.filter(assignee=user).values_list("issue_id", flat=True)
        mine = (
            Issue.issue_objects.filter(id__in=issue_ids)
            .select_related("project", "state", "workspace", "estimate_point")
        )

        open_items = [i for i in mine if i.state and i.state.group in OPEN]
        choices = _timebox_choices({i.project.estimate_id for i in open_items if i.project.estimate_id})

        quests = []
        for i in open_items:
            hours = _hours(i)
            late = (today - i.target_date).days if i.target_date else None
            quests.append({
                "id": str(i.id),
                "ref": f"{i.project.identifier}-{i.sequence_id}",
                "title": i.name,
                "priority": i.priority or "none",
                "state": i.state.name,
                "workspace": i.workspace.slug,
                "workspace_name": i.workspace.name,
                "project": i.project.name,
                "due": str(i.target_date) if i.target_date else None,
                "overdue_days": late if (late and late > 0) else 0,
                "hours": hours,
                "timebox_options": choices.get(i.project.estimate_id, []),
                "reward_xp": _xp_points(i.priority or "none", hours),
                "score": _score(i, today),
                "url": f"/{i.workspace.slug}/projects/{i.project_id}/issues/{i.id}",
            })
        # Deep backlogs produce ties at the overdue cap, which would make the top of
        # the list arbitrary. Break ties toward quick wins: smaller estimates first,
        # then the earliest due date. Momentum beats an arbitrary order.
        quests.sort(key=lambda q: (-q["score"], q["hours"] or 99, q["due"] or "9999-99-99"))

        # XP + streak come from completed work (assigned to this user)
        done = (
            Issue.issue_objects.filter(id__in=issue_ids, state__group="completed", completed_at__isnull=False)
            .select_related("state", "estimate_point")
        )
        xp = 0
        completed_dates = []
        today_done = week_done = 0
        for i in done:
            xp += _xp_points(i.priority or "none", _hours(i))
            d = i.completed_at.date()
            completed_dates.append(d)
            if d == today:
                today_done += 1
            if d >= today - datetime.timedelta(days=6):
                week_done += 1

        stats = _level(xp)
        overdue = sum(1 for q in quests if q["overdue_days"])
        return Response({
            "player": user.display_name or user.email,
            **stats,
            "streak_days": _streak(completed_dates),
            "completed_today": today_done,
            "completed_this_week": week_done,
            "completed_all_time": len(completed_dates),
            "open_total": len(quests),
            "overdue_total": overdue,
            "boss": next((q for q in quests if q["overdue_days"]), None),
            "quests": quests,
        }, status=status.HTTP_200_OK)


class SetTimeboxEndpoint(BaseAPIView):
    """Put a timebox on one of the caller's OWN items, from the Quest Board.

    Work arrives assigned but unestimated — 1 of 110 open assigned items carried an
    estimate before this existed — because whoever assigns it rarely knows how long
    it will take. The person about to do it does. So the number gets set at the
    moment they pick the task up.

    It writes Plane's own estimate field rather than a private one, so the same
    number the board sorts by is the number the capacity dashboard counts against
    the 8h day. One number, set once, by the person best placed to judge it.
    """

    def post(self, request, issue_id):
        if not IssueAssignee.objects.filter(assignee=request.user, issue_id=issue_id).exists():
            return Response({"error": "not your item"}, status=status.HTTP_403_FORBIDDEN)
        issue = Issue.issue_objects.filter(id=issue_id).select_related("project").first()
        if not issue:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            hours = float(request.data.get("hours"))
        except (TypeError, ValueError):
            return Response({"error": "hours must be a number"}, status=status.HTTP_400_BAD_REQUEST)
        if not issue.project.estimate_id:
            return Response(
                {"error": "this project has no estimate scale to record a timebox on"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        point = next(
            (
                ep
                for ep in EstimatePoint.objects.filter(estimate_id=issue.project.estimate_id)
                if _as_float(ep.value) == hours
            ),
            None,
        )
        if not point:
            return Response(
                {"error": f"{hours:g}h is not on this project's scale"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        before = str(issue.estimate_point_id) if issue.estimate_point_id else None
        issue.estimate_point = point
        issue.save(update_fields=["estimate_point"])
        # goes through the normal activity pipeline, so the change shows in the item's
        # history with the right actor rather than appearing from nowhere
        issue_activity.delay(
            type="issue.activity.updated",
            requested_data=json.dumps({"estimate_point": str(point.id)}),
            actor_id=str(request.user.id),
            issue_id=str(issue.id),
            project_id=str(issue.project_id),
            current_instance=json.dumps({"estimate_point": before}),
            epoch=int(timezone.now().timestamp()),
            notification=False,
        )
        return Response(
            {"ok": True, "hours": hours, "reward_xp": _xp_points(issue.priority or "none", hours)},
            status=status.HTTP_200_OK,
        )


class CompleteQuestEndpoint(BaseAPIView):
    """Mark one of the caller's OWN items done, and grant its XP. Recorded in the
    item's history like any other completion."""

    def post(self, request, issue_id):
        if not IssueAssignee.objects.filter(assignee=request.user, issue_id=issue_id).exists():
            return Response({"error": "not your item"}, status=status.HTTP_403_FORBIDDEN)
        issue = Issue.issue_objects.filter(id=issue_id).select_related("project", "state", "estimate_point").first()
        if not issue:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        done_state = (
            State.objects.filter(project_id=issue.project_id, group="completed", default=True).first()
            or State.objects.filter(project_id=issue.project_id, group="completed").first()
        )
        if not done_state:
            return Response({"error": "this project has no Done state"}, status=status.HTTP_400_BAD_REQUEST)
        if issue.state_id == done_state.id:
            return Response({"error": "already done"}, status=status.HTTP_400_BAD_REQUEST)

        before = {"state_id": str(issue.state_id)}
        issue.state = done_state
        issue.completed_at = timezone.now()
        issue.save(update_fields=["state", "completed_at"])
        issue_activity.delay(
            type="issue.activity.updated",
            requested_data='{"state_id": "%s"}' % str(done_state.id),
            actor_id=str(request.user.id),
            issue_id=str(issue.id),
            project_id=str(issue.project_id),
            current_instance='{"state_id": "%s"}' % before["state_id"],
            epoch=int(timezone.now().timestamp()),
            notification=True,
        )
        return Response(
            {"ok": True, "reward_xp": _xp_points(issue.priority or "none", _hours(issue))},
            status=status.HTTP_200_OK,
        )
