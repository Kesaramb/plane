"""
Unit tests for the PMO member connector's Plane sign-in (views/pmo_member_connector.py).

No database: the three data helpers are mocked, so these exercise the flow itself —
who is let through, what is signed, what is refused, and that nothing secret leaks.
"""

import base64
import json
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import resolve

from plane.app.views import pmo_connector
from plane.app.views import pmo_member_connector as mc

AUTHORIZE = (
    "/authorize?response_type=code&client_id=abc"
    "&redirect_uri=https%3A%2F%2Fclaude.ai%2Fapi%2Fmcp%2Fauth_callback"
    "&state=s1&code_challenge=c&code_challenge_method=S256"
)
OTHER_AUTHORIZE = AUTHORIZE.replace("state=s1", "state=s2")
EVIL_AUTHORIZE = "/authorize?response_type=code&client_id=abc&redirect_uri=https%3A%2F%2Fevil.example%2Fcb"
UID = "6f1c2e0a-1111-4222-8333-944455556666"
SECRET = "s" * 40
WORKSPACES = [{"slug": "banking-lk", "name": "Banking-lk"}, {"slug": "kasunr", "name": "KasunR"}]

rf = RequestFactory()


def member(uid=UID, email="kasunpck@gmail.com"):
    return SimpleNamespace(is_authenticated=True, id=uid, email=email)


def grant_for(uri=AUTHORIZE, uid=UID):
    return signing.dumps({"uri": uri, "uid": uid}, salt=mc._GRANT_SALT)


def req_for(uri=AUTHORIZE):
    return signing.dumps({"uri": uri}, salt=mc._REQUEST_SALT)


def query(url, key):
    return parse_qs(urlparse(url).query)[key][0]


@pytest.mark.unit
class TestRoutes:
    @pytest.mark.parametrize(
        "path, view",
        [
            ("/api/pmo/member/gate/", mc.member_gate),
            ("/api/pmo/member/start/", mc.member_start),
            ("/api/pmo/member/denied/", mc.member_denied),
            ("/api/pmo/member/approve/", mc.member_approve),
            ("/api/pmo/member/consent/", mc.member_consent),
            ("/api/pmo/member/landing/", mc.member_landing),
            ("/api/pmo/member/token/", mc.member_token),
        ],
    )
    def test_each_path_reaches_its_view(self, path, view):
        assert resolve(path).func is view

    def test_resume_may_return_to_the_approval_page_only(self):
        assert pmo_connector._resumable(pmo_connector.MEMBER_APPROVE_PATH + "?req=x")
        assert not pmo_connector._resumable("/api/pmo/member/token/")
        assert not pmo_connector._resumable("https://evil.example" + pmo_connector.MEMBER_APPROVE_PATH + "?req=x")


@pytest.mark.unit
class TestGate:
    def gate(self, uri, cookie=None):
        request = rf.get("/api/pmo/member/gate/", HTTP_X_ORIGINAL_URI=uri)
        if cookie is not None:
            request.COOKIES[mc.GRANT_COOKIE] = cookie
        return mc.member_gate(request)

    def test_refuses_an_app_it_does_not_know(self):
        assert self.gate(EVIL_AUTHORIZE, grant_for(EVIL_AUTHORIZE)).status_code == 403

    def test_refuses_anything_but_authorize(self):
        assert self.gate("/token").status_code == 403

    def test_without_an_approval_sends_the_browser_to_start(self):
        assert self.gate(AUTHORIZE).status_code == 401

    def test_with_an_approval_for_this_request_names_the_member_and_nothing_else(self):
        response = self.gate(AUTHORIZE, grant_for())
        assert response.status_code == 200
        assert response["X-PMO-Member-Id"] == UID
        assert "token" not in " ".join(response.headers).lower()

    def test_an_approval_for_a_different_request_does_not_count(self):
        assert self.gate(OTHER_AUTHORIZE, grant_for(AUTHORIZE)).status_code == 401

    def test_an_expired_approval_does_not_count(self, monkeypatch):
        monkeypatch.setattr(mc, "GRANT_SECONDS", -1)
        assert self.gate(AUTHORIZE, grant_for()).status_code == 401

    def test_a_forged_approval_does_not_count(self):
        forged = signing.dumps({"uri": AUTHORIZE, "uid": UID}, salt="something-else")
        assert self.gate(AUTHORIZE, forged).status_code == 401
        assert self.gate(AUTHORIZE, grant_for()[:-2] + "xx").status_code == 401


@pytest.mark.unit
class TestStartAndDenied:
    def test_start_sends_the_browser_to_pmo_with_the_request_signed(self):
        response = mc.member_start(rf.get("/api/pmo/member/start/", HTTP_X_ORIGINAL_URI=AUTHORIZE))
        assert response.status_code == 302
        target = urlparse(response.url)
        assert (target.scheme, target.netloc) == ("https", "pmo.gobrandfactory.com")
        assert target.path == mc.MEMBER_APPROVE_PATH
        assert signing.loads(query(response.url, "req"), salt=mc._REQUEST_SALT) == {"uri": AUTHORIZE}

    def test_start_refuses_an_unknown_app(self):
        response = mc.member_start(rf.get("/api/pmo/member/start/", HTTP_X_ORIGINAL_URI=EVIL_AUTHORIZE))
        assert response.status_code == 403

    def test_denied_explains_the_unknown_app(self):
        response = mc.member_denied(rf.get("/api/pmo/member/denied/"))
        assert response.status_code == 403
        assert b"doesn't recognise" in response.content


