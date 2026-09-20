from typing import Annotated, Protocol

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import DbSession
from app.models import Cycle, Issue, IssueLabel, Label, Team, WorkflowState


class IssueRepository(Protocol):
    db: Session
    def team_for_update(self, team_id: str) -> Team | None: ...
    def states(self, team_id: str) -> list[WorkflowState]: ...
    def state(self, state_id: str) -> WorkflowState | None: ...
    def default_state(self, team_id: str) -> WorkflowState | None: ...
    def cycles(self, team_id: str) -> list[Cycle]: ...
    def cycle(self, cycle_id: str) -> Cycle | None: ...
    def labels(self, team_id: str) -> list[Label]: ...
    def label(self, label_id: str) -> Label | None: ...
    def issue(self, workspace_id: str, key: str, *, for_update: bool = False) -> Issue | None: ...
    def issues(self, team_id: str, **filters) -> list[Issue]: ...
    def issue_labels(self, issue_id: str) -> list[Label]: ...
    def replace_labels(self, issue_id: str, label_ids: list[str]) -> None: ...
    def add(self, record: object) -> None: ...
    def commit(self) -> None: ...
    def refresh(self, record: object) -> None: ...


class SqlAlchemyIssueRepository:
    def __init__(self, db: Session):
        self.db = db

    def team_for_update(self, team_id: str) -> Team | None:
        return self.db.scalar(select(Team).where(Team.id == team_id).with_for_update())

    def states(self, team_id: str) -> list[WorkflowState]:
        return list(self.db.scalars(select(WorkflowState).where(WorkflowState.team_id == team_id).order_by(WorkflowState.position)))

    def state(self, state_id: str) -> WorkflowState | None:
        return self.db.get(WorkflowState, state_id)

    def default_state(self, team_id: str) -> WorkflowState | None:
        return self.db.scalar(select(WorkflowState).where(WorkflowState.team_id == team_id, WorkflowState.is_default.is_(True)))

    def cycles(self, team_id: str) -> list[Cycle]:
        return list(self.db.scalars(select(Cycle).where(Cycle.team_id == team_id, Cycle.archived_at.is_(None)).order_by(Cycle.starts_on, Cycle.id)))

    def cycle(self, cycle_id: str) -> Cycle | None:
        return self.db.get(Cycle, cycle_id)

    def labels(self, team_id: str) -> list[Label]:
        return list(self.db.scalars(select(Label).where(Label.team_id == team_id).order_by(Label.name, Label.id)))

    def label(self, label_id: str) -> Label | None:
        return self.db.get(Label, label_id)

    def issue(self, workspace_id: str, key: str, *, for_update: bool = False) -> Issue | None:
        stmt = select(Issue).where(Issue.workspace_id == workspace_id, Issue.key == key)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.scalar(stmt)

    def issues(self, team_id: str, **filters) -> list[Issue]:
        stmt = select(Issue).where(Issue.team_id == team_id, Issue.archived_at.is_(None))
        if filters.get("workflow_state_id"):
            stmt = stmt.where(Issue.workflow_state_id == filters["workflow_state_id"])
        if filters.get("cycle_id"):
            stmt = stmt.where(Issue.cycle_id == filters["cycle_id"])
        if filters.get("priority") is not None:
            stmt = stmt.where(Issue.priority == filters["priority"])
        if filters.get("assignee_user_id"):
            stmt = stmt.where(Issue.assignee_user_id == filters["assignee_user_id"])
        return list(self.db.scalars(stmt.order_by(Issue.position, Issue.created_at, Issue.id)))

    def issue_labels(self, issue_id: str) -> list[Label]:
        stmt = select(Label).join(IssueLabel, IssueLabel.label_id == Label.id).where(IssueLabel.issue_id == issue_id).order_by(Label.name)
        return list(self.db.scalars(stmt))

    def replace_labels(self, issue_id: str, label_ids: list[str]) -> None:
        self.db.query(IssueLabel).filter(IssueLabel.issue_id == issue_id).delete(synchronize_session=False)
        for label_id in label_ids:
            self.db.add(IssueLabel(issue_id=issue_id, label_id=label_id))

    def add(self, record: object) -> None:
        self.db.add(record)

    def commit(self) -> None:
        self.db.commit()

    def refresh(self, record: object) -> None:
        self.db.refresh(record)


def get_issue_repository(db: DbSession) -> IssueRepository:
    return SqlAlchemyIssueRepository(db)


IssueRepositoryDep = Annotated[IssueRepository, Depends(get_issue_repository)]
