from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)


class UserRead(BaseModel):
    id: int
    email: EmailStr
    name: str
    auth_issuer: str | None
    auth_subject: str | None
    avatar_url: str | None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IdentityUserRead(BaseModel):
    id: str
    email: EmailStr
    name: str
    avatar_url: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: IdentityUserRead


class InternalAuthTokenResponse(AuthTokenResponse):
    refresh_token: str


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name cannot be empty")
        return normalized


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=4000)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)


class WorkspaceRead(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None
    my_role: str
    created_at: datetime
    updated_at: datetime


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    issue_prefix: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{1,11}$")
    description: str | None = Field(default=None, max_length=4000)


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)


class TeamRead(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str | None
    issue_prefix: str
    my_role: str
    created_at: datetime
    updated_at: datetime


class MembershipWrite(BaseModel):
    role: str


class MemberRead(BaseModel):
    user_id: int
    name: str
    email: EmailStr
    role: str
    status: str | None = None


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = "member"
    team_id: str | None = None


class InvitationRead(BaseModel):
    id: str
    workspace_id: str
    email: EmailStr
    role: str
    team_id: str | None
    expires_at: datetime
    invite_token: str


class InvitationAccept(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class TeamOverviewRead(BaseModel):
    team_id: str
    project_count: int = 0
    open_issue_count: int = 0
    current_cycle: dict | None = None
    recent_issues: list[dict] = Field(default_factory=list)
