# Fork addition: instance/workspace/project/team announcements rendered as a
# banner at the top of the Plane UI.
#
# The banner itself is injected by the reverse proxy (see deploy/pmo-banner/),
# so nothing here depends on the web bundle. This module owns only the data and
# the "who should see this" question, which cannot be answered in a browser.
from django.conf import settings
from django.db import models
from django.db.models import Q

from .base import BaseModel


class AnnouncementAudience(models.TextChoices):
    INSTANCE = "instance", "Everyone on the instance"
    WORKSPACE = "workspace", "One workspace"
    PROJECT = "project", "One project"
    TEAM = "team", "One team"


class AnnouncementLevel(models.TextChoices):
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    CRITICAL = "critical", "Critical"


class Announcement(BaseModel):
    message = models.TextField(verbose_name="Announcement Message")
    level = models.CharField(max_length=20, choices=AnnouncementLevel.choices, default=AnnouncementLevel.INFO)

    audience = models.CharField(max_length=20, choices=AnnouncementAudience.choices)
    # exactly one of these is set, per the audience discriminator
    workspace = models.ForeignKey(
        "db.Workspace", on_delete=models.CASCADE, related_name="announcements", null=True, blank=True
    )
    project = models.ForeignKey(
        "db.Project", on_delete=models.CASCADE, related_name="announcements", null=True, blank=True
    )
    team = models.ForeignKey(
        "db.Team", on_delete=models.CASCADE, related_name="announcements", null=True, blank=True
    )

    # Optional poll. Empty options means no voting is offered.
    poll_question = models.CharField(max_length=120, blank=True)
    poll_options = models.JSONField(default=list, blank=True)
    # Voters do not see the running count unless the author opts in. The tally is
    # withheld by the API too, not merely hidden in the UI.
    poll_show_results = models.BooleanField(default=False)

    link_url = models.URLField(max_length=800, blank=True)
    link_label = models.CharField(max_length=60, blank=True)

    is_enabled = models.BooleanField(default=True)
    is_dismissible = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    # Bumped when the author wants an edit re-shown to people who dismissed it.
    # Dismissals record the revision they dismissed, so a silent typo fix stays
    # dismissed while a material change comes back.
    revision = models.PositiveIntegerField(default=0)

    # BaseModel.save() only populates created_by inside an HTTP request, so an
    # explicit author is needed for anything created by a command or a script.
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="authored_announcements", null=True
    )

    def __str__(self):
        return f"{self.get_audience_display()}: {self.message[:40]}"

    def save(self, *args, **kwargs):
        # denormalise the workspace so workspace-scoped reads stay single-table
        if self.project_id and not self.workspace_id:
            self.workspace_id = self.project.workspace_id
        if self.team_id and not self.workspace_id:
            self.workspace_id = self.team.workspace_id
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Announcement"
        verbose_name_plural = "Announcements"
        db_table = "announcements"
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(audience="instance", workspace__isnull=True, project__isnull=True, team__isnull=True)
                    | Q(audience="workspace", workspace__isnull=False, project__isnull=True, team__isnull=True)
                    | Q(audience="project", project__isnull=False, team__isnull=True)
                    | Q(audience="team", team__isnull=False, project__isnull=True)
                ),
                name="announcement_audience_target_match",
            )
        ]
        indexes = [models.Index(fields=["is_enabled", "starts_at"], name="announce_active_idx")]


class AnnouncementDismissal(BaseModel):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="dismissals")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="announcement_dismissals")
    # the revision that was dismissed; a later revision re-shows the banner
    revision = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.user_id} dismissed {self.announcement_id}"

    class Meta:
        verbose_name = "Announcement Dismissal"
        verbose_name_plural = "Announcement Dismissals"
        db_table = "announcement_dismissals"
        ordering = ("-created_at",)
        unique_together = ["announcement", "user", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "user"],
                condition=Q(deleted_at__isnull=True),
                name="announcement_dismissal_unique_when_deleted_at_null",
            )
        ]


class AnnouncementReceipt(BaseModel):
    """Engagement per person per announcement: did it actually reach them.

    Separate from AnnouncementDismissal because dismissal carries revision
    semantics and is a deliberate action, while a receipt is passive telemetry.
    """

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="receipts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="announcement_receipts")
    first_seen_at = models.DateTimeField(null=True)
    last_seen_at = models.DateTimeField(null=True)
    seen_count = models.PositiveIntegerField(default=0)
    clicked_at = models.DateTimeField(null=True)

    def __str__(self):
        return f"{self.user_id} saw {self.announcement_id}"

    class Meta:
        verbose_name = "Announcement Receipt"
        verbose_name_plural = "Announcement Receipts"
        db_table = "announcement_receipts"
        ordering = ("-last_seen_at",)
        unique_together = ["announcement", "user", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "user"],
                condition=Q(deleted_at__isnull=True),
                name="announcement_receipt_unique_when_deleted_at_null",
            )
        ]


class AnnouncementVote(BaseModel):
    """One vote per person per announcement, changeable.

    The chosen label is snapshotted alongside its index so that editing the
    options later cannot silently rewrite what someone actually voted for.
    """

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="announcement_votes")
    choice_index = models.PositiveSmallIntegerField()
    choice_label = models.CharField(max_length=60)

    def __str__(self):
        return f"{self.user_id} voted {self.choice_label}"

    class Meta:
        verbose_name = "Announcement Vote"
        verbose_name_plural = "Announcement Votes"
        db_table = "announcement_votes"
        ordering = ("-created_at",)
        unique_together = ["announcement", "user", "deleted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "user"],
                condition=Q(deleted_at__isnull=True),
                name="announcement_vote_unique_when_deleted_at_null",
            )
        ]
