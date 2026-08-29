# Fork addition: focus blocks — the unit the Quest Board is built around.
#
# The board previously had XP, levels, streaks and estimates, but no notion of *now*.
# A ranked list is a menu, not a session: you still have to decide what to do, how long
# to do it for, and when to stop, every few minutes. That is the opposite of flow.
#
# A focus block is one commitment: this task, for this long, starting now. It is the
# anchor the rest of the system hangs off — the board shows it instead of the list while
# it runs, XP is earned by landing it, and the streak counts days you actually sat down.
#
# Two things are deliberately separate:
#   size     — how big a task is, shared, on the project's estimate scale (1h, 2h, 4h…)
#   a block  — how long *I* am giving it right now, personal (30m, 50m, 90m)
# A four-hour task is three ninety-minute blocks. Conflating them is what made "set a
# timebox" ambiguous, and it is why blocks are not stored on the work item.
import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .base import BaseModel


class FocusOutcome(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"  # the work item was finished in this block
    LANDED = "landed", "Landed"  # block ran its full length, item still open
    STOPPED = "stopped", "Stopped"  # ended early


class FocusBlock(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="focus_blocks"
    )
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="focus_blocks")
    minutes = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=12, choices=FocusOutcome.choices, default=FocusOutcome.RUNNING)

    # A paused block is still the open one — pausing is not an outcome, it is a state a
    # running block is in. Keeping outcome as "running" is what preserves the one-open-
    # block-per-person constraint: pausing must not let a second block be started.
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "focus_blocks"
        verbose_name = "Focus Block"
        ordering = ("-started_at",)
        constraints = [
            # One "now" per person, enforced here rather than in a view: two running
            # blocks would make the anchor meaningless and the day's totals wrong.
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(outcome="running", deleted_at__isnull=True),
                name="one_running_focus_block_per_user",
            )
        ]

    def __str__(self):
        return f"{self.user_id} · {self.minutes}m · {self.outcome}"

    @property
    def is_paused(self):
        return self.paused_at is not None

    def elapsed_seconds(self):
        """Time actually spent in the block: wall clock minus everything paused.

        Measured from the clock rather than accumulated by the browser, so closing the
        tab, sleeping the laptop or coming back tomorrow all give the same answer.
        """
        upto = self.paused_at or timezone.now()
        return max(0, int((upto - self.started_at).total_seconds()) - self.paused_seconds)

    def remaining_seconds(self):
        return max(0, self.minutes * 60 - self.elapsed_seconds())

    def paused_for_seconds(self):
        if not self.paused_at:
            return 0
        return max(0, int((timezone.now() - self.paused_at).total_seconds()))
