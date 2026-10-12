# Fork addition: Plane sign-in for PMO's admin surfaces — the Claude connector and the
# manager dashboard.
#
# Both used to sit behind nginx basic auth with the shared dashboard password: a browser
# popup with no relation to anyone's Plane account. Now nginx asks Plane (auth_request):
#
#   connector_gate   /authorize    instance admin, a trusted app, and the admin said yes
#   dashboard_gate   /dashboard/   instance admin
#
# and routes the answers:
#   200  through
#   401  not signed in: remember where they were going, Plane's sign-in, then resume
#   403  a page: the consent question, or why not
#
# The dashboard also still accepts the shared password (nginx `satisfy any`), because the
# MCP servers read it machine-to-machine and cannot present a browser session. People
# never see that prompt: a 401 goes to Plane's sign-in, not to a WWW-Authenticate popup.
#
# INSTANCE ADMINS ONLY
# Both surfaces read every workspace, and both can create and change work on anyone's
# board. "Has a Plane account" is the wrong bar for that.
#
# WHERE THE CONNECTOR'S CODE MAY GO, AND WHY THE ADMIN IS ASKED
# Anyone can register an OAuth client, so redirect_uri is checked against apps we know —
# Claude, ChatGPT, or the admin's own machine (loopback, for Claude Code).
#
# That alone is not enough, and the first version of this wrongly treated it as if it
# were. Claude and ChatGPT both use one callback for every account on their platform; the
# OAuth `state` decides whose connector the code lands in. So a stranger can start a
# connection in their OWN Claude or ChatGPT account, send the resulting /authorize link to
# an admin, and — if the admin is signed in to Plane and nothing asks — their account
# receives a manager token. The allowlist cannot see that: the callback is genuinely
# claude.ai or chatgpt.com.
#
# Hence the consent page. The FastMCP provider behind /authorize approves anything that
# reaches it, so the question is asked here: which app, what it can do, and "only allow if
# you just started this yourself". Yes is a signed form token (no forging) plus an Origin
# check (no cross-site auto-submit), and it buys a two-minute cookie bound to this exact
# authorize request and this admin. One click per connection.
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
import hashlib
import html
import logging
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core import signing
from django.http import HttpResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from plane.license.models import InstanceAdmin

log = logging.getLogger("plane.api")

# Apps that may receive an approval code, by where they ask for it to be sent. Matched on
# the parsed host and path, never a string prefix, so "https://chatgpt.com.evil.example/"
# and "https://claude.ai@evil.example/" pass for neither.
_OAUTH_ID = "/connector/oauth/"
TRUSTED_APPS = (
    # (shown as, https host, path test) — each from what the app actually sent, per the logs
    ("Claude", "claude.ai", lambda p: p == "/api/mcp/auth_callback"),
    ("Claude", "claude.com", lambda p: p == "/api/mcp/auth_callback"),
    # ChatGPT gives every connector its own callback: /connector/oauth/<id>
    ("ChatGPT", "chatgpt.com", lambda p: p.startswith(_OAUTH_ID) and len(p) > len(_OAUTH_ID)),
)
LOOPBACK = {"localhost", "127.0.0.1", "::1"}

CONSENT_COOKIE = "pmo_connector_consent"
CONSENT_PATH = "/api/pmo/connector/consent/"
CONSENT_SECONDS = 120        # from clicking Allow to the browser arriving back at /authorize
CONSENT_FORM_SECONDS = 600   # how long the question may sit on screen before it goes stale
_COOKIE_SALT = "pmo-connector-consent"
_FORM_SALT = "pmo-connector-consent-form"

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


def _app(redirect):
    """Which trusted app an approval code would go to, or None.

    A missing redirect_uri is refused too: the OAuth server would then fall back to
    whatever the client registered, which is exactly the attacker-chosen value this
    check exists to keep out.
    """
    parsed = urlparse(redirect or "")
    # hostname, not netloc: "https://claude.ai@evil.example/" has hostname "evil.example"
    if parsed.scheme == "https" and parsed.port is None:
        for name, host, path_ok in TRUSTED_APPS:
            if parsed.hostname == host and path_ok(parsed.path):
                return name
    if parsed.scheme == "http" and parsed.hostname in LOOPBACK:
        return "an app on your own computer"
    return None


def _is_admin(request):
    return request.user.is_authenticated and InstanceAdmin.objects.filter(user=request.user).exists()


def _fingerprint(uri):
    return hashlib.sha256(uri.encode()).hexdigest()


