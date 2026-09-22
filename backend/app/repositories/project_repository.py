from typing import Annotated, Protocol

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import DbSession
from app.models import Issue, Project, ProjectMilestone, ProjectObjective, ProjectUpdate, User, WorkflowState


class ProjectRepository(Protocol):
    db: Session
    def projects(self, team_id: str) -> list[Project]: ...
    def project(self, project_id: str, *, for_update: bool = False) -> Project | None: ...
    def objectives(self, project_id: str) -> list[ProjectObjective]: ...
    def objective(self, objective_id: str) -> ProjectObjective | None: ...
    def milestones(self, project_id: str) -> list[ProjectMilestone]: ...
    def milestone(self, milestone_id: str) -> ProjectMilestone | None: ...
    def updates(self, project_id: str) -> list[ProjectUpdate]: ...
    def issues(self, project_id: str) -> list[Issue]: ...
    def state(self, state_id: str) -> WorkflowState | None: ...
    def user(self, user_id: int | None) -> User | None: ...
    def add(self, record: object) -> None: ...
    def commit(self) -> None: ...
    def refresh(self, record: object) -> None: ...


class SqlAlchemyProjectRepository:
    def __init__(self, db: Session):
        self.db = db

    def projects(self, team_id: str) -> list[Project]:
        return list(self.db.scalars(
            select(Project)
            .where(Project.team_id == team_id, Project.archived_at.is_(None))
            .order_by(Project.updated_at.desc(), Project.id)
        ))

    def project(self, project_id: str, *, for_update: bool = False) -> Project | None:
        stmt = select(Project).where(Project.id == project_id, Project.archived_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.scalar(stmt)

    def objectives(self, project_id: str) -> list[ProjectObjective]:
        return list(self.db.scalars(
            select(ProjectObjective)
            .where(ProjectObjective.project_id == project_id)
            .order_by(ProjectObjective.kind, ProjectObjective.position, ProjectObjective.id)
        ))

    def objective(self, objective_id: str) -> ProjectObjective | None:
        return self.db.get(ProjectObjective, objective_id)

    def milestones(self, project_id: str) -> list[ProjectMilestone]:
        return list(self.db.scalars(
            select(ProjectMilestone)
            .where(ProjectMilestone.project_id == project_id)
            .order_by(ProjectMilestone.position, ProjectMilestone.target_date, ProjectMilestone.id)
        ))

    def milestone(self, milestone_id: str) -> ProjectMilestone | None:
        return self.db.get(ProjectMilestone, milestone_id)

    def updates(self, project_id: str) -> list[ProjectUpdate]:
        return list(self.db.scalars(
            select(ProjectUpdate)
            .where(ProjectUpdate.project_id == project_id)
            .order_by(ProjectUpdate.created_at.desc(), ProjectUpdate.id.desc())
        ))

    def issues(self, project_id: str) -> list[Issue]:
        return list(self.db.scalars(
            select(Issue)
            .where(Issue.project_id == project_id, Issue.archived_at.is_(None))
            .order_by(Issue.position, Issue.id)
        ))

    def state(self, state_id: str) -> WorkflowState | None:
        return self.db.get(WorkflowState, state_id)

    def user(self, user_id: int | None) -> User | None:
        return self.db.get(User, user_id) if user_id is not None else None

    def add(self, record: object) -> None:
        self.db.add(record)

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, record: object) -> None:
        self.db.refresh(record)


def get_project_repository(db: DbSession) -> ProjectRepository:
    return SqlAlchemyProjectRepository(db)


ProjectRepositoryDep = Annotated[ProjectRepository, Depends(get_project_repository)]
