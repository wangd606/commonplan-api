from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import User
from app.repositories.workspace_repository import SqlAlchemyWorkspaceRepository
from app.services.workspace_service import WorkspaceForbidden, WorkspaceService


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
        outsider = User(
            email="outsider@example.com",
            name="Outsider",
            auth_issuer="issuer",
            auth_subject="outsider",
            is_deleted=False,
            created_at=now,
            updated_at=now,
        )
        db.add_all([owner, outsider])
        db.commit()
        db.refresh(owner)
        db.refresh(outsider)
        service = WorkspaceService(SqlAlchemyWorkspaceRepository(db))
        yield service, owner, outsider


def test_workspace_creation_adds_owner_and_team_lead(context):
    service, owner, _outsider = context
    workspace, membership = service.create_workspace(
        owner, name="CommonPlan", slug="commonplan", description="Product work"
    )
    team, team_membership = service.create_team(
        owner,
        workspace.id,
        name="Core",
        issue_prefix="key",
        description="Core team",
    )

    assert membership.role == "owner"
    assert team.issue_prefix == "KEY"
    assert team_membership.role == "lead"
    assert service.list_workspaces(owner)[0][0].id == workspace.id
    assert service.list_teams(owner, workspace.id)[0][0].id == team.id


def test_workspace_and_team_access_is_membership_scoped(context):
    service, owner, outsider = context
    workspace, _membership = service.create_workspace(
        owner, name="CommonPlan", slug="commonplan", description=None
    )
    team, _team_membership = service.create_team(
        owner, workspace.id, name="Core", issue_prefix="KEY", description=None
    )

    with pytest.raises(WorkspaceForbidden):
        service.get_workspace(outsider, workspace.id)

    service.put_workspace_member(owner, workspace.id, outsider.id, "member")
    assert service.list_teams(outsider, workspace.id) == []

    service.put_team_member(owner, workspace.id, team.id, outsider.id, "member")
    assert service.list_teams(outsider, workspace.id)[0][0].id == team.id


def test_invitation_is_email_bound_and_can_assign_a_team(context):
    service, owner, outsider = context
    workspace, _membership = service.create_workspace(
        owner, name="CommonPlan", slug="commonplan", description=None
    )
    team, _team_membership = service.create_team(
        owner, workspace.id, name="Core", issue_prefix="KEY", description=None
    )
    invitation, raw_token = service.create_invitation(
        owner,
        workspace.id,
        email=outsider.email,
        role="member",
        team_id=team.id,
    )

    accepted_workspace, membership = service.accept_invitation(outsider, raw_token)

    assert invitation.accepted_at is not None
    assert accepted_workspace.id == workspace.id
    assert membership.role == "member"
    assert outsider.id in {
        member.id for member, _membership in service.team_members(outsider, workspace.id, team.id)
    }
