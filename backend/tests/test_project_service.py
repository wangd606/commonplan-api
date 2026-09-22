from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import User
from app.repositories.issue_repository import SqlAlchemyIssueRepository
from app.repositories.project_repository import SqlAlchemyProjectRepository
from app.repositories.workspace_repository import SqlAlchemyWorkspaceRepository
from app.services.issue_service import IssueService, IssueValidationError
from app.services.project_service import ProjectService, ProjectValidationError
from app.services.workspace_service import WorkspaceService


@pytest.fixture
def context():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        now = datetime.now(timezone.utc)
        owner = User(
            email="owner@example.com", name="Owner", auth_issuer="issuer",
            auth_subject="owner", is_deleted=False, created_at=now, updated_at=now,
        )
        db.add(owner)
        db.commit()
        db.refresh(owner)
        workspaces = WorkspaceService(SqlAlchemyWorkspaceRepository(db))
        workspace, _ = workspaces.create_workspace(
            owner, name="CommonPlan", slug="commonplan", description=None
        )
        team, _ = workspaces.create_team(
            owner, workspace.id, name="Core", issue_prefix="KEY", description=None
        )
        projects = ProjectService(SqlAlchemyProjectRepository(db), workspaces)
        issues = IssueService(SqlAlchemyIssueRepository(db), workspaces)
        yield projects, issues, workspaces, owner, workspace, team


def test_project_brief_objectives_milestones_updates_and_derived_progress(context):
    projects, issues, _workspaces, owner, workspace, team = context
    snapshot = projects.create_project(
        owner, workspace.id, team.id, name="Launch readiness",
        summary="Prepare the product launch", description="A complete project brief.",
        status="in_progress", lead_user_id=owner.id, target_date=date(2026, 11, 1),
    )
    project = snapshot["project"]
    assert project.slug == "launch-readiness"

    projects.add_objective(
        owner, workspace.id, team.id, project.id,
        kind="objective", body="Ship a dependable first release", position=0,
    )
    projects.add_objective(
        owner, workspace.id, team.id, project.id,
        kind="success_criterion", body="All launch blockers are complete", position=1,
    )
    milestone = projects.add_milestone(
        owner, workspace.id, team.id, project.id, name="Release candidate",
        description="Candidate build", status="in_progress", target_date=date(2026, 10, 20), position=0,
    )["milestone"]
    projects.add_update(
        owner, workspace.id, team.id, project.id,
        body="The release candidate is on schedule.", health="on_track",
    )
    issue, _ = issues.create_issue(
        owner, workspace.id, team.id, title="Resolve final blocker", label_ids=[],
        project_id=project.id, milestone_id=milestone.id,
    )
    done = next(state for state in issues.states(owner, workspace.id, team.id) if state.category == "done")
    issues.update_issue(
        owner, workspace.id, issue.key, {"version": 1, "workflow_state_id": done.id}
    )

    detail = projects.get_project(owner, workspace.id, team.id, project.id)
    assert detail["issue_count"] == 1
    assert detail["completed_issue_count"] == 1
    assert detail["progress_percent"] == 100
    assert len(detail["objectives"]) == 2
    assert detail["milestones"][0]["completed_issue_count"] == 1
    assert detail["updates"][0]["author_name"] == "Owner"


def test_project_and_milestone_references_cannot_cross_teams(context):
    projects, issues, workspaces, owner, workspace, first_team = context
    second_team, _ = workspaces.create_team(
        owner, workspace.id, name="Platform", issue_prefix="PLAT", description=None
    )
    foreign = projects.create_project(
        owner, workspace.id, second_team.id, name="Platform work", status="planned"
    )["project"]

    with pytest.raises(IssueValidationError):
        issues.create_issue(
            owner, workspace.id, first_team.id, title="Invalid project", label_ids=[],
            project_id=foreign.id,
        )

    with pytest.raises(ProjectValidationError):
        projects.create_project(
            owner, workspace.id, first_team.id, name="Invalid lead",
            status="planned", lead_user_id=999,
        )
