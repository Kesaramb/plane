# Fork addition: Plane sign-in for the PMO *member* connector — the personal MCP server
# (pmo_mcp_user.py) at my.gobrandfactory.com, which acts as whoever connects it.
#
# pmo_connector.py does this for the manager connector; this file borrows its pieces
# (trusted apps, pages, the sign-in round trip). Three things are different.
#
# ANY PLANE USER, ACTING AS THEMSELVES
# The manager connector reads every workspace, so it is for instance admins. This one
# reaches Plane with the person's own API token, so Plane decides what they can see and
# their work carries their name. Having a Plane account is the right bar here.
#
# TWO HOSTS
# The connector needs its own origin (OAuth discovery is per issuer, and the manager
# connector owns pmo.gobrandfactory.com's root), but Plane's session cookie belongs to
# pmo.gobrandfactory.com alone. /authorize on the member host cannot see who is signed in,
# so the browser makes a round trip:
#
#   my.  /authorize                 gate: no grant -> 401 -> start
#   my.  /api/pmo/member/start/     -> pmo. approve/?req=<signed authorize URI>
#   pmo. /api/pmo/member/approve/   sign in if needed (pmo_connector's resume), then ask
#   pmo. /api/pmo/member/consent/   Allow -> my. landing/?grant=<signed uri + uid>
#   my.  /api/pmo/member/landing/   grant -> a two-minute cookie for /authorize
#   my.  /authorize                 gate: grant for this exact request -> 200, X-PMO-Member-Id
#
# The question is asked for the reason pmo_connector.py gives: Claude and ChatGPT share one
# callback across all their accounts, so without it a stranger could send a member an
# /authorize link and receive a connector that acts as that member.
#
# THE TOKEN NEVER CROSSES NGINX
# Every route here is also reachable at pmo.gobrandfactory.com/api/..., so the gate answers
# nothing secret: who, and no more. The MCP server then fetches that person's Plane token
# from token/ over loopback, proving itself with PMO_MEMBER_CONNECTOR_SECRET (set in both).
# No secret configured, no token, ever. token/ mints only when asked at connect time, which
# only follows a fresh approval; renewals just look it up, so deleting "Claude connector"
# from Plane's API tokens really does cut the connection off.
import hmac
import html
import json
import logging
import os
from urllib.parse import urlencode

from django.core import signing
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from plane.app.views.pmo_connector import MEMBER_APPROVE_PATH, _app, _page, _redirect_uri, _sign_in_then
from plane.db.models import APIToken, User, WorkspaceMember

log = logging.getLogger("plane.api")

PMO_URL = os.environ.get("PMO_PUBLIC_URL", "https://pmo.gobrandfactory.com").rstrip("/")
MEMBER_URL = os.environ.get("PMO_MEMBER_CONNECTOR_URL", "https://my.gobrandfactory.com").rstrip("/")
CONSENT_PATH = "/api/pmo/member/consent/"
LANDING_PATH = "/api/pmo/member/landing/"

REQUEST_SECONDS = 600   # from Connect to Allow, including a sign-in on the way
FORM_SECONDS = 600      # how long the question may sit on screen
GRANT_SECONDS = 120     # from Allow to the browser arriving back at /authorize
GRANT_COOKIE = "pmo_member_grant"
_REQUEST_SALT = "pmo-member-request"
_FORM_SALT = "pmo-member-consent-form"
_GRANT_SALT = "pmo-member-grant"

TOKEN_LABEL = "Claude connector"
TOKEN_DESCRIPTION = "Made when you connected PMO to an AI app. Delete it to disconnect."
_MIN_SECRET = 32


def _signed(value, salt, max_age):
    """A dict this server signed within max_age seconds, or None."""
    try:
        data = signing.loads(value or "", salt=salt, max_age=max_age)
    except signing.BadSignature:      # also covers expired
        return None
    return data if isinstance(data, dict) else None


def _authorize_for_app(uri):
    """The app an /authorize request would send its code to, or None."""
    return _app(_redirect_uri(uri)) if isinstance(uri, str) and uri.startswith("/authorize?") else None


