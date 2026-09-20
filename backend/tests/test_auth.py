import base64
import uuid
from datetime import datetime, timedelta, timezone

import fakeredis
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.infrastructure.redis import get_redis_client
from app.main import app
from app.models import BrowserAuthSession
from app.schemas import IdentityUserRead, InternalAuthTokenResponse
from app.services.auth_service_client import get_auth_service_client
from app.services.access_token_verifier import (
    AccessTokenVerifier,
    AuthPrincipal,
    InvalidAccessToken,
    get_access_token_verifier,
)
from app.services.user_service import get_user_service


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_get_db():
    with TestingSession() as db:
        yield db


class FakeVerifier:
    def verify(self, token: str) -> AuthPrincipal:
        if token != "valid-token":
            raise InvalidAccessToken
        return AuthPrincipal(
            issuer=settings.auth_issuer,
            subject="4d067f9e-e915-43e2-bd2a-1f03da788bbb",
            email="ada@example.com",
            name="Ada Lovelace",
            avatar_url=None,
        )


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_access_token_verifier] = lambda: FakeVerifier()
app.dependency_overrides[get_redis_client] = lambda: fakeredis.aioredis.FakeRedis(
    decode_responses=True
)
client = TestClient(app)


class FakeAuthClient:
    refresh_values: list[str] = []

    def _token(self, suffix: str) -> InternalAuthTokenResponse:
        now = datetime.now(timezone.utc)
        return InternalAuthTokenResponse(
            access_token=f"access-{suffix}",
            refresh_token=f"refresh-{suffix}",
            expires_in=900,
            user=IdentityUserRead(
                id="4d067f9e-e915-43e2-bd2a-1f03da788bbb",
                email="ada@example.com",
                name="Ada Lovelace",
                avatar_url=None,
                is_active=True,
                created_at=now,
                updated_at=now,
            ),
        )

    async def register(self, _payload):
        return self._token("register")

    async def login(self, _payload):
        return self._token("login")

    async def refresh(self, refresh_token):
        self.refresh_values.append(refresh_token)
        return self._token("rotated")

    async def logout(self, refresh_token):
        self.refresh_values.append(refresh_token)

    async def exchange(self, _code):
        return self._token("google")

    async def google_status(self):
        return {"configured": True}


fake_auth_client = FakeAuthClient()
app.dependency_overrides[get_auth_service_client] = lambda: fake_auth_client


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    client.cookies.clear()
    fake_auth_client.refresh_values.clear()
    yield


def bearer(token: str = "valid-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_bff_auth_are_public_but_business_api_is_not():
    assert client.get("/health").status_code == 200
    assert client.get("/auth/google/status").status_code == 200
    google_login = client.get("/auth/google/login", follow_redirects=False)
    assert google_login.status_code == 307
    assert google_login.headers["location"].endswith("/auth/google/login")
    assert client.post("/auth/login", json={}).status_code == 422


def test_browser_receives_access_token_but_refresh_stays_encrypted_server_side():
    response = client.post(
        "/auth/login",
        json={"email": "ada@example.com", "password": "password"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] == "access-login"
    assert "refresh_token" not in response.json()
    browser_session = client.cookies.get(settings.bff_session_cookie_name)
    assert browser_session and browser_session != "refresh-login"

    with TestingSession() as db:
        stored = db.scalar(select(BrowserAuthSession))
        assert stored is not None
        assert stored.encrypted_refresh_token != "refresh-login"
        assert "refresh-login" not in stored.encrypted_refresh_token


def test_bff_uses_and_rotates_server_side_refresh_token():
    client.post(
        "/auth/login",
        json={"email": "ada@example.com", "password": "password"},
    )
    old_session = client.cookies.get(settings.bff_session_cookie_name)
    response = client.post("/auth/refresh")
    new_session = client.cookies.get(settings.bff_session_cookie_name)

    assert response.status_code == 200
    assert response.json()["access_token"] == "access-rotated"
    assert "refresh_token" not in response.json()
    assert fake_auth_client.refresh_values == ["refresh-login"]
    assert new_session != old_session


def test_bff_rejects_cross_site_auth_requests_and_clears_invalid_session():
    rejected = client.post(
        "/auth/login",
        headers={"Origin": "https://attacker.example"},
        json={"email": "ada@example.com", "password": "password"},
    )
    assert rejected.status_code == 403

    client.cookies.set(
        settings.bff_session_cookie_name,
        "invalid-session",
        domain="testserver.local",
        path="/auth",
    )
    invalid = client.post("/auth/refresh")
    assert invalid.status_code == 401
    assert client.cookies.get(settings.bff_session_cookie_name) is None


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/users/me", None),
        ("get", "/users", None),
        ("get", "/users/1", None),
        ("patch", "/users/1", {"name": "Updated User"}),
        ("delete", "/users/1", None),
        ("get", "/api/v1/workspaces", None),
        ("post", "/api/v1/workspaces", {"name": "Acme", "slug": "acme"}),
        ("get", "/api/v1/workspaces/workspace-1/teams", None),
        ("post", "/api/v1/workspaces/workspace-1/teams", {"name": "Core", "issue_prefix": "CORE"}),
        ("get", "/api/v1/workspaces/workspace-1/teams/team-1/workflow-states", None),
        ("get", "/api/v1/workspaces/workspace-1/teams/team-1/cycles", None),
        ("get", "/api/v1/workspaces/workspace-1/teams/team-1/labels", None),
        ("get", "/api/v1/workspaces/workspace-1/teams/team-1/issues", None),
        ("get", "/api/v1/workspaces/workspace-1/issues/CORE-1", None),
        ("get", "/api/v1/me/issues", None),
    ],
)
def test_every_business_route_requires_access_token(method, path, json_body):
    response = client.request(method, path, json=json_body)
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
    assert response.headers["www-authenticate"] == "Bearer"


