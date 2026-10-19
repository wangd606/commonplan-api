import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import Depends
from app.events import DomainEvent, EventPublisher, EventPublisherDep
from app.models import Cycle, Issue, IssueComment, IssueEvent, Label, Project, TeamCycleSettings, User
from app.repositories.errors import RepositoryConflictError
from app.repositories.issue_repository import IssueRepository, IssueRepositoryDep
from app.services.workspace_service import WorkspaceService, WorkspaceServiceDep


class IssueNotFound(Exception): pass
class IssueForbidden(Exception): pass
class IssueConflict(Exception): pass
class IssueValidationError(Exception): pass


class IssueService:
    def __init__(
        self, repository: IssueRepository, workspaces: WorkspaceService,
        events: EventPublisher | None = None,
    ):
        self.repository = repository
        self.workspaces = workspaces
        self.events = events

    def states(self, user: User, workspace_id: str, team_id: str):
        self._access(user, workspace_id, team_id)
        return self.repository.states(team_id)

    def cycles(self, user: User, workspace_id: str, team_id: str):
        self._access(user, workspace_id, team_id)
        self._synchronize_cycle_schedule(team_id)
        return self.repository.cycles(team_id)

    def cycle_settings(self, user: User, workspace_id: str, team_id: str):
        self._access(user, workspace_id, team_id)
        settings = self.repository.cycle_settings(team_id)
        if settings is None:
            raise IssueNotFound
        return settings

    def update_cycle_settings(self, user: User, workspace_id: str, team_id: str, changes: dict):
        _team, team_membership, workspace_membership = self._access(user, workspace_id, team_id)
        if workspace_membership.role not in {"owner", "admin"} and (
            team_membership is None or team_membership.role != "lead"
        ):
            raise IssueForbidden
        settings = self.repository.cycle_settings(team_id)
        if settings is None:
            settings = TeamCycleSettings(team_id=team_id, updated_by_user_id=user.id)
            self.repository.add(settings)
        for field in ("enabled", "duration_weeks", "upcoming_cycle_count", "next_cycle_starts_on", "rollover_incomplete"):
            if field in changes:
                setattr(settings, field, changes[field])
        if settings.enabled and settings.next_cycle_starts_on is None:
            settings.next_cycle_starts_on = date.today()
        settings.updated_by_user_id = user.id
        self._commit()
        self.repository.refresh(settings)
        self._synchronize_cycle_schedule(team_id)
        self.repository.refresh(settings)
        return settings

    def create_cycle(self, user: User, workspace_id: str, team_id: str, *, name, starts_on, ends_on):
        _team, team_membership, workspace_membership = self._access(user, workspace_id, team_id)
        if workspace_membership.role not in {"owner", "admin"} and (team_membership is None or team_membership.role != "lead"):
            raise IssueForbidden
        if starts_on >= ends_on:
            raise IssueValidationError("Cycle start must be before its end")
        for existing in self.repository.cycles(team_id):
            if starts_on < existing.ends_on and ends_on > existing.starts_on:
                raise IssueConflict("Cycle dates overlap an existing cycle")
        cycle = Cycle(id=str(uuid.uuid4()), team_id=team_id, name=name.strip(), starts_on=starts_on, ends_on=ends_on)
        self.repository.add(cycle)
        self._commit()
        self.repository.refresh(cycle)
        return cycle

    def labels(self, user: User, workspace_id: str, team_id: str):
        self._access(user, workspace_id, team_id)
        return self.repository.labels(team_id)

    def create_label(self, user: User, workspace_id: str, team_id: str, *, name, color):
        self._access(user, workspace_id, team_id)
        label = Label(id=str(uuid.uuid4()), team_id=team_id, name=name.strip(), color=color.upper())
        self.repository.add(label)
        self._commit()
        return label

    def list_issues(self, user: User, workspace_id: str, team_id: str, **filters):
        self._access(user, workspace_id, team_id)
        return [(issue, self.repository.issue_labels(issue.id)) for issue in self.repository.issues(team_id, **filters)]

    def create_issue(self, user: User, workspace_id: str, team_id: str, **values):
        self._access(user, workspace_id, team_id)
        team = self.repository.team_for_update(team_id)
        if team is None or team.workspace_id != workspace_id:
            raise IssueNotFound
        state_id = values.get("workflow_state_id")
        state = self.repository.state(state_id) if state_id else self.repository.default_state(team_id)
        if state is None or state.team_id != team_id:
            raise IssueValidationError("Workflow state must belong to the selected team")
        self._validate_refs(
            team_id, values.get("assignee_user_id"), values.get("cycle_id"),
            values.get("label_ids", []), values.get("project_id"), values.get("milestone_id"),
        )
        parent_issue_id = values.get("parent_issue_id")
        if parent_issue_id is not None:
            parent = self.repository.issue_by_id(parent_issue_id)
            if parent is None or parent.team_id != team_id:
                raise IssueValidationError("Parent issue must belong to the selected team")
        number = team.next_issue_number
        team.next_issue_number += 1
        issue = Issue(
            id=str(uuid.uuid4()), workspace_id=workspace_id, team_id=team_id,
            number=number, key=f"{team.issue_prefix}-{number}",
            title=values["title"].strip(), description=values.get("description"),
            workflow_state_id=state.id, priority=values.get("priority", 0),
            creator_user_id=user.id, assignee_user_id=values.get("assignee_user_id"),
            cycle_id=values.get("cycle_id"), due_date=values.get("due_date"),
            project_id=values.get("project_id"), milestone_id=values.get("milestone_id"),
            parent_issue_id=parent_issue_id,
            position=Decimal(number), version=1,
        )
        self.repository.add(issue)
        self.repository.flush()
        self.repository.replace_labels(issue.id, values.get("label_ids", []))
        self._event(
            issue,
            user,
            "issue.created",
            {"key": issue.key, "title": issue.title},
        )
        self._knowledge_event(issue)
        self._commit()
        self.repository.refresh(issue)
        return issue, self.repository.issue_labels(issue.id)

    def get_issue(self, user: User, workspace_id: str, key: str):
        issue = self.repository.issue(workspace_id, key)
        if issue is None:
            raise IssueNotFound
        self._access(user, workspace_id, issue.team_id)
        return issue, self.repository.issue_labels(issue.id)

    def issue_details(self, issue: Issue, labels=None):
        """Return presentation data without exposing repository access to routes."""
        state = self.repository.state(issue.workflow_state_id)
        resolved_labels = labels if labels is not None else self.repository.issue_labels(issue.id)
        return state, resolved_labels

    def update_issue(self, user: User, workspace_id: str, key: str, changes: dict):
        issue = self.repository.issue(workspace_id, key, for_update=True)
        if issue is None:
            raise IssueNotFound
        self._access(user, workspace_id, issue.team_id)
        expected_version = changes.pop("version")
        if issue.version != expected_version:
            raise IssueConflict("Issue changed since it was loaded")
        label_ids = changes.pop("label_ids", None)
        old_label_ids = [label.id for label in self.repository.issue_labels(issue.id)]
        state_id = changes.get("workflow_state_id")
        if state_id is not None:
            state = self.repository.state(state_id)
            if state is None or state.team_id != issue.team_id:
                raise IssueValidationError("Workflow state must belong to the selected team")
        project_id = changes.get("project_id", issue.project_id)
        milestone_id = changes.get("milestone_id", issue.milestone_id)
        self._validate_refs(
            issue.team_id, changes.get("assignee_user_id"), changes.get("cycle_id"),
            label_ids or [], project_id, milestone_id,
        )
        event_changes = {}
        for field, value in changes.items():
            old_value = getattr(issue, field)
            if old_value != value:
                event_changes[field] = {
                    "from": self._json_value(old_value),
                    "to": self._json_value(value),
                }
            setattr(issue, field, value.strip() if field == "title" and isinstance(value, str) else value)
        if label_ids is not None:
            self.repository.replace_labels(issue.id, label_ids)
            if old_label_ids != label_ids:
                event_changes["label_ids"] = {"from": old_label_ids, "to": label_ids}
        issue.version += 1
        if event_changes:
            self._event(issue, user, "issue.updated", {"fields": event_changes})
            self._knowledge_event(issue)
        self._commit()
        self.repository.refresh(issue)
        return issue, self.repository.issue_labels(issue.id)

    def add_comment(self, user: User, workspace_id: str, key: str, body: str):
        issue = self.repository.issue(workspace_id, key)
        if issue is None:
            raise IssueNotFound
        self._access(user, workspace_id, issue.team_id)
        if not body.strip():
            raise IssueValidationError("Comment cannot be empty")
        comment = IssueComment(
            id=str(uuid.uuid4()),
            issue_id=issue.id,
            author_user_id=user.id,
            body=body.strip(),
        )
        self.repository.add(comment)
        self.repository.flush()
        self._event(
            issue,
            user,
            "comment.created",
            {"comment_id": comment.id},
        )
        self._publish(DomainEvent(
            workspace_id=issue.workspace_id,
            source_type="issue_comment",
            source_id=comment.id,
            metadata={"issue_id": issue.id, "team_id": issue.team_id},
        ))
        self._commit()
        self.repository.refresh(comment)
        return self._comment_activity(comment)

    def activity(self, user: User, workspace_id: str, key: str):
        issue = self.repository.issue(workspace_id, key)
        if issue is None:
            raise IssueNotFound
        self._access(user, workspace_id, issue.team_id)
        rows = [self._comment_activity(comment) for comment in self.repository.comments(issue.id)]
        rows.extend(self._event_activity(event) for event in self.repository.events(issue.id))
        return sorted(rows, key=lambda row: (row["created_at"], row["id"]))

    def my_issues(self, user: User, workspace_id: str | None = None):
        if workspace_id:
            self.workspaces.get_workspace(user, workspace_id)
        issues = self.repository.assigned_issues(user.id, workspace_id)
        visible = []
        for issue in issues:
            try:
                self._access(user, issue.workspace_id, issue.team_id)
                visible.append((issue, self.repository.issue_labels(issue.id)))
            except Exception:
                continue
        return visible

    def team_overview(self, user: User, workspace_id: str, team_id: str):
        self._access(user, workspace_id, team_id)
        issues = self.repository.issues(team_id)
        state_categories = {
            state.id: state.category for state in self.repository.states(team_id)
        }
        today = date.today()
        current_cycle = next(
            (
                cycle
                for cycle in self.repository.cycles(team_id)
                if cycle.starts_on <= today < cycle.ends_on
            ),
            None,
        )
        return {
            "team_id": team_id,
            "project_count": self.repository.active_project_count(team_id),
            "open_issue_count": sum(
                state_categories.get(issue.workflow_state_id) not in {"done", "canceled"}
                for issue in issues
            ),
            "current_cycle": (
                {
                    "id": current_cycle.id,
                    "name": current_cycle.name,
                    "starts_on": current_cycle.starts_on.isoformat(),
                    "ends_on": current_cycle.ends_on.isoformat(),
                }
                if current_cycle
                else None
            ),
            "recent_issues": [
                {
                    "key": issue.key,
                    "title": issue.title,
                    "priority": issue.priority,
                    "workflow_state": self.repository.state(issue.workflow_state_id).name,
                }
                for issue in sorted(issues, key=lambda item: item.updated_at, reverse=True)[:5]
            ],
        }

    def _access(self, user, workspace_id, team_id):
        try:
            return self.workspaces.get_team(user, workspace_id, team_id)
        except Exception as exc:
            if exc.__class__.__name__.endswith("NotFound"):
                raise IssueNotFound from exc
            raise IssueForbidden from exc

    def _validate_refs(self, team_id, assignee_user_id, cycle_id, label_ids, project_id=None, milestone_id=None):
        if assignee_user_id is not None and not self.workspaces.is_team_member(team_id, assignee_user_id):
            raise IssueValidationError("Assignee must be an active team member")
        if cycle_id is not None:
            cycle = self.repository.cycle(cycle_id)
            if cycle is None or cycle.team_id != team_id:
                raise IssueValidationError("Cycle must belong to the selected team")
        for label_id in label_ids:
            label = self.repository.label(label_id)
            if label is None or label.team_id != team_id:
                raise IssueValidationError("Label must belong to the selected team")
        project = self.repository.project(project_id) if project_id is not None else None
        if project_id is not None and (project is None or project.team_id != team_id):
            raise IssueValidationError("Project must belong to the selected team")
        if milestone_id is not None:
            milestone = self.repository.milestone(milestone_id)
            if milestone is None or milestone.project_id != project_id:
                raise IssueValidationError("Milestone must belong to the selected project")

    def _synchronize_cycle_schedule(self, team_id: str):
        settings = self.repository.cycle_settings(team_id)
        if settings is None or not settings.enabled or settings.next_cycle_starts_on is None:
            return
        cycles = self.repository.cycles(team_id)
        today = date.today()
        duration = timedelta(weeks=settings.duration_weeks)
        cursor = max((cycle.ends_on for cycle in cycles), default=settings.next_cycle_starts_on)
        cursor = max(cursor, settings.next_cycle_starts_on)
        future_count = sum(cycle.starts_on > today for cycle in cycles)
        changed = False
        generated = 0
        while future_count < settings.upcoming_cycle_count and generated < 64:
            cycle = Cycle(
                id=str(uuid.uuid4()),
                team_id=team_id,
                name=f"Cycle {cursor.isoformat()}",
                starts_on=cursor,
                ends_on=cursor + duration,
            )
            self.repository.add(cycle)
            cycles.append(cycle)
            if cycle.starts_on > today:
                future_count += 1
            cursor = cycle.ends_on
            generated += 1
            changed = True

        if changed:
            self._commit()
            cycles = self.repository.cycles(team_id)

        now = datetime.now(timezone.utc)
        state_categories = {state.id: state.category for state in self.repository.states(team_id)}
        rollover_changed = False
        for cycle in sorted(cycles, key=lambda row: (row.starts_on, row.id)):
            if cycle.completed_at is not None or cycle.ends_on > today:
                continue
            next_cycle = next(
                (candidate for candidate in cycles if candidate.starts_on >= cycle.ends_on),
                None,
            )
            if next_cycle is None:
                continue
            if settings.rollover_incomplete:
                for issue in self.repository.issues(team_id, cycle_id=cycle.id):
                    if state_categories.get(issue.workflow_state_id) in {"done", "canceled"}:
                        continue
                    previous_cycle_id = issue.cycle_id
                    issue.cycle_id = next_cycle.id
                    issue.version += 1
                    self._event(issue, None, "issue.updated", {"fields": {"cycle_id": {"from": previous_cycle_id, "to": next_cycle.id}}, "automation": "cycle_rollover"})
                    self._knowledge_event(issue)
            cycle.completed_at = now
            rollover_changed = True
        if rollover_changed:
            self._commit()

    def _event(self, issue: Issue, user: User | None, event_type: str, changes: dict):
        self.repository.add(IssueEvent(
            id=str(uuid.uuid4()),
            issue_id=issue.id,
            actor_user_id=user.id if user else None,
            event_type=event_type,
            changes={"schema_version": 1, **changes},
        ))

    def _knowledge_event(self, issue: Issue) -> None:
        self._publish(DomainEvent(
            workspace_id=issue.workspace_id,
            source_type="issue",
            source_id=issue.id,
            source_version=issue.version,
            metadata={"issue_id": issue.id, "team_id": issue.team_id},
        ))

    def _publish(self, event: DomainEvent) -> None:
        if self.events is not None:
            self.events.publish(event)

    def _comment_activity(self, comment: IssueComment):
        author = self.repository.user(comment.author_user_id)
        return {
            "id": comment.id,
            "kind": "comment",
            "actor_user_id": comment.author_user_id,
            "actor_name": author.name if author else None,
            "body": comment.body,
            "event_type": None,
            "changes": {},
            "created_at": comment.created_at,
            "edited_at": comment.edited_at,
        }

    def _event_activity(self, event: IssueEvent):
        actor = self.repository.user(event.actor_user_id)
        return {
            "id": event.id,
            "kind": "event",
            "actor_user_id": event.actor_user_id,
            "actor_name": actor.name if actor else None,
            "body": None,
            "event_type": event.event_type,
            "changes": event.changes,
            "created_at": event.created_at,
            "edited_at": None,
        }

    @staticmethod
    def _json_value(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    def _commit(self):
        try:
            self.repository.save_changes()
        except RepositoryConflictError as exc:
            raise IssueConflict("Issue planning value already exists") from exc


def get_issue_service(
    repository: IssueRepositoryDep,
    workspaces: WorkspaceServiceDep,
    events: EventPublisherDep,
) -> IssueService:
    return IssueService(repository, workspaces, events)


IssueServiceDep = Annotated[IssueService, Depends(get_issue_service)]
