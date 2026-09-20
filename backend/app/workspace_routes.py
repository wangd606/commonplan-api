from fastapi import APIRouter, HTTPException, Response, status

from app.auth import CurrentUser
from app.schemas import (
    InvitationAccept,
    InvitationCreate,
    InvitationRead,
    MemberRead,
    MembershipWrite,
    TeamCreate,
    TeamOverviewRead,
    TeamRead,
    TeamUpdate,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceUpdate,
)
from app.services.workspace_service import (
    WorkspaceConflict,
    WorkspaceForbidden,
    WorkspaceNotFound,
    WorkspaceServiceDep,
    WorkspaceValidationError,
)


router = APIRouter(prefix="/api/v1", tags=["workspaces"])


def _workspace_read(workspace, membership) -> WorkspaceRead:
    return WorkspaceRead(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        description=workspace.description,
        my_role=membership.role,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _team_read(team, team_membership, workspace_membership=None) -> TeamRead:
    role = team_membership.role if team_membership is not None else workspace_membership.role
    return TeamRead(
        id=team.id,
        workspace_id=team.workspace_id,
        name=team.name,
        description=team.description,
        issue_prefix=team.issue_prefix,
        my_role=role,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


def _member_read(user, membership, *, workspace: bool) -> MemberRead:
    return MemberRead(
        user_id=user.id,
        name=user.name,
        email=user.email,
        role=membership.role,
        status=membership.status if workspace else None,
    )


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkspaceNotFound):
        return HTTPException(status_code=404, detail="Resource not found")
    if isinstance(exc, WorkspaceForbidden):
        return HTTPException(status_code=403, detail="Insufficient workspace or team access")
    if isinstance(exc, WorkspaceConflict):
        return HTTPException(status_code=409, detail="Workspace or team value already exists")
    if isinstance(exc, WorkspaceValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


@router.get("/workspaces", response_model=list[WorkspaceRead])
def list_workspaces(current_user: CurrentUser, service: WorkspaceServiceDep):
    return [_workspace_read(workspace, membership) for workspace, membership in service.list_workspaces(current_user)]


@router.post("/workspaces", response_model=WorkspaceRead, status_code=201)
def create_workspace(payload: WorkspaceCreate, current_user: CurrentUser, service: WorkspaceServiceDep):
    try:
        return _workspace_read(
            *service.create_workspace(current_user, **payload.model_dump())
        )
    except (WorkspaceConflict, WorkspaceValidationError) as exc:
        raise _translate(exc) from exc


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceRead)
def get_workspace(workspace_id: str, current_user: CurrentUser, service: WorkspaceServiceDep):
    try:
        return _workspace_read(*service.get_workspace(current_user, workspace_id))
    except (WorkspaceNotFound, WorkspaceForbidden) as exc:
        raise _translate(exc) from exc


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceRead)
def update_workspace(
    workspace_id: str, payload: WorkspaceUpdate, current_user: CurrentUser, service: WorkspaceServiceDep
):
    try:
        return _workspace_read(
            *service.update_workspace(current_user, workspace_id, payload.model_dump(exclude_unset=True))
        )
    except (WorkspaceNotFound, WorkspaceForbidden, WorkspaceConflict) as exc:
        raise _translate(exc) from exc


@router.get("/workspaces/{workspace_id}/teams", response_model=list[TeamRead])
def list_teams(workspace_id: str, current_user: CurrentUser, service: WorkspaceServiceDep):
    try:
        rows = service.list_teams(current_user, workspace_id)
        workspace_membership = service.get_workspace(current_user, workspace_id)[1]
        return [_team_read(team, membership, workspace_membership) for team, membership in rows]
    except (WorkspaceNotFound, WorkspaceForbidden) as exc:
        raise _translate(exc) from exc


@router.post("/workspaces/{workspace_id}/teams", response_model=TeamRead, status_code=201)
def create_team(
    workspace_id: str, payload: TeamCreate, current_user: CurrentUser, service: WorkspaceServiceDep
):
    try:
        team, membership = service.create_team(current_user, workspace_id, **payload.model_dump())
        return _team_read(team, membership)
    except (WorkspaceNotFound, WorkspaceForbidden, WorkspaceConflict) as exc:
        raise _translate(exc) from exc


@router.get("/workspaces/{workspace_id}/teams/{team_id}", response_model=TeamRead)
def get_team(workspace_id: str, team_id: str, current_user: CurrentUser, service: WorkspaceServiceDep):
    try:
        return _team_read(*service.get_team(current_user, workspace_id, team_id))
    except (WorkspaceNotFound, WorkspaceForbidden) as exc:
        raise _translate(exc) from exc


