# Fork addition: email an announcement to everyone it targets.
#
# Runs on the worker so posting an announcement never blocks on SMTP. Recipients
# come from the same audience_users() the banner uses, so an email can never
# reach someone the banner would not have shown it to. One SMTP connection is
# opened for the whole batch rather than one per person, and a single bad
# address cannot stop the rest going out.
import logging

from celery import shared_task
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from plane.license.utils.instance_value import get_email_configuration
from plane.utils.exception_logger import log_exception

LEVEL_LABEL = {"critical": "Important", "warning": "Please note", "info": "Announcement"}


def _html(announcement, site, author):
    accent = {"critical": "#b02a2a", "warning": "#8a5a00", "info": "#1c5cab"}.get(announcement.level, "#1c5cab")
    body = (announcement.message or "").replace("\n", "<br>")
    link = ""
    if announcement.link_url:
        label = announcement.link_label or "Open"
        link = (
            f'<p style="margin:22px 0 0"><a href="{announcement.link_url}" '
            f'style="background:{accent};color:#fff;text-decoration:none;padding:10px 18px;'
            f'border-radius:8px;display:inline-block;font-weight:600">{label}</a></p>'
        )
    poll = ""
    if announcement.poll_options:
        opts = " &nbsp;·&nbsp; ".join(str(o) for o in announcement.poll_options)
        question = announcement.poll_question or "Let us know"
        poll = (
            f'<p style="margin:20px 0 0;padding:14px 16px;background:#f6f7f9;border-radius:10px;font-size:14px">'
            f'<b>{question}</b><br><span style="color:#52514e">{opts}</span><br>'
            f'<span style="color:#898781;font-size:13px">Vote from the banner at the top of Plane.</span></p>'
        )
    return f"""<div style="font-family:system-ui,-apple-system,'Segoe UI',sans-serif;max-width:560px;margin:0 auto;padding:8px">
  <p style="color:{accent};font-size:12px;letter-spacing:.12em;text-transform:uppercase;font-weight:700;margin:0 0 6px">
    {LEVEL_LABEL.get(announcement.level, 'Announcement')}</p>
  <p style="font-size:17px;line-height:1.5;color:#0b0b0b;margin:0">{body}</p>
  {link}{poll}
  <p style="margin:26px 0 0;color:#898781;font-size:13px">
    {'From ' + author + ' · ' if author else ''}<a href="{site}" style="color:#1c5cab">Open Plane</a></p>
</div>"""


@shared_task
def send_announcement_email(announcement_id, site):
    from plane.app.views.announcement import audience_users
    from plane.db.models import Announcement, AnnouncementReceipt

    try:
        announcement = Announcement.objects.filter(pk=announcement_id).select_related("author").first()
        if not announcement:
            return
        recipients = list(audience_users(announcement).exclude(email=""))
        if not recipients:
            return

        (
            EMAIL_HOST,
            EMAIL_HOST_USER,
            EMAIL_HOST_PASSWORD,
            EMAIL_PORT,
            EMAIL_USE_TLS,
            EMAIL_USE_SSL,
            EMAIL_FROM,
        ) = get_email_configuration()
        if not EMAIL_HOST:
            logging.getLogger("plane.worker").info("announcement email skipped: no SMTP configured")
            return

        connection = get_connection(
            host=EMAIL_HOST,
            port=int(EMAIL_PORT),
            username=EMAIL_HOST_USER,
            password=EMAIL_HOST_PASSWORD,
            use_tls=EMAIL_USE_TLS == "1",
            use_ssl=EMAIL_USE_SSL == "1",
        )
        connection.open()

        author = announcement.author.display_name if announcement.author else ""
        subject = (announcement.message or "Announcement").strip().split("\n")[0][:110]
        if announcement.level == "critical":
            subject = "[Important] " + subject
        html = _html(announcement, site, author)
        text = (announcement.message or "") + (f"\n\n{announcement.link_url}" if announcement.link_url else "")

        sent = 0
        for user in recipients:
            try:
                msg = EmailMultiAlternatives(
                    subject=subject, body=text, from_email=EMAIL_FROM,
                    to=[user.email], connection=connection,
                )
                msg.attach_alternative(html, "text/html")
                msg.send()
                sent += 1
                receipt, _ = AnnouncementReceipt.objects.get_or_create(
                    announcement=announcement, user=user, deleted_at__isnull=True
                )
                receipt.emailed_at = timezone.now()
                receipt.save(update_fields=["emailed_at"])
            except Exception as exc:  # one bad address must not stop the batch
                logging.getLogger("plane.worker").warning(
                    "announcement email to %s failed: %s", user.email, exc
                )
        connection.close()
        logging.getLogger("plane.worker").info(
            "announcement %s emailed to %s of %s", announcement_id, sent, len(recipients)
        )
    except Exception as e:
        log_exception(e)
    return