@pytest.mark.unit
class TestApprove:
    def approve(self, user, req=None):
        request = rf.get(mc.MEMBER_APPROVE_PATH, {"req": req if req is not None else req_for()})
        request.user = user
        return mc.member_approve(request)

    def test_a_stale_or_forged_request_is_refused(self, monkeypatch):
        assert self.approve(member(), "nonsense").status_code == 400
        assert self.approve(member(), req_for(EVIL_AUTHORIZE)).status_code == 400
        monkeypatch.setattr(mc, "REQUEST_SECONDS", -1)
        assert self.approve(member()).status_code == 400

    def test_signed_out_goes_to_sign_in_and_resume_returns_here(self):
        req = req_for()
        response = self.approve(AnonymousUser(), req)
        assert response.status_code == 302
        assert response.url == pmo_connector.SIGN_IN_URL
        back = base64.urlsafe_b64decode(response.cookies[pmo_connector.RESUME_COOKIE].value).decode()
        assert pmo_connector._resumable(back)
        assert query(back, "req") == req

        resume = rf.get(pmo_connector.RESUME_PATH)
        resume.COOKIES[pmo_connector.RESUME_COOKIE] = response.cookies[pmo_connector.RESUME_COOKIE].value
        resume.user = member()
        assert pmo_connector.resume(resume).url == back

    def test_someone_in_no_workspace_is_told_so(self):
        with mock.patch.object(mc, "_workspaces_of", return_value=[]):
            response = self.approve(member())
        assert response.status_code == 403
        assert b"not in a PMO workspace" in response.content

    def test_a_member_is_asked_with_their_account_and_workspaces(self):
        with mock.patch.object(mc, "_workspaces_of", return_value=WORKSPACES):
            response = self.approve(member())
        page = response.content.decode()
        assert response.status_code == 200
        assert "kasunpck@gmail.com" in page and "Banking-lk" in page and "KasunR" in page
        assert response["X-Frame-Options"] == "DENY"
        token = page.split("name=token value='")[1].split("'")[0]
        assert signing.loads(token, salt=mc._FORM_SALT) == {"uri": AUTHORIZE, "uid": UID}

    def test_workspace_names_are_escaped(self):
        with mock.patch.object(mc, "_workspaces_of", return_value=[{"slug": "x", "name": "<script>x</script>"}]):
            page = self.approve(member()).content.decode()
        assert "<script>" not in page and "&lt;script&gt;" in page


@pytest.mark.unit
class TestConsent:
    def consent(self, user, token, decision="allow", origin="https://pmo.gobrandfactory.com"):
        request = rf.post(
            mc.CONSENT_PATH,
            {"token": token, "decision": decision},
            HTTP_ORIGIN=origin,
            HTTP_HOST="pmo.gobrandfactory.com",
        )
        request.user = user
        return mc.member_consent(request)

    def form(self, uri=AUTHORIZE, uid=UID):
        return signing.dumps({"uri": uri, "uid": uid}, salt=mc._FORM_SALT)

    def test_must_come_from_the_pmo_page(self):
        assert self.consent(member(), self.form(), origin="https://evil.example").status_code == 403

    def test_an_expired_question_is_refused(self, monkeypatch):
        monkeypatch.setattr(mc, "FORM_SECONDS", -1)
        assert self.consent(member(), self.form()).status_code == 400

    def test_someone_else_cannot_answer(self):
        other = member(uid="00000000-0000-4000-8000-000000000000")
        assert self.consent(other, self.form()).status_code == 403
        assert self.consent(AnonymousUser(), self.form()).status_code == 403

    def test_an_unknown_app_is_refused_even_if_signed(self):
        assert self.consent(member(), self.form(EVIL_AUTHORIZE)).status_code == 403

    def test_cancel_connects_nothing(self):
        response = self.consent(member(), self.form(), decision="cancel")
        assert response.status_code == 200
        assert b"Cancelled" in response.content

    def test_allow_hands_a_signed_approval_to_the_member_host(self):
        response = self.consent(member(), self.form())
        assert response.status_code == 302
        target = urlparse(response.url)
        assert (target.netloc, target.path) == ("my.gobrandfactory.com", mc.LANDING_PATH)
        grant = signing.loads(query(response.url, "grant"), salt=mc._GRANT_SALT)
        assert grant == {"uri": AUTHORIZE, "uid": UID}


