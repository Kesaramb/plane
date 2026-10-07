# Fork addition: connecting the PMO Claude connector with a Plane sign-in.
#
# The manager connector (pmo_mcp.py, served at /mcp) is an OAuth server whose /authorize
# step approves any request that reaches it. Something in front has to decide who may
# connect. That used to be nginx basic auth with the shared dashboard password — a
# browser popup with no relation to anyone's Plane account.
#
# Now nginx asks Plane (auth_request -> connector_gate), which answers:
#   200  signed in to Plane as an instance admin: the connection is approved
#   401  not signed in: park the authorize URL, sign in, come back to it
#   403  signed in but not an instance admin, or the code would go somewhere untrusted
#
# INSTANCE ADMINS ONLY
# The connector reads every workspace and can create and change work on anyone's board.
# "Has a Plane account" is the wrong bar for that.
#
# WHERE THE CODE MAY GO
# /authorize approves without a consent screen, and anyone can register an OAuth client.
# Without a check on redirect_uri, a link to /authorize naming a stranger's callback would,
# for an admin already signed in to Plane, mint that stranger a manager token in one click.
# The basic-auth popup was the only speed bump in that path, and it is gone. Codes now go
# to Claude, or to the admin's own machine (loopback, for Claude Code), and nowhere else.
#
# WHY A COOKIE RATHER THAN next_path
# Plane's sign-in drops a next_path over 500 characters and rejects one containing %2F%2F.
# An authorize URL is usually both — it carries redirect_uri=https%3A%2F%2Fclaude.ai... —
# so it waits in a short-lived cookie and sign-in is pointed at a short resume path.
#
# Plain Django views, not DRF: nginx needs a real 401 to tell "not signed in" from
# "not allowed", and DRF's session authentication reports an anonymous user as 403.
import base64
import html
import logging
from urllib.parse import parse_qs, urlparse

from django.http import HttpResponse, HttpResponseRedirect

from plane.license.models import InstanceAdmin

log = logging.getLogger("plane.api")

ALLOWED_REDIRECTS = {
    "https://claude.ai/api/mcp/auth_callback",   # what Claude sent when this was first connected
    "https://claude.com/api/mcp/auth_callback",
}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

RESUME_COOKIE = "pmo_connector_resume"
RESUME_PATH = "/api/pmo/connector/resume/"
COOKIE_PATH = "/api/pmo/connector/"
RESUME_SECONDS = 600


def _authorize_uri(request):
    """The /authorize request nginx is asking about, exactly as the browser sent it."""
    uri = request.headers.get("X-Original-URI", "")
    return uri if uri.startswith("/authorize?") else ""


def _redirect_uri(uri):
    return (parse_qs(urlparse(uri).query).get("redirect_uri") or [""])[0]


def _trusted(redirect):
    """Is this a place an approval code may be sent?

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
        "<title>PMO connector</title>"
        "<body style='font:15px/1.5 system-ui,-apple-system,sans-serif;max-width:34rem;"
        "margin:12vh auto;padding:0 1.25rem;color:#1b1d23'>"
        f"<h1 style='font-size:1.3rem'>{title}</h1>{body}</body>",
        status=status,
        content_type="text/html; charset=utf-8",
    )


def connector_gate(request):
    """nginx auth_request target: may this browser approve this connection?"""
    uri = _authorize_uri(request)
    redirect = _redirect_uri(uri)
    if not uri or not _trusted(redirect):
        log.warning("pmo connector: refused /authorize for redirect_uri=%r", redirect)
        return HttpResponse(status=403)
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    if not _is_admin(request):
        log.warning("pmo connector: %s is signed in but not an instance admin", request.user.email)
        return HttpResponse(status=403)
    return HttpResponse(status=200)


def connector_sign_in(request):
    """Not signed in: remember where they were going, then send them to Plane's sign-in."""
    uri = _authorize_uri(request)
    if not uri:
        return _page(400, "Nothing to connect", "<p>Start connecting PMO again from Claude.</p>")
    response = HttpResponseRedirect("/sign-in?next_path=" + RESUME_PATH)
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


def connector_resume(request):
    """Back from sign-in: return to the /authorize request that was interrupted."""
    try:
        uri = base64.urlsafe_b64decode(request.COOKIES.get(RESUME_COOKIE, "").encode()).decode()
    except (ValueError, UnicodeDecodeError):
        uri = ""
    # Only ever back to /authorize on this host — never an open redirect. The gate
    # re-checks everything on arrival, so a stale or tampered cookie gains nothing.
    if not uri.startswith("/authorize?"):
        return _page(
            400,
            "This connection attempt has expired",
            "<p>You're signed in. Go back to Claude and choose <b>Connect</b> on PMO again.</p>",
        )
    response = HttpResponseRedirect(uri)
    response.delete_cookie(RESUME_COOKIE, path=COOKIE_PATH)
    return response


def connector_denied(request):
    """Shown instead of a bare 403, saying which of the two refusals it was."""
    uri = _authorize_uri(request)
    if not uri or not _trusted(_redirect_uri(uri)):
        return _page(
            403,
            "This connection wasn't started by Claude",
            "<p>PMO only sends approvals back to Claude. If you didn't just choose "
            "<b>Connect</b> in Claude, close this page.</p>",
        )
    who = html.escape(request.user.email or "") if request.user.is_authenticated else "this account"
    return _page(
        403,
        "This account can't connect the PMO manager connector",
        f"<p>You're signed in as <b>{who}</b>, which isn't a PMO admin.</p>"
        "<p>This connector can see every workspace and change work on anyone's board, so "
        "only admins can connect it. Ask Kesara if you need access to your own work in "
        "Claude.</p>",
    )
