from fastapi import APIRouter, HTTPException

from app.auth import CurrentUser
from app.issue_routes import _issue_read
from app.repositories.issue_repository import SqlAlchemyIssueRepository
from app.schemas import (
    ProjectCreate, ProjectMilestoneCreate, ProjectMilestonePatch, ProjectMilestoneRead,
    ProjectObjectiveCreate, ProjectObjectivePatch, ProjectObjectiveRead, ProjectPatch,
    ProjectRead, ProjectStatusUpdateCreate, ProjectStatusUpdateRead,
)
from app.services.issue_service import IssueService
from app.services.project_service import (
    ProjectConflict, ProjectForbidden, ProjectNotFound, ProjectServiceDep, ProjectValidationError,
)


router = APIRouter(prefix="/api/v1", tags=["projects"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProjectNotFound):
        return HTTPException(status_code=404, detail="Project resource not found")
    if isinstance(exc, ProjectForbidden):
        return HTTPException(status_code=403, detail="Insufficient team access")
    if isinstance(exc, ProjectConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ProjectValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


def _milestone_read(snapshot) -> ProjectMilestoneRead:
    row = snapshot["milestone"]
    return ProjectMilestoneRead(
        id=row.id, project_id=row.project_id, name=row.name, description=row.description,
        status=row.status, target_date=row.target_date, position=row.position,
        issue_count=snapshot["issue_count"], completed_issue_count=snapshot["completed_issue_count"],
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _update_read(snapshot) -> ProjectStatusUpdateRead:
    row = snapshot["update"]
    return ProjectStatusUpdateRead(
        id=row.id, project_id=row.project_id, author_user_id=row.author_user_id,
        author_name=snapshot["author_name"], body=row.body, health=row.health,
        created_at=row.created_at, edited_at=row.edited_at,
    )


def _project_read(service, snapshot) -> ProjectRead:
    row = snapshot["project"]
    issue_service = IssueService(SqlAlchemyIssueRepository(service.repository.db), service.workspaces)
    return ProjectRead(
        id=row.id, team_id=row.team_id, name=row.name, slug=row.slug,
        summary=row.summary, description=row.description, status=row.status,
        lead_user_id=row.lead_user_id, target_date=row.target_date,
        issue_count=snapshot["issue_count"], completed_issue_count=snapshot["completed_issue_count"],
        progress_percent=snapshot["progress_percent"],
        objectives=[ProjectObjectiveRead.model_validate(item) for item in snapshot["objectives"]],
        milestones=[_milestone_read(item) for item in snapshot["milestones"]],
        updates=[_update_read(item) for item in snapshot["updates"]],
        linked_issues=[
            _issue_read(issue_service, issue, issue_service.repository.issue_labels(issue.id))
            for issue in snapshot["issues"]
        ],
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.get("/workspaces/{workspace_id}/teams/{team_id}/projects", response_model=list[ProjectRead])
def list_projects(workspace_id: str, team_id: str, current_user: CurrentUser, service: ProjectServiceDep):
    try:
        return [_project_read(service, row) for row in service.list_projects(current_user, workspace_id, team_id)]
    except (ProjectNotFound, ProjectForbidden) as exc:
        raise _error(exc) from exc


@router.post("/workspaces/{workspace_id}/teams/{team_id}/projects", response_model=ProjectRead, status_code=201)
def create_project(workspace_id: str, team_id: str, payload: ProjectCreate, current_user: CurrentUser, service: ProjectServiceDep):
    try:
        return _project_read(service, service.create_project(current_user, workspace_id, team_id, **payload.model_dump()))
    except (ProjectNotFound, ProjectForbidden, ProjectConflict, ProjectValidationError) as exc:
        raise _error(exc) from exc


@router.get("/workspaces/{workspace_id}/teams/{team_id}/projects/{project_id}", response_model=ProjectRead)
def get_project(workspace_id: str, team_id: str, project_id: str, current_user: CurrentUser, service: ProjectServiceDep):
    try:
        return _project_read(service, service.get_project(current_user, workspace_id, team_id, project_id))
    except (ProjectNotFound, ProjectForbidden) as exc:
        raise _error(exc) from exc


@router.patch("/workspaces/{workspace_id}/teams/{team_id}/projects/{project_id}", response_model=ProjectRead)
def update_project(workspace_id: str, team_id: str, project_id: str, payload: ProjectPatch, current_user: CurrentUser, service: ProjectServiceDep):
    try:
        return _project_read(service, service.update_project(current_user, workspace_id, team_id, project_id, payload.model_dump(exclude_unset=True)))
    except (ProjectNotFound, ProjectForbidden, ProjectConflict, ProjectValidationError) as exc:
        raise _error(exc) from exc


@router.post("/workspaces/{workspace_id}/teams/{team_id}/projects/{project_id}/objectives", response_model=ProjectObjectiveRead, status_code=201)
def add_objective(workspace_id: str, team_id: str, project_id: str, payload: ProjectObjectiveCreate, current_user: CurrentUser, service: ProjectServiceDep):
    try:
        return service.add_objective(current_user, workspace_id, team_id, project_id, **payload.model_dump())
    except (ProjectNotFound, ProjectForbidden, ProjectValidationError) as exc:
        raise _error(exc) from exc


@router.patch("/workspaces/{workspace_id}/teams/{team_id}/projects/{project_id}/objectives/{objective_id}", response_model=ProjectObjectiveRead)
def update_objective(workspace_id: str, team_id: str, project_id: str, objective_id: str, payload: ProjectObjectivePatch, current_user: CurrentUser, service: ProjectServiceDep):
    try:
        return service.update_objective(current_user, workspace_id, team_id, project_id, objective_id, payload.model_dump(exclude_unset=True))
    except (ProjectNotFound, ProjectForbidden, ProjectValidationError) as exc:
        raise _error(exc) from exc


@router.post("/workspaces/{workspace_id}/teams/{team_id}/projects/{project_id}/milestones", response_model=ProjectMilestoneRead, status_code=201)
def add_milestone(workspace_id: str, team_id: str, project_id: str, payload: ProjectMilestoneCreate, current_user: CurrentUser, service: ProjectServiceDep):
    try:
        return _milestone_read(service.add_milestone(current_user, workspace_id, team_id, project_id, **payload.model_dump()))
    except (ProjectNotFound, ProjectForbidden, ProjectValidationError) as exc:
        raise _error(exc) from exc


@router.patch("/workspaces/{workspace_id}/teams/{team_id}/projects/{project_id}/milestones/{milestone_id}", response_model=ProjectMilestoneRead)
def update_milestone(workspace_id: str, team_id: str, project_id: str, milestone_id: str, payload: ProjectMilestonePatch, current_user: CurrentUser, service: ProjectServiceDep):
    try:
        return _milestone_read(service.update_milestone(current_user, workspace_id, team_id, project_id, milestone_id, payload.model_dump(exclude_unset=True)))
    except (ProjectNotFound, ProjectForbidden, ProjectValidationError) as exc:
        raise _error(exc) from exc


@router.post("/workspaces/{workspace_id}/teams/{team_id}/projects/{project_id}/updates", response_model=ProjectStatusUpdateRead, status_code=201)
def add_update(workspace_id: str, team_id: str, project_id: str, payload: ProjectStatusUpdateCreate, current_user: CurrentUser, service: ProjectServiceDep):
    try:
        return _update_read(service.add_update(current_user, workspace_id, team_id, project_id, **payload.model_dump()))
    except (ProjectNotFound, ProjectForbidden, ProjectValidationError) as exc:
        raise _error(exc) from exc
