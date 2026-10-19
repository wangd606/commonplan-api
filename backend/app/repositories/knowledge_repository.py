from datetime import datetime, timezone
from hashlib import sha256
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ingestion.knowledge_types import SourceMaterial
from app.ingestion.types import ChunkDraft
from app.models import (
    Issue, IssueComment, IssueLabel, KnowledgeChunk, KnowledgeSource, Label,
    Project, ProjectMilestone, ProjectObjective, ProjectUpdate, Team, User,
    WorkflowState,
)


class KnowledgeRepository:
    """Owns every database operation performed by the knowledge indexer."""

    def __init__(self, db: Session):
        self.db = db

    def load(self, source_type: str, source_id: str) -> SourceMaterial | None:
        loader = {
            "issue": self._issue,
            "issue_comment": self._comment,
            "project": self._project,
            "project_update": self._project_update,
        }.get(source_type)
        return loader(source_id) if loader else None

    def _issue(self, source_id: str) -> SourceMaterial | None:
        row = self.db.execute(
            select(Issue, Team, WorkflowState, Project)
            .join(Team, Team.id == Issue.team_id)
            .join(WorkflowState, WorkflowState.id == Issue.workflow_state_id)
            .outerjoin(Project, Project.id == Issue.project_id)
            .where(Issue.id == source_id, Issue.archived_at.is_(None))
        ).first()
        if row is None:
            return None
        issue, team, state, project = row
        labels = list(self.db.scalars(
            select(Label.name)
            .join(IssueLabel, IssueLabel.label_id == Label.id)
            .where(IssueLabel.issue_id == issue.id)
            .order_by(Label.name)
        ))
        return SourceMaterial(
            workspace_id=issue.workspace_id,
            team_id=issue.team_id,
            source_type="issue",
            source_id=issue.id,
            source_version=issue.version,
            title=f"{issue.key}: {issue.title}",
            sections=tuple((name, value) for name, value in (
                ("Description", issue.description or ""),
                ("Team", team.name),
                ("Project", project.name if project else ""),
                ("Workflow state", state.name),
                ("Labels", ", ".join(labels)),
            ) if value),
            metadata={
                "issue_key": issue.key,
                "workflow_state_id": issue.workflow_state_id,
                "priority": issue.priority,
                "assignee_user_id": issue.assignee_user_id,
                "cycle_id": issue.cycle_id,
                "label_names": labels,
            },
            project_id=issue.project_id,
            issue_id=issue.id,
        )

    def _comment(self, source_id: str) -> SourceMaterial | None:
        row = self.db.execute(
            select(IssueComment, Issue, Team, Project, User)
            .join(Issue, Issue.id == IssueComment.issue_id)
            .join(Team, Team.id == Issue.team_id)
            .outerjoin(Project, Project.id == Issue.project_id)
            .join(User, User.id == IssueComment.author_user_id)
            .where(IssueComment.id == source_id, IssueComment.deleted_at.is_(None))
        ).first()
        if row is None:
            return None
        comment, issue, team, project, author = row
        version = int((comment.edited_at or comment.created_at).timestamp() * 1_000_000)
        return SourceMaterial(
            workspace_id=issue.workspace_id,
            team_id=issue.team_id,
            source_type="issue_comment",
            source_id=comment.id,
            source_version=version,
            title=f"Comment on {issue.key}: {issue.title}",
            sections=(("Author", author.name), ("Comment", comment.body)),
            metadata={"issue_key": issue.key, "author_user_id": comment.author_user_id},
            project_id=project.id if project else None,
            issue_id=issue.id,
        )

    def _project(self, source_id: str) -> SourceMaterial | None:
        row = self.db.execute(
            select(Project, Team)
            .join(Team, Team.id == Project.team_id)
            .where(Project.id == source_id, Project.archived_at.is_(None))
        ).first()
        if row is None:
            return None
        project, team = row
        objectives = list(self.db.scalars(
            select(ProjectObjective).where(ProjectObjective.project_id == project.id)
            .order_by(ProjectObjective.position, ProjectObjective.id)
        ))
        milestones = list(self.db.scalars(
            select(ProjectMilestone).where(ProjectMilestone.project_id == project.id)
            .order_by(ProjectMilestone.position, ProjectMilestone.id)
        ))
        objective_text = "\n".join(f"- {row.body}" for row in objectives)
        milestone_text = "\n".join(
            f"- {row.name}: {row.description or ''}".rstrip() for row in milestones
        )
        version = int(project.updated_at.timestamp() * 1_000_000)
        return SourceMaterial(
            workspace_id=team.workspace_id,
            team_id=project.team_id,
            source_type="project",
            source_id=project.id,
            source_version=version,
            title=project.name,
            sections=tuple((name, value) for name, value in (
                ("Summary", project.summary or ""),
                ("Description", project.description or ""),
                ("Team", team.name),
                ("Objectives", objective_text),
                ("Milestones", milestone_text),
            ) if value),
            metadata={"status": project.status, "lead_user_id": project.lead_user_id},
            project_id=project.id,
        )

    def _project_update(self, source_id: str) -> SourceMaterial | None:
        row = self.db.execute(
            select(ProjectUpdate, Project, Team, User)
            .join(Project, Project.id == ProjectUpdate.project_id)
            .join(Team, Team.id == Project.team_id)
            .join(User, User.id == ProjectUpdate.author_user_id)
            .where(ProjectUpdate.id == source_id, Project.archived_at.is_(None))
        ).first()
        if row is None:
            return None
        update, project, team, author = row
        version = int((update.edited_at or update.created_at).timestamp() * 1_000_000)
        return SourceMaterial(
            workspace_id=team.workspace_id,
            team_id=project.team_id,
            source_type="project_update",
            source_id=update.id,
            source_version=version,
            title=f"Update for project {project.name}",
            sections=(("Author", author.name), ("Update", update.body)),
            metadata={"health": update.health, "author_user_id": update.author_user_id},
            project_id=project.id,
        )

    def is_current(
        self, material: SourceMaterial, *, content_hash: str, renderer_version: str,
        chunker_version: str, embedding_provider: str, embedding_model: str,
        embedding_dimensions: int,
    ) -> bool:
        source = self._source(material.source_type, material.source_id)
        return bool(
            source and source.status == "ready" and source.deleted_at is None
            and source.source_version == material.source_version
            and source.content_hash == content_hash
            and source.renderer_version == renderer_version
            and source.chunker_version == chunker_version
            and source.embedding_provider == embedding_provider
            and source.embedding_model == embedding_model
            and source.embedding_dimensions == embedding_dimensions
        )

    def begin(
        self, material: SourceMaterial, *, content_hash: str, renderer_version: str,
        chunker_version: str, embedding_provider: str, embedding_model: str,
        embedding_dimensions: int,
    ) -> None:
        source = self._source(material.source_type, material.source_id)
        if source is None:
            source = KnowledgeSource(
                id=str(uuid.uuid4()), workspace_id=material.workspace_id,
                team_id=material.team_id, source_type=material.source_type,
                source_id=material.source_id, content_hash=content_hash,
                renderer_version=renderer_version, chunker_version=chunker_version,
                embedding_provider=embedding_provider, embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
            )
            self.db.add(source)
        source.workspace_id = material.workspace_id
        source.team_id = material.team_id
        source.project_id = material.project_id
        source.issue_id = material.issue_id
        source.source_version = material.source_version
        source.content_hash = content_hash
        source.renderer_version = renderer_version
        source.chunker_version = chunker_version
        source.embedding_provider = embedding_provider
        source.embedding_model = embedding_model
        source.embedding_dimensions = embedding_dimensions
        source.status = "embedding"
        source.error_code = None
        source.error_message = None
        source.deleted_at = None
        self.db.commit()

    def replace(
        self, material: SourceMaterial, *, content_hash: str, renderer_version: str,
        chunker_version: str, embedding_provider: str, embedding_model: str,
        embedding_dimensions: int, chunks: list[ChunkDraft], embeddings: list[list[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Embedding count does not match chunk count")
        source = self._source(material.source_type, material.source_id)
        if source is None:
            raise ValueError("Knowledge source was not initialized before replacement")
        source.workspace_id = material.workspace_id
        source.team_id = material.team_id
        source.project_id = material.project_id
        source.issue_id = material.issue_id
        source.source_version = material.source_version
        source.content_hash = content_hash
        source.renderer_version = renderer_version
        source.chunker_version = chunker_version
        source.embedding_provider = embedding_provider
        source.embedding_model = embedding_model
        source.embedding_dimensions = embedding_dimensions
        source.status = "embedding"
        source.error_code = None
        source.error_message = None
        source.deleted_at = None
        self.db.flush()
        self.db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.knowledge_source_id == source.id))
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            self.db.add(KnowledgeChunk(
                id=str(uuid.uuid4()), knowledge_source_id=source.id,
                workspace_id=material.workspace_id, team_id=material.team_id,
                project_id=material.project_id, issue_id=material.issue_id,
                chunk_index=chunk.chunk_index, content=chunk.content,
                content_hash=sha256(chunk.content.encode()).hexdigest(),
                token_count=chunk.token_count, embedding=embedding,
                chunk_metadata={**material.metadata, **chunk.metadata,
                                "source_type": material.source_type,
                                "source_id": material.source_id},
            ))
        source.status = "ready"
        source.indexed_at = datetime.now(timezone.utc)
        self.db.commit()

    def delete_source(self, source_type: str, source_id: str) -> None:
        source = self._source(source_type, source_id)
        if source is None:
            return
        self.db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.knowledge_source_id == source.id))
        source.status = "deleted"
        source.deleted_at = datetime.now(timezone.utc)
        self.db.commit()

    def fail(self, source_type: str, source_id: str, error: Exception) -> None:
        self.db.rollback()
        source = self._source(source_type, source_id)
        if source is not None:
            source.status = "failed"
            source.error_code = error.__class__.__name__[:80]
            source.error_message = str(error)[:4000]
            self.db.commit()

    def _source(self, source_type: str, source_id: str) -> KnowledgeSource | None:
        return self.db.scalar(select(KnowledgeSource).where(
            KnowledgeSource.source_type == source_type,
            KnowledgeSource.source_id == source_id,
        ))
