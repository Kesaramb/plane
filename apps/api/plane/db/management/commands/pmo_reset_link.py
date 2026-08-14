# Fork addition: mint a password-reset link for a user without sending email.
#
#   python manage.py pmo_reset_link --email someone@example.com
#
# This instance has no SMTP, so Plane's own "forgot password" mail never arrives
# and every reset otherwise has to be done by an admin typing a password and
# telling the person what it is. This produces the same link Plane would have
# emailed, for the admin to pass along over whatever channel they like. The
# person then chooses their own password, and the admin never learns it.
#
# The link uses Django's PasswordResetTokenGenerator, so it is single-use — it
# stops working once the password changes — and it is invalidated by the user's
# last_login and current password hash.
import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Generate a password reset link for a user (no email sent)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--base-url", default=None, help="Defaults to WEB_URL")

    def handle(self, *args, **options):
        import os

        from plane.authentication.views.app.password_management import generate_password_token
        from plane.db.models import User

        email = options["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            near = list(
                User.objects.filter(email__icontains=email.split("@")[0], is_active=True)
                .values_list("email", flat=True)[:5]
            )
            raise CommandError(f"no user '{email}'" + (f"; did you mean: {near}" if near else ""))
        if not user.is_active:
            raise CommandError(f"'{email}' is deactivated — reactivate it before sending a reset link")

        base = (options["base_url"] or os.environ.get("WEB_URL") or "").rstrip("/")
        if not base:
            raise CommandError("no base url; pass --base-url")

        uidb64, token = generate_password_token(user=user)
        link = f"{base}/accounts/reset-password/?uidb64={uidb64}&token={token}&email={user.email}"

        self.stdout.write(json.dumps({
            "ok": True,
            "email": user.email,
            "display_name": user.display_name,
            "workspaces": user.workspace_member.filter(is_active=True).count()
            if hasattr(user, "workspace_member") else None,
            "reset_link": link,
            "note": "Single use. Stops working once the password is changed.",
        }))
