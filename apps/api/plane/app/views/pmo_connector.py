# Fork addition: Plane sign-in for PMO's admin surfaces — the Claude connector and the
# manager dashboard.
#
# Both used to sit behind nginx basic auth with the shared dashboard password: a browser
# popup with no relation to anyone's Plane account. Now nginx asks Plane (auth_request):
#
#   connector_gate   /authorize    instance admin, and the approval code headed for Claude
#   dashboard_gate   /dashboard/   instance admin
#
# and routes the answers:
#   200  through
#   401  not signed in: remember where they were going, Plane's sign-in, then resume
#   403  a page saying why
#
# The dashboard also still accepts the shared password (nginx `satisfy any`), because the
# MCP servers read it machine-to-machine and cannot present a browser session. People
# never see that prompt: a 401 goes to Plane's sign-in, not to a WWW-Authenticate popup.
#
# INSTANCE ADMINS ONLY
# Both surfaces read every workspace, and both can create and change work on anyone's
# board. "Has a Plane account" is the wrong bar for that.
#
# WHERE THE CONNECTOR'S CODE MAY GO
# /authorize approves without a consent screen, and anyone can register an OAuth client.
# Without a check on redirect_uri, a link to /authorize naming a stranger's callback would,
# for an admin already signed in to Plane, mint that stranger a manager token in one click.
# Codes go to Claude, or to the admin's own machine (loopback, for Claude Code), only.
#
# WHERE SIGN-IN ACTUALLY IS
# Plane's sign-in form lives at "/", not "/sign-in". /sign-in redirects to "/" in the
# browser and drops the query string, next_path with it — so someone sent to
# /sign-in?next_path=... signs in and is stranded on the app's home page. The first version
# of this did exactly that, and curl could not catch it because curl runs no JavaScript.
#
# WHY A COOKIE RATHER THAN next_path
# Plane drops a next_path over 500 characters and rejects one containing %2F%2F, and an
# authorize URL is usually both (redirect_uri=https%3A%2F%2Fclaude.ai...). So the target
# waits in a ten-minute cookie and next_path carries only the short resume path.
#
# THE RETURN TRIP
# After sign-in Plane lands on /?next_path=<resume>. nginx (nginx.ssl.conf_landing, with the
# map in plane_pmo.conf) hops that to resume — only when a session cookie is present,
# because signed out the same URL is the sign-in page. A stale cookie can still hop a
# signed-out browser into resume; resume clears it and sends them to sign in, so that
# detour happens at most once rather than looping.
#
# Plain Django views, not DRF: nginx needs a real 401 to tell "not signed in" from
# "not allowed", and DRF's session authentication reports an anonymous user as 403.
import base64
import html
import logging
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect

from plane.license.models import InstanceAdmin

log = logging.getLogger("plane.api")

ALLOWED_REDIRECTS = {
    "https://claude.ai/api/mcp/auth_callback",   # what Claude sends, per the connector's own logs
    "https://claude.com/api/mcp/auth_callback",
}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

RESUME_COOKIE = "pmo_connector_resume"
RESUME_PATH = "/api/pmo/connector/resume/"
COOKIE_PATH = "/api/pmo/connector/"
RESUME_SECONDS = 600
SIGN_IN_URL = "/?next_path=" + RESUME_PATH


def _resumable(uri):
    """Same-host paths this flow may send a browser back to. Never anything absolute."""
    return uri.startswith("/authorize?") or uri == "/dashboard" or uri.startswith("/dashboard/")


def _original(request):
    """What the browser asked for when nginx stopped it, exactly as sent."""
    uri = request.headers.get("X-Original-URI", "")
    return uri if _resumable(uri) else ""


def _redirect_uri(uri):
    return (parse_qs(urlparse(uri).query).get("redirect_uri") or [""])[0]


def _trusted(redirect):
    """Is this a place a connector approval code may be sent?

    A missing redirect_uri is refused too: the OAuth server would then fall back to
    whatever the client registered, which is exactly the attacker-chosen value this
    check exists to keep out.
    """
    if redirect in ALLOWED_REDIRECTS:
        return True
    parsed = urlparse(redirect)
    # hostname, not netloc: "http://127.0.0.1@evil.example/" has netloc
    # "127.0.0.1@evil.example" and hostname "evil.example"
    return parsed.scheme == "http" and parsed.hostname in LOOPBACK


def _is_admin(request):
    return request.user.is_authenticated and InstanceAdmin.objects.filter(user=request.user).exists()