def _grant(value):
    grant = _signed(value, _GRANT_SALT, GRANT_SECONDS)
    if grant and _authorize_for_app(grant.get("uri")) and isinstance(grant.get("uid"), str):
        return grant
    return None


def _unknown_app_page():
    return _page(
        403,
        "PMO doesn't recognise the app asking to connect",
        "<p>PMO only sends approvals back to Claude or ChatGPT. If you didn't just choose "
        "<b>Connect</b> in one of them, close this page.</p>",
    )


def _start_again_page(title):
    return _page(400, title, "<p>Go back to the app and choose <b>Connect</b> on PMO again.</p>")


# ---- data ---------------------------------------------------------------------------


def _active_user(uid):
    if not isinstance(uid, str) or not uid:
        return None
    try:
        return User.objects.filter(id=uid, is_active=True).first()
    except (ValueError, ValidationError):
        return None


def _workspaces_of(user):
    rows = (
        WorkspaceMember.objects.filter(member=user, is_active=True, workspace__deleted_at__isnull=True)
        .select_related("workspace")
        .order_by("workspace__name")
    )
    return [{"slug": m.workspace.slug, "name": m.workspace.name} for m in rows]


def _connector_token(user, create):
    """The person's live connector token; a new one only when create is set."""
    live = (
        APIToken.objects.filter(user=user, label=TOKEN_LABEL, is_active=True, is_service=False)
        .filter(Q(expired_at__isnull=True) | Q(expired_at__gt=timezone.now()))
        .order_by("-created_at")
        .first()
    )
    if live or not create:
        return live
    return APIToken.objects.create(user=user, label=TOKEN_LABEL, description=TOKEN_DESCRIPTION, user_type=0)


# ---- member host (my.) ----------------------------------------------------------------


def member_gate(request):
    """auth_request target for /authorize on the member host. Says who; nothing secret."""
    uri = request.headers.get("X-Original-URI", "")
    if not _authorize_for_app(uri):
        log.warning("pmo member connector: refused /authorize for redirect_uri=%r", _redirect_uri(uri))
        return HttpResponse(status=403)
    grant = _grant(request.COOKIES.get(GRANT_COOKIE, ""))
    if not grant or grant["uri"] != uri:
        return HttpResponse(status=401)
    response = HttpResponse(status=200)
    response["X-PMO-Member-Id"] = grant["uid"]
    return response


def member_start(request):
    """Reached instead of a 401 on the member host: off to pmo. to sign in and approve."""
    uri = request.headers.get("X-Original-URI", "")
    if not _authorize_for_app(uri):
        return _unknown_app_page()
    req = signing.dumps({"uri": uri}, salt=_REQUEST_SALT)
    return HttpResponseRedirect(f"{PMO_URL}{MEMBER_APPROVE_PATH}?{urlencode({'req': req})}")


def member_denied(request):
    """Reached instead of a 403 on the member host — the gate refuses only unknown apps."""
    return _unknown_app_page()


def member_landing(request):
    """Back on the member host after Allow: the approval becomes a cookie /authorize sees."""
    raw = request.GET.get("grant", "")
    grant = _grant(raw)
    if not grant:
        return _start_again_page("This approval has expired")
    response = HttpResponseRedirect(grant["uri"])
    response.set_cookie(
        GRANT_COOKIE, raw, max_age=GRANT_SECONDS, path="/authorize", secure=True, httponly=True, samesite="Lax"
    )
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


# ---- pmo. -----------------------------------------------------------------------------


def member_approve(request):
    """Sign in if needed, then ask whether to connect."""
    req = request.GET.get("req", "")
    uri = (_signed(req, _REQUEST_SALT, REQUEST_SECONDS) or {}).get("uri")
    app = _authorize_for_app(uri)
    if not app:
        return _start_again_page("This connection request has expired")
    if not request.user.is_authenticated:
        return _sign_in_then(f"{MEMBER_APPROVE_PATH}?{urlencode({'req': req})}")
    workspaces = _workspaces_of(request.user)
    if not workspaces:
        who = html.escape(request.user.email or "this account")
        return _page(
            403,
            "You're not in a PMO workspace yet",
            f"<p>You're signed in as <b>{who}</b>, but no workspace has added you, so there is "
            "nothing to connect to.</p><p>Ask whoever runs your workspace to add you, then "
            "choose <b>Connect</b> again.</p>",
        )
    return _consent_page(request, uri, app, workspaces)