def test_authentication_runs_before_business_service_logic():
    class TrackingUserService:
        called = False

        def provision_identity(self, _principal):
            self.called = True
            raise AssertionError("identity provisioning must not run without authentication")

        def list(self, **_kwargs):
            self.called = True
            raise AssertionError("handler logic must not run without authentication")

    service = TrackingUserService()
    app.dependency_overrides[get_user_service] = lambda: service
    try:
        response = client.get("/users")
    finally:
        app.dependency_overrides.pop(get_user_service)

    assert response.status_code == 401
    assert service.called is False


def test_valid_identity_is_provisioned_before_business_logic():
    response = client.get("/users/me", headers=bearer())

    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"
    assert response.json()["auth_subject"] == "4d067f9e-e915-43e2-bd2a-1f03da788bbb"
    users = client.get("/users", headers=bearer()).json()
    assert [user["email"] for user in users] == ["ada@example.com"]


def test_invalid_token_is_rejected():
    response = client.get("/users/me", headers=bearer("invalid"))
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired access token"


def test_authenticated_user_cannot_modify_another_profile():
    own = client.get("/users/me", headers=bearer()).json()
    response = client.patch(
        f"/users/{own['id'] + 1}",
        headers=bearer(),
        json={"name": "Not allowed"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "You may only modify your own profile"


def _base64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_real_verifier_enforces_signature_issuer_audience_and_type():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    pyjwk = jwt.PyJWK.from_dict(
        {
            "kty": "RSA",
            "kid": "test-key",
            "alg": "RS256",
            "use": "sig",
            "n": _base64url_uint(public_numbers.n),
            "e": _base64url_uint(public_numbers.e),
        }
    )

    class StaticJwksClient:
        def get_signing_key_from_jwt(self, _token):
            return pyjwk

    now = datetime.now(timezone.utc)
    claims = {
        "iss": settings.auth_issuer,
        "sub": str(uuid.uuid4()),
        "aud": settings.auth_audience,
        "client_id": settings.auth_client_id,
        "email": "security@example.com",
        "name": "Security Test",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    token = jwt.encode(
        claims,
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key", "typ": "at+jwt"},
    )

    principal = AccessTokenVerifier(StaticJwksClient()).verify(token)
    assert principal.email == "security@example.com"

    wrong_audience = jwt.encode(
        {**claims, "aud": "another-api"},
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key", "typ": "at+jwt"},
    )
    with pytest.raises(InvalidAccessToken):
        AccessTokenVerifier(StaticJwksClient()).verify(wrong_audience)

    wrong_client = jwt.encode(
        {**claims, "client_id": "another-client"},
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key", "typ": "at+jwt"},
    )
    with pytest.raises(InvalidAccessToken):
        AccessTokenVerifier(StaticJwksClient()).verify(wrong_client)

    too_long = jwt.encode(
        {**claims, "exp": now + timedelta(minutes=16)},
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key", "typ": "at+jwt"},
    )
    with pytest.raises(InvalidAccessToken):
        AccessTokenVerifier(StaticJwksClient()).verify(too_long)


def test_openapi_fails_closed_for_every_business_operation():
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["AccessToken"] == {
        "type": "http",
        "description": "Short-lived JWT issued by Zhitong Auth Service",
        "scheme": "bearer",
    }
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if path == "/health" or path.startswith("/auth/"):
                assert operation.get("security") in (None, [])
            else:
                assert operation.get("security") == [{"AccessToken": []}], (
                    f"{method.upper()} {path} must require authentication"
                )
