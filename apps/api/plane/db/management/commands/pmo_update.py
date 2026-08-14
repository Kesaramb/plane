# Fork addition: edit an existing work item, and add comments, from outside the
# web app (the dashboard MCP uses this).
#
#   python manage.py pmo_update --kind work_item --payload '<json>'
#   python manage.py pmo_update --kind comment   --payload '<json>'
#
# work_item { ref | (workspace, project, sequence_id), state?, assignees?[],
#             add_assignees?[], priority?, name?, description?, start_date?,
#             target_date?, estimate_hours?, cycle?, actor? }
# comment   { ref, comment, actor? }
#
# Changes are written through the ORM and then announced with Plane's own
# issue_activity task, so an edit made here shows up in the item's history and
# notifies subscribers exactly like an edit made in the UI.
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Update a work item, or comment on it."

    def add_arguments(self, parser):
        parser.add_argument("--kind", required=True, choices=["work_item", "comment"])
        parser.add_argument("--payload", required=True)

    def handle(self, *args, **options):
        payload = json.loads(options["payload"])
        self.stdout.write(json.dumps(getattr(self, f"_{options['kind']}")(payload)))

    # ------------------------------------------------------------------ helpers
    def _actor(self, workspace, hint=None):
        from plane.db.models import User, WorkspaceMember

        if hint:
            user = User.objects.filter(email__iexact=hint).first() or User.objects.filter(
                display_name__iexact=hint
            ).first()
            if user:
                return user
        member = (
            WorkspaceMember.objects.filter(workspace=workspace, is_active=True, role=20)
            .order_by("created_at")
            .first()
        )
        if not member:
            raise CommandError(f"no active admin in workspace '{workspace.slug}'")
        return member.member

    def _find_issue(self, p):
        """Resolve a work item by 'ABC-123' ref, or workspace + project + number."""
        from plane.db.models import Issue, Project

        ref = (p.get("ref") or "").strip()
        if ref:
            if "-" not in ref:
                raise CommandError("ref should look like ABC-123")
            identifier, _, number = ref.rpartition("-")
            if not number.isdigit():
                raise CommandError("ref should look like ABC-123")
            projects = Project.objects.filter(identifier__iexact=identifier, deleted_at__isnull=True)
            if p.get("workspace"):
                projects = projects.filter(workspace__slug=p["workspace"])
            issues = Issue.objects.filter(project__in=projects, sequence_id=int(number), deleted_at__isnull=True)
            found = list(issues.select_related("project", "workspace")[:5])
            if not found:
                raise CommandError(f"no work item '{ref}'" + (f" in {p['workspace']}" if p.get("workspace") else ""))
            if len(found) > 1:
                where = ", ".join(f"{i.workspace.slug}" for i in found)
                raise CommandError(f"'{ref}' exists in several workspaces ({where}); pass workspace to disambiguate")
            return found[0]
        raise CommandError("ref required, e.g. 'ABC-123'")

    # -------------------------------------------------------------------- kinds
    def _work_item(self, p):
        from plane.bgtasks.issue_activities_task import issue_activity
        from plane.db.models import Cycle, CycleIssue, EstimatePoint, IssueAssignee, State, User

        issue = self._find_issue(p)
        project, workspace = issue.project, issue.workspace
        actor = self._actor(workspace, p.get("actor"))

        # snapshot for the activity feed, matching what the view sends
        before = {
            "name": issue.name,
            "priority": issue.priority,
            "state_id": str(issue.state_id) if issue.state_id else None,
            "start_date": str(issue.start_date) if issue.start_date else None,
            "target_date": str(issue.target_date) if issue.target_date else None,
            "description_html": issue.description_html,
            "estimate_point": str(issue.estimate_point_id) if issue.estimate_point_id else None,
        }

        requested, changed = {}, []

        if p.get("state"):
            state = (
                State.objects.filter(project=project, name__iexact=p["state"]).first()
                or State.objects.filter(project=project, group__iexact=p["state"]).first()
            )
            if not state:
                options = list(State.objects.filter(project=project).values_list("name", flat=True))
                raise CommandError(f"no state '{p['state']}' in {project.name}; options: {options}")
            issue.state = state
            requested["state_id"] = str(state.id)
            changed.append(f"state -> {state.name}")

        for field in ("name", "priority"):
            if p.get(field):
                setattr(issue, field, p[field])
                requested[field] = p[field]
                changed.append(f"{field} -> {p[field]}")

        if p.get("description"):
            issue.description_html = f"<p>{p['description']}</p>"
            requested["description_html"] = issue.description_html
            changed.append("description")

        for field in ("start_date", "target_date"):
            if field in p and p[field] is not None:
                setattr(issue, field, p[field] or None)
                requested[field] = p[field]
                changed.append(f"{field} -> {p[field]}")

        if p.get("estimate_hours"):
            wanted = str(p["estimate_hours"]).strip()
            point = EstimatePoint.objects.filter(project=project, estimate__name="Hours", value=wanted).first()
            if not point:
                available = list(
                    EstimatePoint.objects.filter(project=project, estimate__name="Hours")
                    .order_by("key")
                    .values_list("value", flat=True)
                )
                raise CommandError(f"estimate_hours '{wanted}' unavailable; options: {available}")
            issue.estimate_point = point
            requested["estimate_point"] = str(point.id)
            changed.append(f"estimate -> {wanted}h")

        issue.save()

        def resolve(names):
            out = []
            for who in names:
                user = User.objects.filter(email__iexact=who).first() or User.objects.filter(
                    display_name__iexact=who
                ).first()
                if not user:
                    raise CommandError(f"unknown person '{who}'")
                out.append(user)
            return out

        if p.get("assignees") is not None:  # replace the whole set
            people = resolve(p["assignees"])
            IssueAssignee.objects.filter(issue=issue).delete()
            IssueAssignee.objects.bulk_create(
                [IssueAssignee(issue=issue, assignee=u, project=project, workspace=workspace) for u in people],
                ignore_conflicts=True,
            )
            requested["assignee_ids"] = [str(u.id) for u in people]
            changed.append("assignees -> " + (", ".join(u.display_name for u in people) or "nobody"))
        elif p.get("add_assignees"):
            people = resolve(p["add_assignees"])
            IssueAssignee.objects.bulk_create(
                [IssueAssignee(issue=issue, assignee=u, project=project, workspace=workspace) for u in people],
                ignore_conflicts=True,
            )
            changed.append("added " + ", ".join(u.display_name for u in people))

        if p.get("cycle"):
            cycles = Cycle.objects.filter(project=project, deleted_at__isnull=True)
            cycle = cycles.filter(name__iexact=p["cycle"]).first() or cycles.filter(
                name__icontains=p["cycle"]
            ).first()
            if not cycle:
                options = list(cycles.order_by("-start_date").values_list("name", flat=True)[:8])
                raise CommandError(f"no cycle '{p['cycle']}' in {project.name}; options: {options}")
            CycleIssue.objects.filter(issue=issue).delete()
            CycleIssue.objects.create(
                cycle=cycle, issue=issue, project=project, workspace=workspace, created_by=actor
            )
            changed.append(f"cycle -> {cycle.name}")

        if not changed:
            raise CommandError("nothing to change — pass at least one field")

        # let Plane record this like any other edit: history entry + subscriber notifications
        if requested:
            issue_activity.delay(
                type="issue.activity.updated",
                requested_data=json.dumps(requested),
                actor_id=str(actor.id),
                issue_id=str(issue.id),
                project_id=str(project.id),
                current_instance=json.dumps(before),
                epoch=int(timezone.now().timestamp()),
                notification=True,
            )

        issue.refresh_from_db()
        return {
            "ok": True,
            "ref": f"{project.identifier}-{issue.sequence_id}",
            "name": issue.name,
            "state": issue.state.name if issue.state else None,
            "project": project.name,
            "workspace": workspace.slug,
            "changed": changed,
            "at": timezone.now().isoformat(),
        }

    def _comment(self, p):
        from plane.bgtasks.issue_activities_task import issue_activity
        from plane.db.models import IssueComment

        issue = self._find_issue(p)
        body = (p.get("comment") or "").strip()
        if not body:
            raise CommandError("comment text required")
        actor = self._actor(issue.workspace, p.get("actor"))

        comment = IssueComment.objects.create(
            issue=issue,
            project=issue.project,
            workspace=issue.workspace,
            comment_html=f"<p>{body}</p>",
            comment_stripped=body,
            actor=actor,
            created_by=actor,
        )
        issue_activity.delay(
            type="comment.activity.created",
            requested_data=json.dumps({"id": str(comment.id), "comment_html": comment.comment_html}),
            actor_id=str(actor.id),
            issue_id=str(issue.id),
            project_id=str(issue.project_id),
            current_instance=None,
            epoch=int(timezone.now().timestamp()),
            notification=True,
        )
        return {
            "ok": True,
            "ref": f"{issue.project.identifier}-{issue.sequence_id}",
            "comment_id": str(comment.id),
            "by": actor.display_name,
            "at": timezone.now().isoformat(),
        }
