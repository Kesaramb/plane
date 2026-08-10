# Fork addition: team lead + team membership (manager/team-lead hierarchy)
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("db", "0121_alter_estimate_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="lead",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="led_teams",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="TeamMember",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                ("role", models.CharField(choices=[("lead", "Lead"), ("member", "Member")], default="member", max_length=20)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by", to=settings.AUTH_USER_MODEL, verbose_name="Created By",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="team_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="members", to="db.team",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by", to=settings.AUTH_USER_MODEL, verbose_name="Last Modified By",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="workspace_team_members", to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Team Member",
                "verbose_name_plural": "Team Members",
                "db_table": "team_members",
                "ordering": ("-created_at",),
                "unique_together": {("team", "member", "deleted_at")},
            },
        ),
        migrations.AddConstraint(
            model_name="teammember",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("team", "member"),
                name="team_member_unique_when_deleted_at_null",
            ),
        ),
    ]
