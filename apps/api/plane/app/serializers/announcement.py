# Fork addition: serializers for the announcement banner.
from django.core.validators import URLValidator
from rest_framework import serializers

from plane.db.models import Announcement, AnnouncementAudience, Project, Team, Workspace

from .base import BaseSerializer


class AnnouncementSerializer(BaseSerializer):
    """What the banner script reads."""

    author_name = serializers.CharField(source="author.display_name", read_only=True, default="")

    class Meta:
        model = Announcement
        fields = [
            "id",
            "message",
            "level",
            "link_url",
            "link_label",
            "is_dismissible",
            "revision",
            "author_name",
        ]
        read_only_fields = fields


class AnnouncementWriteSerializer(BaseSerializer):
    """Authoring. Targets are given by slug/id and resolved here."""

    workspace_slug = serializers.CharField(write_only=True, required=False, allow_blank=True)
    project_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    team_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Announcement
        fields = [
            "id",
            "message",
            "level",
            "audience",
            "link_url",
            "link_label",
            "is_enabled",
            "is_dismissible",
            "starts_at",
            "ends_at",
            "revision",
            "workspace_slug",
            "project_id",
            "team_id",
        ]
        read_only_fields = ["id", "revision"]

    def validate_link_url(self, value):
        # rendered into an <a href>, so anything but http(s) is stored XSS
        if not value:
            return value
        URLValidator(schemes=["http", "https"])(value)
        return value

    def validate(self, attrs):
        audience = attrs.get("audience") or getattr(self.instance, "audience", None)
        slug = attrs.pop("workspace_slug", None)
        project_id = attrs.pop("project_id", None)
        team_id = attrs.pop("team_id", None)

        if audience == AnnouncementAudience.WORKSPACE:
            workspace = Workspace.objects.filter(slug=slug).first()
            if not workspace:
                raise serializers.ValidationError({"workspace_slug": "Unknown workspace"})
            attrs.update(workspace=workspace, project=None, team=None)
        elif audience == AnnouncementAudience.PROJECT:
            project = Project.objects.filter(pk=project_id, deleted_at__isnull=True).first()
            if not project:
                raise serializers.ValidationError({"project_id": "Unknown project"})
            attrs.update(project=project, workspace=project.workspace, team=None)
        elif audience == AnnouncementAudience.TEAM:
            team = Team.objects.filter(pk=team_id, deleted_at__isnull=True).first()
            if not team:
                raise serializers.ValidationError({"team_id": "Unknown team"})
            attrs.update(team=team, workspace=team.workspace, project=None)
        elif audience == AnnouncementAudience.INSTANCE:
            attrs.update(workspace=None, project=None, team=None)

        if attrs.get("starts_at") and attrs.get("ends_at") and attrs["starts_at"] > attrs["ends_at"]:
            raise serializers.ValidationError("Start time cannot be after end time")
        self._workspace_id = attrs.get("workspace").id if attrs.get("workspace") else None
        return attrs

    def resolved_workspace_id(self):
        return getattr(self, "_workspace_id", None)