@router.patch("/workspaces/{workspace_id}/teams/{team_id}", response_model=TeamRead)
def update_team(
    workspace_id: str,
    team_id: str,
    payload: TeamUpdate,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
):
    try:
        return _team_read(
            *service.update_team(current_user, workspace_id, team_id, payload.model_dump(exclude_unset=True))
        )
    except (WorkspaceNotFound, WorkspaceForbidden, WorkspaceConflict) as exc:
        raise _translate(exc) from exc


@router.get("/workspaces/{workspace_id}/teams/{team_id}/overview", response_model=TeamOverviewRead)
def team_overview(
    workspace_id: str, team_id: str, current_user: CurrentUser, service: WorkspaceServiceDep
):
    try:
        service.get_team(current_user, workspace_id, team_id)
        return TeamOverviewRead(team_id=team_id)
    except (WorkspaceNotFound, WorkspaceForbidden) as exc:
        raise _translate(exc) from exc


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberRead])
def workspace_members(workspace_id: str, current_user: CurrentUser, service: WorkspaceServiceDep):
    try:
        return [_member_read(user, membership, workspace=True) for user, membership in service.workspace_members(current_user, workspace_id)]
    except (WorkspaceNotFound, WorkspaceForbidden) as exc:
        raise _translate(exc) from exc


@router.put("/workspaces/{workspace_id}/members/{user_id}", response_model=MemberRead)
def put_workspace_member(
    workspace_id: str,
    user_id: int,
    payload: MembershipWrite,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
):
    try:
        return _member_read(*service.put_workspace_member(current_user, workspace_id, user_id, payload.role), workspace=True)
    except (WorkspaceNotFound, WorkspaceForbidden, WorkspaceValidationError) as exc:
        raise _translate(exc) from exc


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
def delete_workspace_member(
    workspace_id: str, user_id: int, current_user: CurrentUser, service: WorkspaceServiceDep
):
    try:
        service.delete_workspace_member(current_user, workspace_id, user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except (WorkspaceNotFound, WorkspaceForbidden, WorkspaceValidationError) as exc:
        raise _translate(exc) from exc


@router.get("/workspaces/{workspace_id}/teams/{team_id}/members", response_model=list[MemberRead])
def team_members(
    workspace_id: str, team_id: str, current_user: CurrentUser, service: WorkspaceServiceDep
):
    try:
        return [_member_read(user, membership, workspace=False) for user, membership in service.team_members(current_user, workspace_id, team_id)]
    except (WorkspaceNotFound, WorkspaceForbidden) as exc:
        raise _translate(exc) from exc


@router.put("/workspaces/{workspace_id}/teams/{team_id}/members/{user_id}", response_model=MemberRead)
def put_team_member(
    workspace_id: str,
    team_id: str,
    user_id: int,
    payload: MembershipWrite,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
):
    try:
        return _member_read(
            *service.put_team_member(current_user, workspace_id, team_id, user_id, payload.role),
            workspace=False,
        )
    except (WorkspaceNotFound, WorkspaceForbidden, WorkspaceValidationError) as exc:
        raise _translate(exc) from exc


@router.delete("/workspaces/{workspace_id}/teams/{team_id}/members/{user_id}", status_code=204)
def delete_team_member(
    workspace_id: str,
    team_id: str,
    user_id: int,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
):
    try:
        service.delete_team_member(current_user, workspace_id, team_id, user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except (WorkspaceNotFound, WorkspaceForbidden) as exc:
        raise _translate(exc) from exc


@router.post("/workspaces/{workspace_id}/invitations", response_model=InvitationRead, status_code=201)
def create_invitation(
    workspace_id: str,
    payload: InvitationCreate,
    current_user: CurrentUser,
    service: WorkspaceServiceDep,
):
    try:
        invitation, token = service.create_invitation(current_user, workspace_id, **payload.model_dump())
        return InvitationRead(
            id=invitation.id,
            workspace_id=invitation.workspace_id,
            email=invitation.email,
            role=invitation.role,
            team_id=invitation.team_id,
            expires_at=invitation.expires_at,
            invite_token=token,
        )
    except (WorkspaceNotFound, WorkspaceForbidden, WorkspaceConflict, WorkspaceValidationError) as exc:
        raise _translate(exc) from exc


@router.post("/invitations/accept", response_model=WorkspaceRead)
def accept_invitation(payload: InvitationAccept, current_user: CurrentUser, service: WorkspaceServiceDep):
    try:
        return _workspace_read(*service.accept_invitation(current_user, payload.token))
    except (WorkspaceNotFound, WorkspaceForbidden, WorkspaceConflict) as exc:
        raise _translate(exc) from exc
