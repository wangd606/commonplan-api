from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth_routes import router as auth_router
from app.auth import CurrentUser, SelfUser, get_current_user
from app.config import settings
from app.infrastructure.redis import create_redis_client
from app.models import User
from app.schemas import UserRead, UserUpdate
from app.services.user_service import UserConflict, UserServiceDep
from app.stores.session_store import SessionStoreUnavailable
from app.workspace_routes import router as workspace_router
from app.issue_routes import router as issue_router
from app.project_routes import router as project_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis_client = await create_redis_client()
    app.state.redis_client = redis_client
    try:
        yield
    finally:
        await redis_client.aclose()


app = FastAPI(title="Project Zhitong Business API", lifespan=lifespan)

# Every business operation belongs to this router. Authentication is therefore
# fail-closed by default and runs before endpoint or service logic. Only health
# and API metadata live on the unprotected application router.
business_router = APIRouter(dependencies=[Depends(get_current_user)])
business_router.include_router(workspace_router)
business_router.include_router(issue_router)
business_router.include_router(project_router)


@app.exception_handler(SessionStoreUnavailable)
async def session_store_unavailable_handler(
    _request: Request,
    _exc: SessionStoreUnavailable,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Session service unavailable"},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prevent_auth_response_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/auth/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@business_router.get("/users/me", response_model=UserRead)
def get_me(current_user: CurrentUser) -> User:
    return current_user


@business_router.get("/users", response_model=list[UserRead])
def list_users(
    user_service: UserServiceDep,
    include_deleted: bool = False,
) -> list[User]:
    return user_service.list(include_deleted=include_deleted)


@business_router.get("/users/{user_id}", response_model=UserRead)
def get_user(user_id: int, user_service: UserServiceDep) -> User:
    user = user_service.get_active_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@business_router.patch("/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    user_service: UserServiceDep,
    _authorized_user: SelfUser,
) -> User:
    user = user_service.get_active_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        return user_service.update(user, payload.model_dump(exclude_unset=True))
    except UserConflict as exc:
        raise HTTPException(status_code=409, detail="Email already exists") from exc


@business_router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    user_service: UserServiceDep,
    _authorized_user: SelfUser,
) -> None:
    user = user_service.get_active_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user_service.soft_delete(user)


app.include_router(auth_router)
app.include_router(business_router)