@pytest.mark.unit
class TestLanding:
    def test_turns_the_approval_into_a_cookie_for_authorize(self):
        grant = grant_for()
        response = mc.member_landing(rf.get(mc.LANDING_PATH, {"grant": grant}))
        assert response.status_code == 302
        assert response.url == AUTHORIZE
        cookie = response.cookies[mc.GRANT_COOKIE]
        assert cookie.value == grant
        assert cookie["path"] == "/authorize"
        assert cookie["httponly"] and cookie["secure"]
        assert int(cookie["max-age"]) == mc.GRANT_SECONDS

    def test_refuses_a_bad_or_late_approval(self, monkeypatch):
        assert mc.member_landing(rf.get(mc.LANDING_PATH, {"grant": "nope"})).status_code == 400
        assert mc.member_landing(rf.get(mc.LANDING_PATH, {"grant": grant_for(EVIL_AUTHORIZE)})).status_code == 400
        monkeypatch.setattr(mc, "GRANT_SECONDS", -1)
        assert mc.member_landing(rf.get(mc.LANDING_PATH, {"grant": grant_for()})).status_code == 400


@pytest.mark.unit
class TestToken:
    def call(self, body, secret=SECRET, method="post"):
        headers = {"HTTP_X_PMO_CONNECTOR_SECRET": secret} if secret is not None else {}
        if method == "get":
            return mc.member_token(rf.get("/api/pmo/member/token/", **headers))
        raw = body if isinstance(body, str) else json.dumps(body)
        return mc.member_token(rf.post("/api/pmo/member/token/", raw, content_type="application/json", **headers))

    @pytest.fixture(autouse=True)
    def configured(self, monkeypatch):
        monkeypatch.setenv("PMO_MEMBER_CONNECTOR_SECRET", SECRET)

    def test_without_a_configured_secret_nothing_is_handed_out(self, monkeypatch):
        monkeypatch.delenv("PMO_MEMBER_CONNECTOR_SECRET")
        assert self.call({"uid": UID}, secret="").status_code == 404

    def test_a_short_configured_secret_counts_as_none(self, monkeypatch):
        monkeypatch.setenv("PMO_MEMBER_CONNECTOR_SECRET", "short")
        assert self.call({"uid": UID}, secret="short").status_code == 404

    def test_the_wrong_secret_gets_404(self):
        assert self.call({"uid": UID}, secret="x" * 40).status_code == 404
        assert self.call({"uid": UID}, secret=None).status_code == 404

    def test_only_post(self):
        assert self.call(None, method="get").status_code == 405

    def test_bad_json(self):
        assert self.call("{not json").status_code == 400

    def test_unknown_user(self):
        with mock.patch.object(mc, "_active_user", return_value=None):
            assert self.call({"uid": UID, "create": True}).status_code == 404

    def test_renewal_does_not_mint_a_token_the_member_deleted(self):
        user = SimpleNamespace(email="kasunpck@gmail.com", display_name="kasun")
        with mock.patch.object(mc, "_active_user", return_value=user), \
                mock.patch.object(mc, "_connector_token", return_value=None) as token:
            response = self.call({"uid": UID})
        assert response.status_code == 404
        token.assert_called_once_with(user, create=False)

    def test_connect_returns_the_members_token_and_workspaces(self):
        user = SimpleNamespace(email="kasunpck@gmail.com", display_name="kasun")
        minted = SimpleNamespace(token="plane_api_abc")
        with mock.patch.object(mc, "_active_user", return_value=user), \
                mock.patch.object(mc, "_connector_token", return_value=minted) as token, \
                mock.patch.object(mc, "_workspaces_of", return_value=WORKSPACES):
            response = self.call({"uid": UID, "create": True})
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert json.loads(response.content) == {
            "token": "plane_api_abc", "email": "kasunpck@gmail.com", "name": "kasun", "workspaces": WORKSPACES,
        }
        token.assert_called_once_with(user, create=True)


@pytest.mark.unit
class TestData:
    def test_active_user_ignores_junk_without_querying(self):
        with mock.patch.object(mc, "User") as User:
            assert mc._active_user(None) is None
            assert mc._active_user(123) is None
            User.objects.filter.assert_not_called()

    def test_active_user_survives_a_malformed_id(self):
        with mock.patch.object(mc, "User") as User:
            User.objects.filter.side_effect = ValidationError("not a uuid")
            assert mc._active_user("not-a-uuid") is None

    def test_connector_token_reuses_a_live_one(self):
        live = object()
        with mock.patch.object(mc, "APIToken") as APIToken:
            APIToken.objects.filter.return_value.filter.return_value.order_by.return_value.first.return_value = live
            assert mc._connector_token("user", create=True) is live
            APIToken.objects.create.assert_not_called()

    def test_connector_token_mints_only_when_asked(self):
        with mock.patch.object(mc, "APIToken") as APIToken:
            APIToken.objects.filter.return_value.filter.return_value.order_by.return_value.first.return_value = None
            assert mc._connector_token("user", create=False) is None
            APIToken.objects.create.assert_not_called()
            mc._connector_token("user", create=True)
            APIToken.objects.create.assert_called_once_with(
                user="user", label=mc.TOKEN_LABEL, description=mc.TOKEN_DESCRIPTION, user_type=0
            )
