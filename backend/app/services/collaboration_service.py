import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends
from app.events import DomainEvent, EventPublisher, EventPublisherDep

from app.models import IssueComment, IssueEvent, IssueRelation, IssueRelationType, IssueWatcher, User
from app.repositories.collaboration_repository import CollaborationRepository, CollaborationRepositoryDep
from app.services.issue_service import IssueService, IssueServiceDep
from app.services.resource_policy import ResourceForbidden, ResourceNotFound, ResourcePolicy
from app.services.workspace_service import WorkspaceService, WorkspaceServiceDep
from app.services.view_notification_service import ViewNotificationService, ViewNotificationServiceDep


class CollaborationValidationError(Exception): pass


class CollaborationService:
    def __init__(
        self,
        repository: CollaborationRepository,
        workspaces: WorkspaceService,
        issues: IssueService,
        notifications: ViewNotificationService | None = None,
        events: EventPublisher | None = None,
    ):
        self.repository = repository
        self.workspaces = workspaces
        self.issues = issues
        self.notifications = notifications
        self.events = events
        self.policy = ResourcePolicy(workspaces)

    def create_comment(self, user: User, workspace_id: str, key: str, body: str, mentioned_user_ids: list[int] | None = None):
        issue = self._issue(user, workspace_id, key)
        normalized = body.strip()
        if not normalized:
            raise CollaborationValidationError("Comment cannot be empty")
        comment = IssueComment(id=str(uuid.uuid4()), issue_id=issue.id, author_user_id=user.id, body=normalized)
        self.repository.add(comment)
        # CommentMention has no ORM relationship to advertise insert ordering, so
        # persist the parent row before adding its foreign-key children.
        self.repository.flush()
        mentioned = self._mentioned_users(user, workspace_id, issue.team_id, mentioned_user_ids or [])
        self.repository.replace_mentions(comment.id, mentioned)
        self._ensure_watcher(issue.id, user.id, "participated")
        # The author is already added as a participant above. Trying to add the
        # same (issue_id, user_id) watcher again in this uncommitted unit of
        # work violates the composite primary key when somebody mentions
        # themselves in their first comment.
        for user_id in mentioned - {user.id}:
            self._ensure_watcher(issue.id, user_id, "mentioned")
        event_id = self._event(issue.id, user.id, "comment.created", {"comment_id": comment.id, "mentioned_user_ids": sorted(mentioned)})
        self._notify(issue, event_id, user.id, "comment", mentioned, {"issue_key": issue.key, "comment_id": comment.id})
        self._comment_event(issue, comment)
        self.repository.save_changes()
        self.repository.refresh(comment)
        return self._comment_activity(comment)

    def update_comment(self, user: User, workspace_id: str, key: str, comment_id: str, body: str, mentioned_user_ids: list[int] | None = None):
        issue = self._issue(user, workspace_id, key)
        comment = self.repository.comment(comment_id)
        if comment is None or comment.issue_id != issue.id or comment.deleted_at is not None:
            raise ResourceNotFound
        self.policy.require_comment_manager(user, workspace_id, issue, comment)
        normalized = body.strip()
        if not normalized:
            raise CollaborationValidationError("Comment cannot be empty")
        comment.body = normalized
        comment.edited_at = datetime.now(timezone.utc)
        mentioned = self._mentioned_users(user, workspace_id, issue.team_id, mentioned_user_ids or [])
        self.repository.replace_mentions(comment.id, mentioned)
        for user_id in mentioned:
            self._ensure_watcher(issue.id, user_id, "mentioned")
        event_id = self._event(issue.id, user.id, "comment.updated", {"comment_id": comment.id, "mentioned_user_ids": sorted(mentioned)})
        self._notify(issue, event_id, user.id, "comment_updated", mentioned, {"issue_key": issue.key, "comment_id": comment.id})
        self._comment_event(issue, comment)
        self.repository.save_changes()
        self.repository.refresh(comment)
        return self._comment_activity(comment)

    def delete_comment(self, user: User, workspace_id: str, key: str, comment_id: str):
        issue = self._issue(user, workspace_id, key)
        comment = self.repository.comment(comment_id)
        if comment is None or comment.issue_id != issue.id or comment.deleted_at is not None:
            raise ResourceNotFound
        self.policy.require_comment_manager(user, workspace_id, issue, comment)
        comment.deleted_at = datetime.now(timezone.utc)
        self.repository.replace_mentions(comment.id, set())
        self._event(issue.id, user.id, "comment.deleted", {"comment_id": comment.id})
        self._comment_event(issue, comment, operation="delete")
        self.repository.save_changes()

    def collaboration(self, user: User, workspace_id: str, key: str):
        issue = self._issue(user, workspace_id, key)
        return {
            "watching": self.repository.watcher(issue.id, user.id) is not None,
            "watchers": self.repository.watchers(issue.id),
            "children": self.repository.children(issue.id),
        }

    def watch(self, user: User, workspace_id: str, key: str):
        issue = self._issue(user, workspace_id, key)
        self._ensure_watcher(issue.id, user.id, "manual")
        self._event(issue.id, user.id, "watcher.added", {"user_id": user.id})
        self.repository.save_changes()
        return self.collaboration(user, workspace_id, key)

    def unwatch(self, user: User, workspace_id: str, key: str):
        issue = self._issue(user, workspace_id, key)
        watcher = self.repository.watcher(issue.id, user.id)
        if watcher is not None:
            self.repository.delete(watcher)
            self._event(issue.id, user.id, "watcher.removed", {"user_id": user.id})
            self.repository.save_changes()
        return self.collaboration(user, workspace_id, key)

    def create_sub_issue(self, user: User, workspace_id: str, key: str, values: dict):
        parent = self._issue(user, workspace_id, key)
        return self.issues.create_issue(
            user,
            workspace_id,
            parent.team_id,
            **values,
            parent_issue_id=parent.id,
        )

    def relation_types(self, user: User, workspace_id: str):
        self._workspace(user, workspace_id)
        return self.repository.relation_types(workspace_id)

    def create_relation_type(self, user: User, workspace_id: str, values: dict):
        _workspace, membership = self._workspace(user, workspace_id)
        if membership.role not in {"owner", "admin"}:
            raise ResourceForbidden
        key = values["key"].strip().lower()
        if any(row.key == key for row in self.repository.relation_types(workspace_id)):
            raise CollaborationValidationError("Relation type key already exists")
        symmetric = values.get("symmetric", False)
        forward_label = values["forward_label"].strip()
        inverse_label = values["inverse_label"].strip()
        if symmetric and forward_label != inverse_label:
            raise CollaborationValidationError("Symmetric relation labels must match")
        relation_type = IssueRelationType(
            id=str(uuid.uuid4()), workspace_id=workspace_id, key=key,
            forward_label=forward_label, inverse_label=inverse_label,
            category=values.get("category", "custom"), is_system=False,
            symmetric=symmetric, allow_cycles=values.get("allow_cycles", True),
        )
        self.repository.add(relation_type)
        self.repository.save_changes()
        self.repository.refresh(relation_type)
        return relation_type

    def relations(self, user: User, workspace_id: str, key: str):
        issue = self._issue(user, workspace_id, key)
        result = []
        for relation in self.repository.relations_for_issue(issue.id):
            relation_type = self.repository.relation_type(relation.relation_type_id)
            if relation_type is None or relation_type.archived_at is not None:
                continue
            outgoing = relation.source_issue_id == issue.id
            related = self.repository.issue_by_id(
                relation.target_issue_id if outgoing else relation.source_issue_id
            )
            if related is None:
                continue
            self.policy.require_issue_access(user, workspace_id, related)
            result.append({
                "relation": relation,
                "type": relation_type,
                "direction": "outgoing" if outgoing else "incoming",
                "label": relation_type.forward_label if outgoing or relation_type.symmetric else relation_type.inverse_label,
                "related": related,
            })
        return result

    def relation_summaries(self, user: User, workspace_id: str, team_id: str):
        try:
            self.workspaces.get_team(user, workspace_id, team_id)
        except Exception as exc:
            if exc.__class__.__name__.endswith("NotFound"):
                raise ResourceNotFound from exc
            raise ResourceForbidden from exc
        summaries = []
        for relation in self.repository.relations_for_team(team_id):
            relation_type = self.repository.relation_type(relation.relation_type_id)
            source = self.repository.issue_by_id(relation.source_issue_id)
            target = self.repository.issue_by_id(relation.target_issue_id)
            if relation_type is None or source is None or target is None or relation_type.archived_at is not None:
                continue
            for issue, related, direction in (
                (source, target, "outgoing"),
                (target, source, "incoming"),
            ):
                if issue.team_id != team_id:
                    continue
                try:
                    self.policy.require_issue_access(user, workspace_id, related)
                except (ResourceForbidden, ResourceNotFound):
                    continue
                summaries.append({
                    "issue_key": issue.key,
                    "relation_id": relation.id,
                    "type_key": relation_type.key,
                    "category": relation_type.category,
                    "direction": direction,
                    "label": relation_type.forward_label if direction == "outgoing" or relation_type.symmetric else relation_type.inverse_label,
                    "related_issue_key": related.key,
                })
        return summaries

    def create_relation(self, user: User, workspace_id: str, key: str, relation_type_id: str, target_issue_key: str, direction: str = "outgoing"):
        current = self._issue(user, workspace_id, key)
        related = self._issue(user, workspace_id, target_issue_key)
        source, target = current, related
        if direction == "incoming":
            source, target = target, source
        if source.id == target.id:
            raise CollaborationValidationError("An issue cannot relate to itself")
        relation_type = self.repository.relation_type(relation_type_id)
        if relation_type is None or relation_type.workspace_id != workspace_id or relation_type.archived_at is not None:
            raise CollaborationValidationError("Relation type does not belong to this workspace")
        source_id, target_id = source.id, target.id
        if relation_type.symmetric and source_id > target_id:
            source_id, target_id = target_id, source_id
        pairs = self.repository.relation_pairs(relation_type.id)
        if (source_id, target_id) in pairs:
            raise CollaborationValidationError("This issue relation already exists")
        if not relation_type.allow_cycles and self._path_exists(pairs, target_id, source_id):
            raise CollaborationValidationError("This dependency would create a cycle")
        relation = IssueRelation(
            id=str(uuid.uuid4()), workspace_id=workspace_id,
            relation_type_id=relation_type.id, source_issue_id=source_id,
            target_issue_id=target_id, created_by_user_id=user.id,
        )
        self.repository.add(relation)
        self.repository.flush()
        self._event(current.id, user.id, "issue.relation_added", {
            "relation_id": relation.id, "relation_type": relation_type.key,
            "related_issue_key": related.key,
        })
        self.repository.save_changes()
        self.repository.refresh(relation)
        return next(row for row in self.relations(user, workspace_id, key) if row["relation"].id == relation.id)

    def delete_relation(self, user: User, workspace_id: str, key: str, relation_id: str):
        issue = self._issue(user, workspace_id, key)
        relation = self.repository.relation(relation_id)
        if relation is None or relation.workspace_id != workspace_id or issue.id not in {relation.source_issue_id, relation.target_issue_id}:
            raise ResourceNotFound
        related_id = relation.target_issue_id if relation.source_issue_id == issue.id else relation.source_issue_id
        related = self.repository.issue_by_id(related_id)
        self.policy.require_issue_access(user, workspace_id, related)
        relation_type = self.repository.relation_type(relation.relation_type_id)
        self.repository.delete(relation)
        self._event(issue.id, user.id, "issue.relation_removed", {
            "relation_id": relation_id,
            "relation_type": relation_type.key if relation_type else "unknown",
            "related_issue_key": related.key,
        })
        self.repository.save_changes()

    def _issue(self, user, workspace_id, key):
        issue = self.repository.issue(workspace_id, key.upper())
        self.policy.require_issue_access(user, workspace_id, issue)
        return issue

    def _workspace(self, user, workspace_id):
        try:
            return self.workspaces.get_workspace(user, workspace_id)
        except Exception as exc:
            if exc.__class__.__name__.endswith("NotFound"):
                raise ResourceNotFound from exc
            raise ResourceForbidden from exc

    @staticmethod
    def _path_exists(pairs, start_id, target_id):
        adjacency = {}
        for source_id, destination_id in pairs:
            adjacency.setdefault(source_id, set()).add(destination_id)
        pending = [start_id]
        visited = set()
        while pending:
            current = pending.pop()
            if current == target_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, ()))
        return False

    def _mentioned_users(self, user, workspace_id, team_id, mentioned_user_ids):
        members = self.workspaces.team_members(user, workspace_id, team_id)
        member_ids = {member.id for member, _membership in members}
        mentioned = set(mentioned_user_ids)
        if not mentioned.issubset(member_ids):
            raise CollaborationValidationError("Mentioned users must be active members of this team")
        return mentioned

    def _ensure_watcher(self, issue_id, user_id, reason):
        if self.repository.watcher(issue_id, user_id) is None:
            self.repository.add(IssueWatcher(issue_id=issue_id, user_id=user_id, reason=reason))

    def _event(self, issue_id, actor_user_id, event_type, changes):
        event_id = str(uuid.uuid4())
        self.repository.add(IssueEvent(
            id=event_id, issue_id=issue_id, actor_user_id=actor_user_id,
            event_type=event_type, changes={"schema_version": 1, **changes},
        ))
        return event_id

    def _notify(self, issue, event_id, actor_user_id, kind, mentioned, payload):
        if self.notifications is None:
            return
        recipients = (self.repository.watcher_user_ids(issue.id) | set(mentioned)) - {actor_user_id}
        self.notifications.enqueue_many(
            recipients=recipients, workspace_id=issue.workspace_id, issue_id=issue.id,
            event_id=event_id, actor_user_id=actor_user_id, kind=kind, payload=payload,
        )
        if actor_user_id in mentioned:
            self.notifications.enqueue_many(
                recipients={actor_user_id}, workspace_id=issue.workspace_id, issue_id=issue.id,
                event_id=event_id, actor_user_id=actor_user_id, kind="self_mention", payload=payload,
                include_actor=True,
            )

    def _comment_activity(self, comment):
        return {
            "id": comment.id, "kind": "comment", "actor_user_id": comment.author_user_id,
            "actor_name": None, "body": comment.body, "event_type": None, "changes": {},
            "created_at": comment.created_at, "edited_at": comment.edited_at,
        }

    def _comment_event(self, issue, comment, *, operation="upsert"):
        if self.events is not None:
            self.events.publish(DomainEvent(
                workspace_id=issue.workspace_id,
                source_type="issue_comment",
                source_id=comment.id,
                operation=operation,
                metadata={"issue_id": issue.id, "team_id": issue.team_id},
            ))


def get_collaboration_service(
    repository: CollaborationRepositoryDep,
    workspaces: WorkspaceServiceDep,
    issues: IssueServiceDep,
    notifications: ViewNotificationServiceDep,
    events: EventPublisherDep,
) -> CollaborationService:
    return CollaborationService(repository, workspaces, issues, notifications, events)


CollaborationServiceDep = Annotated[CollaborationService, Depends(get_collaboration_service)]
