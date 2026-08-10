# Fork addition: create an announcement / meeting as a real work item and
# notify the chosen recipients via Plane's in-app notification inbox.
#
# Usage (invoked by the dashboard admin API):
#   python manage.py send_announcement --payload '<json>'
#
# payload = {
#   "workspace": "slug",
#   "project_id": "uuid",              # host project for the work item
#   "title": "Team meeting — Fri 3pm",
#   "message": "Agenda: ...",          # plain text; stored as html + stripped
#   "date": "2026-08-15",              # optional target_date (meeting day)
#   "sender": "PMO",                   # label shown as the notification sender
#   "triggered_by": "uuid|null",       # user id of the sender (optional)
#   "recipients": {"mode": "users|project|team",
#                  "user_ids": [...], "project_id": "uuid", "team_id": "uuid"}
# }
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Create an announcement/meeting work item and notify recipients (in-app)."

    def add_arguments(self, parser):
        parser.add_argument("--payload", required=True, help="JSON payload")

    def handle(self, *args, **options):
        from plane.db.models import (
            Workspace, Project, ProjectMember, State, Issue, IssueAssignee,
            Notification, TeamMember, User,
        )

        p = json.loads(options["payload"])
        try:
            ws = Workspace.objects.get(slug=p["workspace"])
            project = Project.objects.get(id=p["project_id"], workspace=ws)
        except (Workspace.DoesNotExist, Project.DoesNotExist):
            raise CommandError("invalid workspace or project")

        title = (p.get("title") or "").strip()
        if not title:
            raise CommandError("title required")
        message = (p.get("message") or "").strip()
        sender = (p.get("sender") or "Announcement").strip()

        # Resolve recipients -> set of user ids
        rc = p.get("recipients", {})
        mode = rc.get("mode", "users")
        if mode == "project":
            ids = ProjectMember.objects.filter(
                project_id=rc.get("project_id") or project.id, is_active=True
            ).values_list("member_id", flat=True)
        elif mode == "team":
            ids = TeamMember.objects.filter(
                team_id=rc.get("team_id"), deleted_at__isnull=True
            ).values_list("member_id", flat=True)
        else:
            ids = rc.get("user_ids", [])
        recipient_ids = list(dict.fromkeys(str(i) for i in ids))
        if not recipient_ids:
            raise CommandError("no recipients resolved")

        # A sensible default state for the host project (unstarted, else any)
        state = (
            State.objects.filter(project=project, group="unstarted").first()
            or State.objects.filter(project=project, group="backlog").first()
            or State.objects.filter(project=project).first()
        )

        triggered_by = None
        if p.get("triggered_by"):
            triggered_by = User.objects.filter(id=p["triggered_by"]).first()

        # The meeting/announcement as a real, clickable work item (marked with 📢)
        issue = Issue.objects.create(
            name=f"📢 {title}",
            description_html=f"<p>{message}</p>" if message else "<p></p>",
            project=project,
            workspace=ws,
            state=state,
            priority=p.get("priority", "high"),
            target_date=p.get("date") or None,
            created_by=triggered_by,
        )

        assignees = list(User.objects.filter(id__in=recipient_ids))
        IssueAssignee.objects.bulk_create(
            [IssueAssignee(issue=issue, assignee=u, project=project, workspace=ws) for u in assignees],
            ignore_conflicts=True,
        )

        # In-app notification per recipient (entity_name="issue" so Plane's inbox shows it)
        msg_html = f"<p><b>{title}</b></p>" + (f"<p>{message}</p>" if message else "")
        if p.get("date"):
            msg_html += f"<p>🗓 {p['date']}</p>"
        notifs = [
            Notification(
                workspace=ws,
                project=project,
                entity_identifier=issue.id,
                entity_name="issue",
                title=title,
                message_html=msg_html,
                message_stripped=message or title,
                sender=sender,
                triggered_by=triggered_by,
                receiver=u,
            )
            for u in assignees
        ]
        Notification.objects.bulk_create(notifs)

        self.stdout.write(json.dumps({
            "ok": True,
            "issue_id": str(issue.id),
            "sequence_id": issue.sequence_id,
            "project_identifier": project.identifier,
            "recipients": len(assignees),
            "at": timezone.now().isoformat(),
        }))
