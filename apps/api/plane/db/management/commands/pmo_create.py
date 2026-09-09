# Fork addition: create projects, cycles and work items through Plane's own ORM
# so everything Plane seeds on creation (states, sequences, membership) is correct.
#
# Usage (invoked by the dashboard admin API):
#   python manage.py pmo_create --kind project|cycle|work_item --payload '<json>'
#
# project    { workspace, name, identifier?, description?, lead?, owner? }
# cycle      { workspace, project_id, name, start_date, end_date, description? }
# work_item  { workspace, project_id, name, description?, assignees?[], priority?,
#              start_date?, target_date?, estimate_hours?, cycle_id? }
#
# Newly created projects are seeded with the "Hours" estimate system, because this
# fork requires a time allocation before an item may enter an In Progress state —
# without it, nothing in a new project could ever be started.
import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

HOURS_POINTS = ["1", "2", "4", "6", "8", "16"]


def _looks_like_uuid(value):
    import uuid as _uuid

    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class Command(BaseCommand):
    help = "Create a project, cycle or work item via Plane's ORM."

    def add_arguments(self, parser):
        parser.add_argument("--kind", required=True, choices=["project", "cycle", "work_item"])
        parser.add_argument("--payload", required=True, help="JSON payload")

    def handle(self, *args, **options):
        payload = json.loads(options["payload"])
        result = getattr(self, f"_{options['kind']}")(payload)
        self.stdout.write(json.dumps(result))

    # ------------------------------------------------------------------ helpers
    def _workspace(self, slug):
        from plane.db.models import Workspace

        try:
            return Workspace.objects.get(slug=slug)
        except Workspace.DoesNotExist:
            raise CommandError(f"unknown workspace '{slug}'")

    def _actor(self, workspace, hint=None):
        """The user a creation is attributed to: an explicit hint, else a workspace admin."""
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
            raise CommandError(f"no active admin found in workspace '{workspace.slug}'")
        return member.member

    def _get_project(self, workspace, payload):
        """Resolve a project by id, or by name/identifier against the live DB.

        Name resolution happens here rather than in the caller because a project
        created moments ago is not yet in the dashboard's cached snapshot.
        """
        from plane.db.models import Project

        projects = Project.objects.filter(workspace=workspace, deleted_at__isnull=True)
        pid = payload.get("project_id")
        if pid:
            project = projects.filter(id=pid).first()
            if project:
                return project
        wanted = (payload.get("project") or "").strip()
        if wanted:
            project = (
                projects.filter(identifier__iexact=wanted).first()
                or projects.filter(name__iexact=wanted).first()
                or projects.filter(name__icontains=wanted).first()
            )
            if project:
                return project
        options = list(projects.order_by("name").values_list("name", flat=True)[:8])
        raise CommandError(f"no project matching '{wanted or pid}' in {workspace.slug}; options: {options}")

    def _seed_hours_estimate(self, project):
        """Give a new project the Hours estimate system this fork requires."""
        from plane.db.models import Estimate, EstimatePoint

        estimate = Estimate.objects.create(project=project, name="Hours", type="points", last_used=True)
        EstimatePoint.objects.bulk_create(
            [
                EstimatePoint(estimate=estimate, project=project, workspace=project.workspace, key=key, value=value)
                for key, value in enumerate(HOURS_POINTS)
            ]
        )
        project.estimate = estimate
        project.save(update_fields=["estimate"])
        return estimate

    # -------------------------------------------------------------------- kinds
    def _project(self, p):
        from plane.db.models import Project, ProjectMember, State
        from plane.db.models.state import DEFAULT_STATES

        workspace = self._workspace(p["workspace"])
        name = (p.get("name") or "").strip()
        if not name:
            raise CommandError("name required")

        identifier = (p.get("identifier") or "").strip().upper()
        if not identifier:
            # derive from the name: initials for multi-word, else the first letters
            words = [w for w in "".join(c if c.isalnum() else " " for c in name).split() if w]
            identifier = ("".join(w[0] for w in words) if len(words) > 1 else name)[:8].upper()
            identifier = "".join(c for c in identifier if c.isalnum()) or "PROJ"
        if Project.objects.filter(workspace=workspace, identifier=identifier).exists():
            base, n = identifier[:6], 2
            while Project.objects.filter(workspace=workspace, identifier=f"{base}{n}").exists():
                n += 1
            identifier = f"{base}{n}"

        owner = self._actor(workspace, p.get("owner"))
        lead = self._actor(workspace, p["lead"]) if p.get("lead") else None

        project = Project.objects.create(
            name=name,
            description=p.get("description", ""),
            identifier=identifier,
            workspace=workspace,
            project_lead=lead,
            created_by=owner,
        )

        # Plane seeds these on create; replicate faithfully.
        State.objects.bulk_create(
            [
                State(
                    name=state["name"],
                    color=state["color"],
                    project=project,
                    sequence=state["sequence"],
                    workspace=workspace,
                    group=state["group"],
                    default=state.get("default", False),
                    created_by=owner,
                )
                for state in DEFAULT_STATES
            ]
        )
        ProjectMember.objects.create(project=project, member=owner, role=20)
        if lead and lead.id != owner.id:
            ProjectMember.objects.create(project=project, member=lead, role=20)
        self._seed_hours_estimate(project)

        return {
            "ok": True,
            "kind": "project",
            "id": str(project.id),
            "name": project.name,
            "identifier": project.identifier,
            "workspace": workspace.slug,
            "lead": lead.display_name if lead else None,
            "states_seeded": len(DEFAULT_STATES),
            "estimate": "Hours",
            "at": timezone.now().isoformat(),
        }

    def _cycle(self, p):
        from plane.db.models import Cycle

        workspace = self._workspace(p["workspace"])
        project = self._get_project(workspace, p)
        name = (p.get("name") or "").strip()
        if not name:
            raise CommandError("name required")
        if not (p.get("start_date") and p.get("end_date")):
            raise CommandError("start_date and end_date required (YYYY-MM-DD)")
        if p["start_date"] > p["end_date"]:
            raise CommandError("start_date cannot be after end_date")

        owner = self._actor(workspace, p.get("owner"))
        cycle = Cycle.objects.create(
            name=name,
            description=p.get("description", ""),
            project=project,
            workspace=workspace,
            start_date=p["start_date"],
            end_date=p["end_date"],
            owned_by=owner,
            created_by=owner,
        )
        # cycles are hidden in the UI unless the project opts into the cycle view
        if not project.cycle_view:
            project.cycle_view = True
            project.save(update_fields=["cycle_view"])

        return {
            "ok": True,
            "kind": "cycle",
            "id": str(cycle.id),
            "name": cycle.name,
            "project": project.name,
            "workspace": workspace.slug,
            "start_date": p["start_date"],
            "end_date": p["end_date"],
            "at": timezone.now().isoformat(),
        }

    def _work_item(self, p):
        from plane.db.models import (
            CycleIssue, EstimatePoint, Issue, IssueAssignee, ProjectMember, State, User,
        )

        workspace = self._workspace(p["workspace"])
        project = self._get_project(workspace, p)
        name = (p.get("name") or "").strip()
        if not name:
            raise CommandError("name required")

        owner = self._actor(workspace, p.get("owner"))
        state = (
            State.objects.filter(project=project, group="unstarted").first()
            or State.objects.filter(project=project, group="backlog").first()
            or State.objects.filter(project=project).first()
        )

        estimate_point = None
        if p.get("estimate_hours"):
            wanted = str(p["estimate_hours"]).strip()
            estimate_point = EstimatePoint.objects.filter(
                project=project, estimate__name="Hours", value=wanted
            ).first()
            if not estimate_point:
                available = list(
                    EstimatePoint.objects.filter(project=project, estimate__name="Hours")
                    .order_by("key")
                    .values_list("value", flat=True)
                )
                raise CommandError(f"estimate_hours '{wanted}' not available; options: {available}")

        issue = Issue.objects.create(
            name=name,
            description_html=f"<p>{p['description']}</p>" if p.get("description") else "<p></p>",
            project=project,
            workspace=workspace,
            state=state,
            priority=p.get("priority", "none"),
            start_date=p.get("start_date") or None,
            target_date=p.get("target_date") or None,
            estimate_point=estimate_point,
            created_by=owner,
        )

        assignees = []
        for who in p.get("assignees", []) or []:
            user = User.objects.filter(email__iexact=who).first() or User.objects.filter(
                display_name__iexact=who
            ).first()
            if not user:
                raise CommandError(f"unknown assignee '{who}'")
            assignees.append(user)
        if assignees:
            IssueAssignee.objects.bulk_create(
                [IssueAssignee(issue=issue, assignee=u, project=project, workspace=workspace) for u in assignees],
                ignore_conflicts=True,
            )
            # An assignee who is not a member of the project cannot open it, so the item
            # lands nowhere they can see — assigned in the database and invisible in the
            # app. Only the project owner and lead get membership at creation, so anyone
            # else planned into the work needs it adding here.
            #
            # One at a time rather than bulk_create: ProjectMember.save() also creates the
            # ProjectUserProperty row that puts the project in that person's sidebar, and
            # bulk_create skips save() entirely.
            for user in assignees:
                if not ProjectMember.objects.filter(
                    project=project, member=user, deleted_at__isnull=True
                ).exists():
                    ProjectMember.objects.create(
                        project=project, member=user, workspace=workspace, role=15, created_by=owner
                    )

        cycle_ref = p.get("cycle_id") or p.get("cycle")
        if cycle_ref:
            from plane.db.models import Cycle

            cycles = Cycle.objects.filter(project=project, deleted_at__isnull=True)
            cycle = (
                cycles.filter(id=cycle_ref).first()
                if _looks_like_uuid(cycle_ref)
                else (cycles.filter(name__iexact=cycle_ref).first() or cycles.filter(name__icontains=cycle_ref).first())
            )
            if not cycle:
                options = list(cycles.order_by("-start_date").values_list("name", flat=True)[:8])
                raise CommandError(f"no cycle matching '{cycle_ref}' in {project.name}; options: {options}")
            CycleIssue.objects.create(
                cycle=cycle, issue=issue, project=project, workspace=workspace, created_by=owner
            )

        return {
            "ok": True,
            "kind": "work_item",
            "id": str(issue.id),
            "ref": f"{project.identifier}-{issue.sequence_id}",
            "name": issue.name,
            "project": project.name,
            "workspace": workspace.slug,
            "assignees": [u.display_name for u in assignees],
            "estimate_hours": estimate_point.value if estimate_point else None,
            "at": timezone.now().isoformat(),
        }