def _consent_page(request, uri, app, workspaces):
    token = signing.dumps({"uri": uri, "uid": str(request.user.id)}, salt=_FORM_SALT)
    who, name = html.escape(request.user.email or ""), html.escape(app)
    where = ", ".join(f"<b>{html.escape(w['name'])}</b>" for w in workspaces)
    button = ("font:600 15px system-ui,sans-serif;padding:.6rem 1.2rem;border-radius:8px;"
              "cursor:pointer;margin-right:.5rem;border:1px solid #c9ccd4;")
    response = _page(
        200,
        f"Allow {name} to use PMO as you?",
        f"<p><b>{name}</b> is asking to connect to PMO as <b>{who}</b>.</p>"
        f"<p>It will be able to see and change work in your workspaces ({where}), just as "
        "you can in Plane. It can't reach any workspace you aren't in.</p>"
        f"<p><b>Only allow this if you just chose Connect in {name} yourself.</b> If a link "
        "or a message sent you here, cancel — someone may be trying to connect their own "
        "account to your work.</p>"
        "<p style='color:#5b616e'>To disconnect later, delete <b>Claude connector</b> from "
        "your API tokens in Plane's settings.</p>"
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
def member_consent(request):
    """The member's answer to the question."""
    if request.headers.get("Origin", "") != f"https://{request.get_host()}":
        return _page(403, "Not allowed", "<p>This approval has to come from the PMO page itself.</p>")
    asked = _signed(request.POST.get("token", ""), _FORM_SALT, FORM_SECONDS)
    if asked is None:
        return _start_again_page("This approval has expired")
    uri = asked.get("uri", "")
    app = _authorize_for_app(uri)
    if not app or not request.user.is_authenticated or asked.get("uid") != str(request.user.id):
        return _page(403, "Not allowed", "<p>Sign in to PMO and try again.</p>")
    if request.POST.get("decision") != "allow":
        log.info("pmo member connector: %s declined %s", request.user.email, app)
        return _page(200, "Cancelled", "<p>Nothing was connected. You can close this tab.</p>")
    log.info("pmo member connector: %s allowed %s", request.user.email, app)
    grant = signing.dumps({"uri": uri, "uid": str(request.user.id)}, salt=_GRANT_SALT)
    return HttpResponseRedirect(f"{MEMBER_URL}{LANDING_PATH}?{urlencode({'grant': grant})}")


# ---- loopback, for the member MCP server --------------------------------------------


@csrf_exempt   # a machine caller with a shared secret, not a browser
def member_token(request):
    """The connecting person's Plane token, for the member MCP server only.

    Without the right secret this answers 404 to everything, so the route is not even
    confirmed to exist. A secret shorter than _MIN_SECRET counts as none at all.
    """
    secret = os.environ.get("PMO_MEMBER_CONNECTOR_SECRET", "")
    offered = request.headers.get("X-PMO-Connector-Secret", "")
    if len(secret) < _MIN_SECRET or not hmac.compare_digest(offered.encode(), secret.encode()):
        return HttpResponse(status=404)
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "body must be JSON"}, status=400)
    user = _active_user(body.get("uid") if isinstance(body, dict) else None)
    if user is None:
        return JsonResponse({"error": "no such active user"}, status=404)
    token = _connector_token(user, create=bool(body.get("create")))
    if token is None:
        return JsonResponse({"error": "not connected; the connector token was removed"}, status=404)
    response = JsonResponse({
        "token": token.token,
        "email": user.email,
        "name": user.display_name,
        "workspaces": _workspaces_of(user),
    })
    response["Cache-Control"] = "no-store"
    return response
