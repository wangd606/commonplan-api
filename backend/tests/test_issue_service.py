from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import User
from app.repositories.issue_repository import SqlAlchemyIssueRepository
from app.repositories.workspace_repository import SqlAlchemyWorkspaceRepository
from app.services.issue_service import IssueConflict, IssueService, IssueValidationError
from app.services.workspace_service import WorkspaceService


@pytest.fixture
def context():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        now = datetime.now(timezone.utc)
        owner = User(
            email="owner@example.com",
            name="Owner",
            auth_issuer="issuer",
            auth_subject="owner",
            is_deleted=False,
            created_at=now,
            updated_at=now,
        )
        db.add(owner)
        db.commit()
        db.refresh(owner)

        workspace_service = WorkspaceService(SqlAlchemyWorkspaceRepository(db))
        workspace, _ = workspace_service.create_workspace(
            owner, name="CommonPlan", slug="commonplan", description=None
        )
        team, _ = workspace_service.create_team(
            owner,
            workspace.id,
            name="Core",
            issue_prefix="KEY",
            description=None,
        )
        issue_service = IssueService(SqlAlchemyIssueRepository(db), workspace_service)
        yield issue_service, workspace_service, owner, workspace, team


def test_team_creation_seeds_workflow_and_issue_numbers(context):
    service, _workspaces, owner, workspace, team = context
    states = service.states(owner, workspace.id, team.id)
    assert [state.name for state in states] == ["Backlog", "Todo", "In Progress", "Done"]
    assert next(state for state in states if state.is_default).name == "Todo"

    first, _ = service.create_issue(
        owner, workspace.id, team.id, title="First issue", label_ids=[]
    )
    second, _ = service.create_issue(
        owner, workspace.id, team.id, title="Second issue", label_ids=[]
    )

    assert first.key == "KEY-1"
    assert second.key == "KEY-2"
    assert first.workflow_state_id == next(state.id for state in states if state.is_default)


def test_cycle_label_and_optimistic_issue_update(context):
    service, _workspaces, owner, workspace, team = context
    cycle = service.create_cycle(
        owner,
        workspace.id,
        team.id,
        name="Cycle 1",
        starts_on=date(2026, 9, 21),
        ends_on=date(2026, 10, 5),
    )
    label = service.create_label(
        owner, workspace.id, team.id, name="Bug", color="#EF4444"
    )
    issue, labels = service.create_issue(
        owner,
        workspace.id,
        team.id,
        title="Login fails",
        priority=3,
        cycle_id=cycle.id,
        label_ids=[label.id],
    )
    assert labels[0].name == "Bug"

    updated, _ = service.update_issue(
        owner,
        workspace.id,
        issue.key,
        {"version": 1, "title": "Login fails on callback"},
    )
    assert updated.version == 2

    updated, updated_labels = service.update_issue(
        owner,
        workspace.id,
        issue.key,
        {
            "version": 2,
            "description": "The callback fails after Google redirects.",
            "due_date": date(2026, 10, 1),
            "label_ids": [],
        },
    )
    assert updated.description.startswith("The callback")
    assert updated.due_date == date(2026, 10, 1)
    assert updated_labels == []

    with pytest.raises(IssueConflict):
        service.update_issue(
            owner,
            workspace.id,
            issue.key,
            {"version": 1, "title": "Stale title"},
        )

    with pytest.raises(IssueConflict):
        service.create_cycle(
            owner,
            workspace.id,
            team.id,
            name="Overlapping",
            starts_on=date(2026, 9, 28),
            ends_on=date(2026, 10, 12),
        )


def test_cross_team_references_are_rejected(context):
    service, workspaces, owner, workspace, first_team = context
    second_team, _ = workspaces.create_team(
        owner,
        workspace.id,
        name="Platform",
        issue_prefix="PLAT",
        description=None,
    )
    foreign_state = service.states(owner, workspace.id, second_team.id)[0]

    with pytest.raises(IssueValidationError):
        service.create_issue(
            owner,
            workspace.id,
            first_team.id,
            title="Invalid state",
            workflow_state_id=foreign_state.id,
            label_ids=[],
        )


def test_issue_activity_contains_creation_updates_and_comments(context):
    service, _workspaces, owner, workspace, team = context
    issue, _ = service.create_issue(
        owner, workspace.id, team.id, title="Build issue detail", label_ids=[]
    )
    service.update_issue(
        owner,
        workspace.id,
        issue.key,
        {"version": 1, "priority": 3},
    )
    comment = service.add_comment(
        owner, workspace.id, issue.key, "This should support a complete workflow."
    )

    activity = service.activity(owner, workspace.id, issue.key)
    assert comment["kind"] == "comment"
    assert comment["actor_name"] == "Owner"
    assert {row["event_type"] for row in activity if row["kind"] == "event"} == {
        "issue.created",
        "issue.updated",
        "comment.created",
    }
    assert next(row for row in activity if row["kind"] == "comment")["body"].startswith("This should")
