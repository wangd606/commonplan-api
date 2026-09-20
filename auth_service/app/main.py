from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from joserfc.errors import JoseError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from app.auth import CurrentIdentity, InternalClient
from app.config import settings
from app.models import IdentityUser
from app.schemas import (
    IdentityUserRead,
    InternalTokenResponse,
    LoginCodeExchangeRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
)
from app.services.identity_service import (
    IdentityConflict,
    IdentityServiceDep,
    LoginRateLimited,
)
from app.services.token_service import InvalidRefreshToken, TokenPair, TokenServiceDep
from app.signing_keys import SigningKeys, get_signing_keys


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    get_signing_keys()
    yield


app = FastAPI(title="Zhitong Auth Service", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie=settings.oauth_state_cookie_name,
    max_age=settings.oauth_state_max_age_seconds,
    path="/auth/google",
    same_site=settings.cookie_same_site,
    https_only=settings.cookie_secure,
)

oauth = OAuth()
google_oauth_configured = bool(settings.google_client_id and settings.google_client_secret)
if google_oauth_configured:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


@app.middleware("http")
async def prevent_credential_response_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/auth/", "/internal/auth/")):
        prevent_auth_response_caching(response)
    return response


def prevent_auth_response_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def internal_token_response(user: IdentityUser, pair: TokenPair) -> InternalTokenResponse:
    return InternalTokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.access_expires_in,
        user=IdentityUserRead.model_validate(user),
    )


def frontend_auth_error_url(error_code: str) -> str:
    parts = urlsplit(settings.frontend_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["auth_error"] = error_code
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/.well-known/jwks.json")
def jwks(
    signing_keys: Annotated[SigningKeys, Depends(get_signing_keys)],
) -> dict[str, list[dict[str, str]]]:
    return {"keys": [signing_keys.jwk()]}


@app.get("/auth/google/status")
def google_status() -> dict[str, bool]:
    return {"configured": google_oauth_configured}


@app.post("/internal/auth/register", response_model=InternalTokenResponse, status_code=201)
async def register_internal(
    payload: RegisterRequest,
    response: Response,
    _client: InternalClient,
    identity_service: IdentityServiceDep,
    token_service: TokenServiceDep,
) -> InternalTokenResponse:
    try:
        user = await run_in_threadpool(
            identity_service.register,
            email=str(payload.email),
            name=payload.name,
            password=payload.password,
        )
    except IdentityConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from exc
    pair = await run_in_threadpool(token_service.issue_pair, user)
    prevent_auth_response_caching(response)
    return internal_token_response(user, pair)


@app.post("/internal/auth/login", response_model=InternalTokenResponse)
async def login_internal(
    payload: LoginRequest,
    response: Response,
    _client: InternalClient,
    identity_service: IdentityServiceDep,
    token_service: TokenServiceDep,
) -> InternalTokenResponse:
    try:
        user = await run_in_threadpool(
            identity_service.authenticate_password,
            email=str(payload.email),
            password=payload.password,
        )
    except LoginRateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    pair = await run_in_threadpool(token_service.issue_pair, user)
    prevent_auth_response_caching(response)
    return internal_token_response(user, pair)


@app.post("/internal/auth/refresh", response_model=InternalTokenResponse)
async def refresh_internal(
    payload: RefreshRequest,
    response: Response,
    _client: InternalClient,
    identity_service: IdentityServiceDep,
    token_service: TokenServiceDep,
) -> InternalTokenResponse:
    try:
        user_id, pair = await run_in_threadpool(token_service.rotate, payload.refresh_token)
    except InvalidRefreshToken:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user = await run_in_threadpool(identity_service.get_active_by_id, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Account is no longer active")
    prevent_auth_response_caching(response)
    return internal_token_response(user, pair)


@app.get("/auth/me", response_model=IdentityUserRead)
def me(current_identity: CurrentIdentity) -> IdentityUser:
    return current_identity


@app.post("/internal/auth/logout", status_code=204)
async def logout_internal(
    payload: RefreshRequest,
    response: Response,
    _client: InternalClient,
    token_service: TokenServiceDep,
) -> None:
    await run_in_threadpool(token_service.revoke, payload.refresh_token)
    prevent_auth_response_caching(response)


@app.post("/internal/auth/exchange", response_model=InternalTokenResponse)
async def exchange_login_code(
    payload: LoginCodeExchangeRequest,
    response: Response,
    _client: InternalClient,
    token_service: TokenServiceDep,
) -> InternalTokenResponse:
    try:
        user, pair = await run_in_threadpool(
            token_service.exchange_login_code, payload.code
        )
    except InvalidRefreshToken as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired login code") from exc
    prevent_auth_response_caching(response)
    return internal_token_response(user, pair)


@app.get("/auth/google/login")
async def google_login(request: Request):
    if not google_oauth_configured:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    return await oauth.google.authorize_redirect(
        request,
        f"{settings.public_url}/auth/google/callback",
        prompt="select_account",
    )


@app.get("/auth/google/callback")
async def google_callback(
    request: Request,
    identity_service: IdentityServiceDep,
    token_service: TokenServiceDep,
):
    if not google_oauth_configured:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        request.session.clear()
        error_code = "access_denied" if exc.error == "access_denied" else "oauth_failed"
        return RedirectResponse(frontend_auth_error_url(error_code), status_code=302)
    except JoseError:
        # Keep OIDC claim verification strict, but never expose a validation
        # traceback or leave the browser on the auth service error page.
        request.session.clear()
        return RedirectResponse(frontend_auth_error_url("oauth_failed"), status_code=302)

    profile = token.get("userinfo")
    if profile is None:
        profile = await oauth.google.parse_id_token(request, token)
    email = profile.get("email")
    subject = profile.get("sub")
    if not email or not subject:
        raise HTTPException(status_code=400, detail="Google profile is missing email or subject")
    if profile.get("email_verified") is not True:
        raise HTTPException(status_code=400, detail="Google email is not verified")
    try:
        user = await run_in_threadpool(
            identity_service.upsert_google_identity,
            email=email,
            subject=subject,
            name=profile.get("name") or email,
            avatar_url=profile.get("picture"),
        )
    except IdentityConflict as exc:
        raise HTTPException(status_code=409, detail="Google account could not be linked") from exc

    request.session.clear()
    code = await run_in_threadpool(token_service.issue_login_code, user)
    parts = urlsplit(settings.bff_callback_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["code"] = code
    callback_url = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
    response = RedirectResponse(callback_url, status_code=302)
    prevent_auth_response_caching(response)
    return response
