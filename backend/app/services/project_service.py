import re
import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import IntegrityError

from app.models import Project, ProjectMilestone, ProjectObjective, ProjectUpdate, User
from app.repositories.project_repository import ProjectRepository, ProjectRepositoryDep
from app.services.workspace_service import WorkspaceService, WorkspaceServiceDep


class ProjectNotFound(Exception): pass
class ProjectForbidden(Exception): pass
class ProjectConflict(Exception): pass
class ProjectValidationError(Exception): pass


PROJECT_STATUSES = {"planned", "in_progress", "paused", "completed", "canceled"}
MILESTONE_STATUSES = {"planned", "in_progress", "completed", "canceled"}
OBJECTIVE_KINDS = {"objective", "success_criterion"}
HEALTH_VALUES = {"on_track", "at_risk", "off_track"}


class ProjectService:
    def __init__(self, repository: ProjectRepository, workspaces: WorkspaceService):
        self.repository = repository
        self.workspaces = workspaces

    def list_projects(self, user: User, workspace_id: str, team_id: str):
        self._access(user, workspace_id, team_id)
        return [self.snapshot(project) for project in self.repository.projects(team_id)]

    def create_project(self, user: User, workspace_id: str, team_id: str, **values):
        self._access(user, workspace_id, team_id)
        self._validate_project(team_id, values)
        name = values["name"].strip()
        slug = values.get("slug") or self._slug(name)
        project = Project(
            id=str(uuid.uuid4()), team_id=team_id, name=name, slug=slug,
            summary=values.get("summary"), description=values.get("description"),
            status=values.get("status", "planned"), lead_user_id=values.get("lead_user_id"),
            target_date=values.get("target_date"),
        )
        self.repository.add(project)
        self._commit()
        self.repository.refresh(project)
        return self.snapshot(project)

    def get_project(self, user: User, workspace_id: str, team_id: str, project_id: str):
        self._access(user, workspace_id, team_id)
        return self.snapshot(self._project(project_id, team_id))

    def update_project(self, user: User, workspace_id: str, team_id: str, project_id: str, changes: dict):
        self._access(user, workspace_id, team_id)
        project = self._project(project_id, team_id, for_update=True)
        self._validate_project(team_id, changes)
        for field, value in changes.items():
            setattr(project, field, value.strip() if field == "name" and isinstance(value, str) else value)
        self._commit()
        self.repository.refresh(project)
        return self.snapshot(project)

    def add_objective(self, user, workspace_id, team_id, project_id, **values):
        self._access(user, workspace_id, team_id)
        project = self._project(project_id, team_id)
        if values["kind"] not in OBJECTIVE_KINDS:
            raise ProjectValidationError("Objective kind is invalid")
        objective = ProjectObjective(
            id=str(uuid.uuid4()), project_id=project.id, kind=values["kind"],
            body=values["body"].strip(), position=values.get("position", 0), is_met=False,
        )
        self.repository.add(objective)
        self._commit()
        self.repository.refresh(objective)
        return objective

    def update_objective(self, user, workspace_id, team_id, project_id, objective_id, changes):
        self._access(user, workspace_id, team_id)
        self._project(project_id, team_id)
        objective = self.repository.objective(objective_id)
        if objective is None or objective.project_id != project_id:
            raise ProjectNotFound
        for field, value in changes.items():
            setattr(objective, field, value.strip() if field == "body" and isinstance(value, str) else value)
        self._commit()
        self.repository.refresh(objective)
        return objective

    def add_milestone(self, user, workspace_id, team_id, project_id, **values):
        self._access(user, workspace_id, team_id)
        project = self._project(project_id, team_id)
        self._validate_milestone(values)
        milestone = ProjectMilestone(
            id=str(uuid.uuid4()), project_id=project.id, name=values["name"].strip(),
            description=values.get("description"), status=values.get("status", "planned"),
            target_date=values.get("target_date"), position=values.get("position", 0),
        )
        self.repository.add(milestone)
        self._commit()
        self.repository.refresh(milestone)
        return self.milestone_snapshot(milestone)

    def update_milestone(self, user, workspace_id, team_id, project_id, milestone_id, changes):
        self._access(user, workspace_id, team_id)
        self._project(project_id, team_id)
        milestone = self.repository.milestone(milestone_id)
        if milestone is None or milestone.project_id != project_id:
            raise ProjectNotFound
        self._validate_milestone(changes)
        for field, value in changes.items():
            setattr(milestone, field, value.strip() if field == "name" and isinstance(value, str) else value)
        self._commit()
        self.repository.refresh(milestone)
        return self.milestone_snapshot(milestone)

    def add_update(self, user, workspace_id, team_id, project_id, *, body, health):
        self._access(user, workspace_id, team_id)
        project = self._project(project_id, team_id)
        if health is not None and health not in HEALTH_VALUES:
            raise ProjectValidationError("Project health is invalid")
        update = ProjectUpdate(
            id=str(uuid.uuid4()), project_id=project.id, author_user_id=user.id,
            body=body.strip(), health=health,
        )
        self.repository.add(update)
        self._commit()
        self.repository.refresh(update)
        return self.update_snapshot(update)

    def snapshot(self, project: Project):
        issues = self.repository.issues(project.id)
        issue_states = [(issue, self.repository.state(issue.workflow_state_id)) for issue in issues]
        relevant = [(issue, state) for issue, state in issue_states if state is None or state.category != "canceled"]
        completed = sum(state is not None and state.category == "done" for _issue, state in relevant)
        total = len(relevant)
        return {
            "project": project,
            "issue_count": total,
            "completed_issue_count": completed,
            "progress_percent": round(completed * 100 / total) if total else 0,
            "objectives": self.repository.objectives(project.id),
            "milestones": [self.milestone_snapshot(row, issues) for row in self.repository.milestones(project.id)],
            "updates": [self.update_snapshot(row) for row in self.repository.updates(project.id)],
            "issues": issues,
        }

    def milestone_snapshot(self, milestone: ProjectMilestone, issues=None):
        project_issues = issues if issues is not None else self.repository.issues(milestone.project_id)
        linked = [issue for issue in project_issues if issue.milestone_id == milestone.id]
        linked_states = [(issue, self.repository.state(issue.workflow_state_id)) for issue in linked]
        relevant = [(issue, state) for issue, state in linked_states if state is None or state.category != "canceled"]
        completed = sum(state is not None and state.category == "done" for _issue, state in relevant)
        return {"milestone": milestone, "issue_count": len(relevant), "completed_issue_count": completed}

    def update_snapshot(self, update: ProjectUpdate):
        author = self.repository.user(update.author_user_id)
        return {"update": update, "author_name": author.name if author else None}

    def _project(self, project_id, team_id, *, for_update=False):
        project = self.repository.project(project_id, for_update=for_update)
        if project is None or project.team_id != team_id:
            raise ProjectNotFound
        return project

    def _validate_project(self, team_id, values):
        if values.get("status") is not None and values["status"] not in PROJECT_STATUSES:
            raise ProjectValidationError("Project status is invalid")
        lead = values.get("lead_user_id")
        if lead is not None and self.workspaces.repository.team_membership(team_id, lead) is None:
            raise ProjectValidationError("Project lead must be an active team member")

    @staticmethod
    def _validate_milestone(values):
        if values.get("status") is not None and values["status"] not in MILESTONE_STATUSES:
            raise ProjectValidationError("Milestone status is invalid")

    def _access(self, user, workspace_id, team_id):
        try:
            return self.workspaces.get_team(user, workspace_id, team_id)
        except Exception as exc:
            if exc.__class__.__name__.endswith("NotFound"):
                raise ProjectNotFound from exc
            raise ProjectForbidden from exc

    @staticmethod
    def _slug(value):
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]
        return slug or "project"

    def _commit(self):
        try:
            self.repository.commit()
        except IntegrityError as exc:
            self.repository.db.rollback()
            raise ProjectConflict("Project value already exists") from exc


def get_project_service(repository: ProjectRepositoryDep, workspaces: WorkspaceServiceDep) -> ProjectService:
    return ProjectService(repository, workspaces)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