def _page(status, title, body):
    return HttpResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<title>PMO</title>"
        "<body style='font:15px/1.5 system-ui,-apple-system,sans-serif;max-width:34rem;"
        "margin:12vh auto;padding:0 1.25rem;color:#1b1d23'>"
        f"<h1 style='font-size:1.3rem'>{title}</h1>{body}</body>",
        status=status,
        content_type="text/html; charset=utf-8",
    )


def _not_admin_page(request, uri):
    who = html.escape(request.user.email or "this account")
    what = "connect the PMO manager connector" if uri.startswith("/authorize") else "open the PMO dashboard"
    return _page(
        403,
        f"This account can't {what}",
        f"<p>You're signed in as <b>{who}</b>, which isn't a PMO admin.</p>"
        "<p>It can see every workspace and change work on anyone's board, so only admins "
        "have access. Ask Kesara if you need it.</p>",
    )


def connector_gate(request):
    """auth_request target for /authorize."""
    uri = _original(request)
    redirect = _redirect_uri(uri)
    if not uri.startswith("/authorize?") or not _trusted(redirect):
        log.warning("pmo connector: refused /authorize for redirect_uri=%r", redirect)
        return HttpResponse(status=403)
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    if not _is_admin(request):
        log.warning("pmo connector: %s is signed in but not an instance admin", request.user.email)
        return HttpResponse(status=403)
    return HttpResponse(status=200)


def dashboard_gate(request):
    """auth_request target for /dashboard/ (alongside the machines' basic auth)."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    if not _is_admin(request):
        log.warning("pmo dashboard: %s is signed in but not an instance admin", request.user.email)
        return HttpResponse(status=403)
    return HttpResponse(status=200)


def sign_in(request):
    """Reached instead of a 401. Decides between sign-in, a refusal, and going through.

    For the dashboard, nginx's `satisfy any` reports a signed-in non-admin as 401 too
    (the missing basic-auth header wins), so this must not assume "not signed in".
    """
    uri = _original(request)
    if not uri:
        return _page(400, "Nothing to resume", "<p>Start again from Claude or the dashboard.</p>")
    if request.user.is_authenticated:
        return _not_admin_page(request, uri) if not _is_admin(request) else HttpResponseRedirect(uri)
    response = HttpResponseRedirect(SIGN_IN_URL)
    response.set_cookie(
        RESUME_COOKIE,
        base64.urlsafe_b64encode(uri.encode()).decode(),   # survives cookie quoting untouched
        max_age=RESUME_SECONDS,
        path=COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="Lax",
    )
    return response


def resume(request):
    """Back from sign-in: return to whatever was interrupted."""
    try:
        uri = base64.urlsafe_b64decode(request.COOKIES.get(RESUME_COOKIE, "").encode()).decode()
    except (ValueError, UnicodeDecodeError):
        uri = ""
    # Only ever back to a same-host path we stopped — never an open redirect. The gates
    # re-check everything on arrival, so a stale or tampered cookie gains nothing.
    if not _resumable(uri):
        return _page(
            400,
            "This sign-in has expired",
            "<p>You're signed in now. Go back to Claude and choose <b>Connect</b> on PMO "
            "again, or <a href='/dashboard/'>open the dashboard</a>.</p>",
        )
    if not request.user.is_authenticated:
        # Hopped here on a session cookie that no longer means anything. Clear it, or nginx
        # would hop the sign-in page straight back here.
        response = HttpResponseRedirect(SIGN_IN_URL)
        response.delete_cookie(
            settings.SESSION_COOKIE_NAME,
            path=settings.SESSION_COOKIE_PATH,
            domain=settings.SESSION_COOKIE_DOMAIN,
            samesite=settings.SESSION_COOKIE_SAMESITE,
        )
        return response
    response = HttpResponseRedirect(uri)
    response.delete_cookie(RESUME_COOKIE, path=COOKIE_PATH)
    return response


def denied(request):
    """Reached instead of a bare 403, saying which refusal it was."""
    uri = request.headers.get("X-Original-URI", "")
    if uri.startswith("/authorize") and not _trusted(_redirect_uri(uri)):
        return _page(
            403,
            "This connection wasn't started by Claude",
            "<p>PMO only sends approvals back to Claude. If you didn't just choose "
            "<b>Connect</b> in Claude, close this page.</p>",
        )
    if request.user.is_authenticated:
        return _not_admin_page(request, uri)
    return _page(403, "Not allowed", "<p>Sign in to PMO as an admin and try again.</p>")
