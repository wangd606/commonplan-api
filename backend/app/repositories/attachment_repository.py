from typing import Annotated, Protocol

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import DbSession
from app.models import (
    DocumentIngestion, DocumentPage, EmbeddingProfile, FileAsset, Issue, IssueAttachment, KnowledgeNode,
    Project, ProjectAttachment, Team,
)
from app.repositories.errors import RepositoryConflictError


class AttachmentRepository(Protocol):
    def issue_by_key(self, workspace_id: str, key: str) -> Issue | None: ...
    def project(self, project_id: str) -> Project | None: ...
    def file(self, file_id: str, *, for_update: bool = False) -> FileAsset | None: ...
    def issue_files(self, issue_id: str) -> list[FileAsset]: ...
    def project_files(self, project_id: str) -> list[FileAsset]: ...
    def file_scope(self, file_id: str) -> tuple[str, str] | None: ...
    def ingestion(
        self, file_id: str
    ) -> tuple[DocumentIngestion, EmbeddingProfile, int, int] | None: ...
    def add(self, record: object) -> None: ...
    def save_changes(self) -> None: ...
    def refresh(self, record: object) -> None: ...


class SqlAlchemyAttachmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def issue_by_key(self, workspace_id: str, key: str) -> Issue | None:
        return self.db.scalar(select(Issue).where(
            Issue.workspace_id == workspace_id, Issue.key == key, Issue.archived_at.is_(None)
        ))

    def project(self, project_id: str) -> Project | None:
        return self.db.scalar(select(Project).where(
            Project.id == project_id, Project.archived_at.is_(None)
        ))

    def file(self, file_id: str, *, for_update: bool = False) -> FileAsset | None:
        stmt = select(FileAsset).where(FileAsset.id == file_id, FileAsset.deleted_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.scalar(stmt)

    def issue_files(self, issue_id: str) -> list[FileAsset]:
        return list(self.db.scalars(
            select(FileAsset)
            .join(IssueAttachment, IssueAttachment.file_asset_id == FileAsset.id)
            .where(IssueAttachment.issue_id == issue_id, FileAsset.deleted_at.is_(None))
            .order_by(FileAsset.created_at.desc(), FileAsset.id.desc())
        ))

    def project_files(self, project_id: str) -> list[FileAsset]:
        return list(self.db.scalars(
            select(FileAsset)
            .join(ProjectAttachment, ProjectAttachment.file_asset_id == FileAsset.id)
            .where(ProjectAttachment.project_id == project_id, FileAsset.deleted_at.is_(None))
            .order_by(FileAsset.created_at.desc(), FileAsset.id.desc())
        ))

    def file_scope(self, file_id: str) -> tuple[str, str] | None:
        issue = self.db.execute(
            select(Issue.workspace_id, Issue.team_id)
            .join(IssueAttachment, IssueAttachment.issue_id == Issue.id)
            .where(IssueAttachment.file_asset_id == file_id)
        ).first()
        if issue:
            return issue.workspace_id, issue.team_id
        project = self.db.execute(
            select(Project.team_id)
            .join(ProjectAttachment, ProjectAttachment.project_id == Project.id)
            .where(ProjectAttachment.file_asset_id == file_id)
        ).first()
        if not project:
            return None
        workspace_id = self.db.scalar(select(Team.workspace_id).where(Team.id == project.team_id))
        return (workspace_id, project.team_id) if workspace_id else None

    def ingestion(
        self, file_id: str
    ) -> tuple[DocumentIngestion, EmbeddingProfile, int, int] | None:
        record = self.db.scalar(
            select(DocumentIngestion).where(DocumentIngestion.file_asset_id == file_id)
        )
        if record is None:
            return None
        profile = self.db.get(EmbeddingProfile, record.embedding_profile_id)
        if profile is None:
            return None
        pages = len(list(self.db.scalars(
            select(DocumentPage.id).where(DocumentPage.ingestion_id == record.id)
        )))
        chunks = len(list(self.db.scalars(
            select(KnowledgeNode.id).where(
                KnowledgeNode.knowledge_source_id == record.knowledge_source_id,
                KnowledgeNode.indexable.is_(True),
            )
        )))
        return record, profile, pages, chunks

    def add(self, record: object) -> None:
        self.db.add(record)

    def save_changes(self) -> None:
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise RepositoryConflictError from exc

    def refresh(self, record: object) -> None:
        self.db.refresh(record)


def get_attachment_repository(db: DbSession) -> AttachmentRepository:
    return SqlAlchemyAttachmentRepository(db)


AttachmentRepositoryDep = Annotated[AttachmentRepository, Depends(get_attachment_repository)]