def _consented(request, uri):
    """Did this admin say yes to this exact /authorize request, in the last two minutes?"""
    try:
        said = signing.loads(request.COOKIES.get(CONSENT_COOKIE, ""), salt=_COOKIE_SALT, max_age=CONSENT_SECONDS)
    except signing.BadSignature:      # also covers expired, and no cookie at all
        return False
    return said == {"uri": _fingerprint(uri), "uid": str(request.user.id)}


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
    if not uri.startswith("/authorize?") or not _app(redirect):
        log.warning("pmo connector: refused /authorize for redirect_uri=%r", redirect)
        return HttpResponse(status=403)
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    if not _is_admin(request):
        log.warning("pmo connector: %s is signed in but not an instance admin", request.user.email)
        return HttpResponse(status=403)
    if not _consented(request, uri):
        return HttpResponse(status=403)     # denied() turns this one into the question
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
    """Reached instead of a bare 403: the consent question, or which refusal it was."""
    uri = request.headers.get("X-Original-URI", "")
    app = _app(_redirect_uri(uri)) if uri.startswith("/authorize?") else None
    if uri.startswith("/authorize") and not app:
        return _page(
            403,
            "PMO doesn't recognise the app asking to connect",
            "<p>PMO only sends approvals back to Claude or ChatGPT. If you didn't just choose "
            "<b>Connect</b> in one of them, close this page.</p>",
        )
    if request.user.is_authenticated and not _is_admin(request):
        return _not_admin_page(request, uri)
    if app and _is_admin(request) and not _consented(request, uri):
        return _consent_page(request, uri, app)
    return _page(403, "Not allowed", "<p>Sign in to PMO as an admin and try again.</p>")


def _consent_page(request, uri, app):
    token = signing.dumps({"uri": uri, "uid": str(request.user.id)}, salt=_FORM_SALT)
    who, name = html.escape(request.user.email or ""), html.escape(app)
    button = ("font:600 15px system-ui,sans-serif;padding:.6rem 1.2rem;border-radius:8px;"
              "cursor:pointer;margin-right:.5rem;border:1px solid #c9ccd4;")
    response = _page(
        200,
        f"Allow {name} to use PMO?",
        f"<p><b>{name}</b> is asking to connect to PMO as <b>{who}</b>.</p>"
        "<p>It will be able to see every workspace, and create and change work on anyone's "
        "board.</p>"
        f"<p><b>Only allow this if you just chose Connect in {name} yourself.</b> If a link "
        "or a message sent you here, cancel — someone may be trying to connect their own "
        "account to your PMO.</p>"
        f"<form method=post action='{CONSENT_PATH}' style='margin-top:1.6rem'>"
        f"<input type=hidden name=token value='{html.escape(token)}'>"
        f"<button name=decision value=allow style='{button}background:#1c5cab;color:#fff;"
        "border-color:#1c5cab'>Allow</button>"
        f"<button name=decision value=cancel style='{button}background:#fff;color:#1b1d23'>"
        "Cancel</button></form>",
    )
    # Never framed: a consent button inside someone else's page is a clickjacking target.
    response["X-Frame-Options"] = "DENY"
    response["Content-Security-Policy"] = "frame-ancestors 'none'"
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt   # protected below by a signed form token and an Origin check, not Plane's CSRF cookie
@require_POST
def consent(request):
    """The admin's answer to the consent question."""
    if request.headers.get("Origin", "") != f"https://{request.get_host()}":
        return _page(403, "Not allowed", "<p>This approval has to come from the PMO page itself.</p>")
    try:
        asked = signing.loads(request.POST.get("token", ""), salt=_FORM_SALT, max_age=CONSENT_FORM_SECONDS)
    except signing.BadSignature:
        return _page(400, "This approval has expired",
                     "<p>Go back to the app and choose <b>Connect</b> on PMO again.</p>")
    uri = asked.get("uri", "")
    app = _app(_redirect_uri(uri)) if uri.startswith("/authorize?") else None
    if not app or not _is_admin(request) or asked.get("uid") != str(request.user.id):
        return _page(403, "Not allowed", "<p>Sign in to PMO as an admin and try again.</p>")
    if request.POST.get("decision") != "allow":
        log.info("pmo connector: %s declined %s", request.user.email, app)
        return _page(200, "Cancelled", "<p>Nothing was connected. You can close this tab.</p>")
    log.info("pmo connector: %s allowed %s", request.user.email, app)
    response = HttpResponseRedirect(uri)
    response.set_cookie(
        CONSENT_COOKIE,
        signing.dumps({"uri": _fingerprint(uri), "uid": str(request.user.id)}, salt=_COOKIE_SALT),
        max_age=CONSENT_SECONDS,
        path="/authorize",
        secure=True,
        httponly=True,
        samesite="Lax",
    )
    return response
