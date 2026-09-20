import base64

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import ExternalIdentity, IdentityUser, LoginCode, RefreshToken
from app.repositories.refresh_token_repository import SqlAlchemyRefreshTokenRepository
from app.services.token_service import hash_refresh_token
from app.services.token_service import TokenService
from app.signing_keys import SigningKeys, get_signing_keys


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
test_keys = SigningKeys.generate("test-key")


def override_get_db():
    with TestingSession() as db:
        yield db


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_signing_keys] = lambda: test_keys
client = TestClient(app)
basic_credentials = base64.b64encode(
    f"{settings.client_id}:{settings.internal_client_secret}".encode()
).decode()
internal_headers = {"Authorization": f"Basic {basic_credentials}"}


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    client.cookies.clear()
    yield


def register() -> dict:
    response = client.post(
        "/internal/auth/register",
        headers=internal_headers,
        json={
            "name": "Ada Lovelace",
            "email": "ADA@EXAMPLE.COM",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_registration_owns_credentials_and_issues_rs256_access_token():
    auth = register()

    header = jwt.get_unverified_header(auth["access_token"])
    assert header == {"alg": "RS256", "kid": "test-key", "typ": "at+jwt"}
    claims = jwt.decode(
        auth["access_token"],
        test_keys.public_pem,
        algorithms=["RS256"],
        issuer=settings.issuer,
        audience=settings.audience,
    )
    assert claims["sub"] == auth["user"]["id"]
    assert claims["email"] == "ada@example.com"
    assert claims["exp"] - claims["iat"] == settings.access_token_max_age_seconds

    raw_refresh = auth["refresh_token"]
    with Session(engine) as db:
        user = db.scalar(select(IdentityUser))
        token = db.scalar(select(RefreshToken))
        assert user is not None and user.password_hash != "correct horse battery staple"
        assert token is not None and token.token_hash == hash_refresh_token(raw_refresh)


def test_login_and_current_identity():
    register()
    client.cookies.clear()
    response = client.post(
        "/internal/auth/login",
        headers=internal_headers,
        json={"email": "ADA@example.com", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    access_token = response.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"


def test_invalid_login_does_not_reveal_account_presence():
    register()
    missing = client.post(
        "/internal/auth/login",
        headers=internal_headers,
        json={"email": "missing@example.com", "password": "wrong-password"},
    )
    wrong = client.post(
        "/internal/auth/login",
        headers=internal_headers,
        json={"email": "ada@example.com", "password": "wrong-password"},
    )
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json() == {"detail": "Invalid email or password"}
    assert missing.headers["cache-control"] == wrong.headers["cache-control"] == "no-store"


def test_repeated_login_failures_are_rate_limited_for_existing_and_missing_accounts():
    register()
    for email in ("ada@example.com", "missing@example.com"):
        for _ in range(settings.login_failure_limit):
            response = client.post(
                "/internal/auth/login",
                headers=internal_headers,
                json={"email": email, "password": "wrong-password"},
            )
            assert response.status_code == 401
        limited = client.post(
            "/internal/auth/login",
            headers=internal_headers,
            json={"email": email, "password": "wrong-password"},
        )
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0


def test_refresh_rotation_and_reuse_detection_revoke_family():
    first = register()
    first_refresh = first["refresh_token"]
    rotated = client.post(
        "/internal/auth/refresh",
        headers=internal_headers,
        json={"refresh_token": first_refresh},
    )
    assert rotated.status_code == 200
    second_refresh = rotated.json()["refresh_token"]
    assert rotated.json()["access_token"] != first["access_token"]
    assert second_refresh != first_refresh

    replay = client.post(
        "/internal/auth/refresh",
        headers=internal_headers,
        json={"refresh_token": first_refresh},
    )
    assert replay.status_code == 401
    with Session(engine) as db:
        tokens = list(db.scalars(select(RefreshToken)))
        assert len(tokens) == 2
        assert all(token.revoked_at is not None for token in tokens)


def test_logout_revokes_refresh_credential_and_clears_cookie():
    auth = register()
    raw_refresh = auth["refresh_token"]
    response = client.post(
        "/internal/auth/logout",
        headers=internal_headers,
        json={"refresh_token": raw_refresh},
    )
    assert response.status_code == 204
    with Session(engine) as db:
        token = db.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(raw_refresh)
            )
        )
        assert token is not None and token.revoked_at is not None


def test_jwks_publishes_only_the_resource_server_verification_key():
    jwks = client.get("/.well-known/jwks.json").json()
    key = jwks["keys"][0]
    assert key["kid"] == "test-key"
    assert key["alg"] == "RS256"
    assert "d" not in key

    assert client.get("/.well-known/oauth-authorization-server").status_code == 404


def test_google_identity_is_owned_by_auth_service(monkeypatch):
    class SuccessfulGoogleClient:
        async def authorize_access_token(self, _request):
            return {
                "userinfo": {
                    "email": "google@example.com",
                    "email_verified": True,
                    "sub": "google-subject",
                    "name": "Google User",
                    "picture": "https://example.com/avatar.png",
                }
            }

    class SuccessfulOAuth:
        google = SuccessfulGoogleClient()

    monkeypatch.setattr(main_module, "google_oauth_configured", True)
    monkeypatch.setattr(main_module, "oauth", SuccessfulOAuth())
    response = client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(settings.bff_callback_url + "?code=")

    with Session(engine) as db:
        identity = db.scalar(select(ExternalIdentity))
        assert identity is not None
        assert (identity.provider, identity.subject) == ("google", "google-subject")
        assert db.scalar(select(LoginCode)) is not None


def test_one_time_google_code_can_only_be_exchanged_once():
    user_auth = register()
    with TestingSession() as db:
        service = TokenService(SqlAlchemyRefreshTokenRepository(db), db, test_keys)
        code = service.issue_login_code(db.get(IdentityUser, user_auth["user"]["id"]))

    first = client.post(
        "/internal/auth/exchange", headers=internal_headers, json={"code": code}
    )
    second = client.post(
        "/internal/auth/exchange", headers=internal_headers, json={"code": code}
    )
    assert first.status_code == 200
    assert "refresh_token" in first.json()
    assert second.status_code == 401


def test_browser_cannot_call_internal_token_endpoints_without_client_secret():
    assert client.post("/auth/register", json={}).status_code == 404
    assert client.post("/internal/auth/login", json={}).status_code == 401


def test_google_login_forces_account_selection(monkeypatch):
    captured: dict = {}

    class GoogleClient:
        async def authorize_redirect(self, _request, redirect_uri, **kwargs):
            captured["redirect_uri"] = redirect_uri
            captured.update(kwargs)
            from fastapi.responses import RedirectResponse

            return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth")

    class TestOAuth:
        google = GoogleClient()

    monkeypatch.setattr(main_module, "google_oauth_configured", True)
    monkeypatch.setattr(main_module, "oauth", TestOAuth())
    response = client.get("/auth/google/login", follow_redirects=False)

    assert response.status_code == 307
    assert captured == {
        "redirect_uri": f"{settings.public_url}/auth/google/callback",
        "prompt": "select_account",
    }


def test_google_cancel_is_an_expected_redirect(monkeypatch):
    from authlib.integrations.base_client.errors import OAuthError

    class CanceledGoogleClient:
        async def authorize_access_token(self, _request):
            raise OAuthError(error="access_denied")

    class CanceledOAuth:
        google = CanceledGoogleClient()

    monkeypatch.setattr(main_module, "google_oauth_configured", True)
    monkeypatch.setattr(main_module, "oauth", CanceledOAuth())
    response = client.get(
        "/auth/google/callback?error=access_denied&state=state",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == f"{settings.frontend_url}?auth_error=access_denied"


def test_invalid_google_id_token_is_a_safe_redirect(monkeypatch):
    from joserfc.errors import InvalidTokenError

    class InvalidTokenGoogleClient:
        async def authorize_access_token(self, _request):
            raise InvalidTokenError("iat")

    class InvalidTokenOAuth:
        google = InvalidTokenGoogleClient()

    monkeypatch.setattr(main_module, "google_oauth_configured", True)
    monkeypatch.setattr(main_module, "oauth", InvalidTokenOAuth())
    response = client.get(
        "/auth/google/callback?code=one-time-code&state=state",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == f"{settings.frontend_url}?auth_error=oauth_failed"
