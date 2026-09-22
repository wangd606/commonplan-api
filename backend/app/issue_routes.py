from fastapi import APIRouter, HTTPException, Query

from app.auth import CurrentUser
from app.schemas import (
    CycleCreate, CycleRead, IssueActivityRead, IssueCommentCreate, IssueCreate, IssueRead, IssueUpdate,
    LabelCreate, LabelRead, WorkflowStateRead,
)
from app.services.issue_service import (
    IssueConflict, IssueForbidden, IssueNotFound, IssueServiceDep, IssueValidationError,
)


router = APIRouter(prefix="/api/v1", tags=["issue planning"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, IssueNotFound):
        return HTTPException(status_code=404, detail="Issue planning resource not found")
    if isinstance(exc, IssueForbidden):
        return HTTPException(status_code=403, detail="Insufficient team access")
    if isinstance(exc, IssueConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, IssueValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


def _issue_read(service, issue, labels) -> IssueRead:
    state = service.repository.state(issue.workflow_state_id)
    return IssueRead(
        id=issue.id, workspace_id=issue.workspace_id, team_id=issue.team_id,
        number=issue.number, key=issue.key, title=issue.title, description=issue.description,
        workflow_state_id=issue.workflow_state_id, workflow_state_name=state.name,
        workflow_category=state.category, priority=issue.priority,
        creator_user_id=issue.creator_user_id, assignee_user_id=issue.assignee_user_id,
        cycle_id=issue.cycle_id, due_date=issue.due_date, version=issue.version,
        project_id=issue.project_id, milestone_id=issue.milestone_id,
        labels=[LabelRead.model_validate(label) for label in labels],
        created_at=issue.created_at, updated_at=issue.updated_at,
    )


@router.get("/workspaces/{workspace_id}/teams/{team_id}/workflow-states", response_model=list[WorkflowStateRead])
def workflow_states(workspace_id: str, team_id: str, current_user: CurrentUser, service: IssueServiceDep):
    try:
        return service.states(current_user, workspace_id, team_id)
    except (IssueNotFound, IssueForbidden) as exc:
        raise _error(exc) from exc


@router.get("/workspaces/{workspace_id}/teams/{team_id}/cycles", response_model=list[CycleRead])
def list_cycles(workspace_id: str, team_id: str, current_user: CurrentUser, service: IssueServiceDep):
    try:
        return service.cycles(current_user, workspace_id, team_id)
    except (IssueNotFound, IssueForbidden) as exc:
        raise _error(exc) from exc


@router.post("/workspaces/{workspace_id}/teams/{team_id}/cycles", response_model=CycleRead, status_code=201)
def create_cycle(workspace_id: str, team_id: str, payload: CycleCreate, current_user: CurrentUser, service: IssueServiceDep):
    try:
        return service.create_cycle(current_user, workspace_id, team_id, **payload.model_dump())
    except (IssueNotFound, IssueForbidden, IssueConflict, IssueValidationError) as exc:
        raise _error(exc) from exc


@router.get("/workspaces/{workspace_id}/teams/{team_id}/labels", response_model=list[LabelRead])
def list_labels(workspace_id: str, team_id: str, current_user: CurrentUser, service: IssueServiceDep):
    try:
        return service.labels(current_user, workspace_id, team_id)
    except (IssueNotFound, IssueForbidden) as exc:
        raise _error(exc) from exc


@router.post("/workspaces/{workspace_id}/teams/{team_id}/labels", response_model=LabelRead, status_code=201)
def create_label(workspace_id: str, team_id: str, payload: LabelCreate, current_user: CurrentUser, service: IssueServiceDep):
    try:
        return service.create_label(current_user, workspace_id, team_id, **payload.model_dump())
    except (IssueNotFound, IssueForbidden, IssueConflict, IssueValidationError) as exc:
        raise _error(exc) from exc


@router.get("/workspaces/{workspace_id}/teams/{team_id}/issues", response_model=list[IssueRead])
def list_issues(
    workspace_id: str, team_id: str, current_user: CurrentUser, service: IssueServiceDep,
    workflow_state_id: str | None = None, cycle_id: str | None = None, project_id: str | None = None,
    priority: int | None = Query(default=None, ge=0, le=4), assignee_user_id: int | None = None,
):
    try:
        rows = service.list_issues(
            current_user, workspace_id, team_id, workflow_state_id=workflow_state_id,
            cycle_id=cycle_id, project_id=project_id, priority=priority, assignee_user_id=assignee_user_id,
        )
        return [_issue_read(service, issue, labels) for issue, labels in rows]
    except (IssueNotFound, IssueForbidden) as exc:
        raise _error(exc) from exc


@router.post("/workspaces/{workspace_id}/teams/{team_id}/issues", response_model=IssueRead, status_code=201)
def create_issue(workspace_id: str, team_id: str, payload: IssueCreate, current_user: CurrentUser, service: IssueServiceDep):
    try:
        return _issue_read(service, *service.create_issue(current_user, workspace_id, team_id, **payload.model_dump()))
    except (IssueNotFound, IssueForbidden, IssueConflict, IssueValidationError) as exc:
        raise _error(exc) from exc


@router.get("/workspaces/{workspace_id}/issues/{key}", response_model=IssueRead)
def get_issue(workspace_id: str, key: str, current_user: CurrentUser, service: IssueServiceDep):
    try:
        return _issue_read(service, *service.get_issue(current_user, workspace_id, key.upper()))
    except (IssueNotFound, IssueForbidden) as exc:
        raise _error(exc) from exc


@router.patch("/workspaces/{workspace_id}/issues/{key}", response_model=IssueRead)
def update_issue(workspace_id: str, key: str, payload: IssueUpdate, current_user: CurrentUser, service: IssueServiceDep):
    try:
        return _issue_read(
            service,
            *service.update_issue(current_user, workspace_id, key.upper(), payload.model_dump(exclude_unset=True)),
        )
    except (IssueNotFound, IssueForbidden, IssueConflict, IssueValidationError) as exc:
        raise _error(exc) from exc


@router.get("/workspaces/{workspace_id}/issues/{key}/activity", response_model=list[IssueActivityRead])
def issue_activity(workspace_id: str, key: str, current_user: CurrentUser, service: IssueServiceDep):
    try:
        return service.activity(current_user, workspace_id, key.upper())
    except (IssueNotFound, IssueForbidden) as exc:
        raise _error(exc) from exc


@router.post(
    "/workspaces/{workspace_id}/issues/{key}/comments",
    response_model=IssueActivityRead,
    status_code=201,
)
def create_issue_comment(
    workspace_id: str,
    key: str,
    payload: IssueCommentCreate,
    current_user: CurrentUser,
    service: IssueServiceDep,
):
    try:
        return service.add_comment(current_user, workspace_id, key.upper(), payload.body)
    except (IssueNotFound, IssueForbidden, IssueValidationError) as exc:
        raise _error(exc) from exc


@router.get("/me/issues", response_model=list[IssueRead])
def my_issues(current_user: CurrentUser, service: IssueServiceDep, workspace_id: str | None = None):
    try:
        return [_issue_read(service, issue, labels) for issue, labels in service.my_issues(current_user, workspace_id)]
    except (IssueNotFound, IssueForbidden) as exc:
        raise _error(exc) from exc
