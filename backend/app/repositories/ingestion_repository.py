from datetime import datetime, timezone
from hashlib import sha256
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ingestion.chunking import count_tokens
from app.ingestion.embedding_profiles import embedding_profile_key
from app.ingestion.types import AttachmentScope, ChunkDraft, ParsedDocument
from app.models import (
    DocumentIngestion,
    DocumentPage,
    EmbeddingProfile,
    FileAsset,
    Issue,
    IssueAttachment,
    KnowledgeEmbedding,
    KnowledgeNode,
    KnowledgeSource,
    Project,
    ProjectAttachment,
    Team,
)


class IngestionRepository:
    """The only ingestion component allowed to access a SQLAlchemy session."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def file(self, file_id: str) -> FileAsset | None:
        return self.db.get(FileAsset, file_id)

    def scope(self, file_id: str) -> AttachmentScope | None:
        issue = self.db.execute(
            select(Issue.workspace_id, Issue.team_id, Issue.id)
            .join(IssueAttachment, IssueAttachment.issue_id == Issue.id)
            .where(IssueAttachment.file_asset_id == file_id)
        ).first()
        if issue:
            return AttachmentScope(issue.workspace_id, issue.team_id, issue_id=issue.id)
        project = self.db.execute(
            select(Team.workspace_id, Project.team_id, Project.id)
            .join(ProjectAttachment, ProjectAttachment.project_id == Project.id)
            .join(Team, Team.id == Project.team_id)
            .where(ProjectAttachment.file_asset_id == file_id)
        ).first()
        if project:
            return AttachmentScope(project.workspace_id, project.team_id, project_id=project.id)
        return None

    def ingestion(self, file_id: str) -> DocumentIngestion | None:
        return self.db.scalar(
            select(DocumentIngestion).where(DocumentIngestion.file_asset_id == file_id)
        )

    def is_current(
        self, file_id: str, *, content_sha256: str, chunker_version: str,
        embedding_provider: str, embedding_model: str, embedding_dimensions: int,
    ) -> bool:
        ingestion = self.ingestion(file_id)
        profile = self._profile(
            embedding_provider, embedding_model, embedding_dimensions, create=False
        )
        return bool(
            ingestion
            and profile
            and ingestion.status == "ready"
            and ingestion.content_sha256 == content_sha256
            and ingestion.chunker_version == chunker_version
            and ingestion.embedding_profile_id == profile.id
        )

    def begin(
        self, file: FileAsset, scope: AttachmentScope, *, content_sha256: str, chunker_version: str,
        embedding_provider: str, embedding_model: str, embedding_dimensions: int,
    ) -> DocumentIngestion:
        now = datetime.now(timezone.utc)
        profile = self._profile(
            embedding_provider, embedding_model, embedding_dimensions, create=True
        )
        assert profile is not None
        source = self._source("attachment", file.id)
        if source is None:
            source = KnowledgeSource(
                id=str(uuid.uuid4()),
                workspace_id=scope.workspace_id,
                team_id=scope.team_id,
                source_type="attachment",
                source_id=file.id,
                project_id=scope.project_id,
                issue_id=scope.issue_id,
                source_version=1,
                content_hash=content_sha256,
                renderer_version=None,
                chunker_version=chunker_version,
                active_embedding_profile_id=profile.id,
                status="parsing",
            )
            self.db.add(source)
            self.db.flush()
        else:
            source.workspace_id = scope.workspace_id
            source.team_id = scope.team_id
            source.project_id = scope.project_id
            source.issue_id = scope.issue_id
            source.content_hash = content_sha256
            source.chunker_version = chunker_version
            source.active_embedding_profile_id = profile.id
            source.status = "parsing"
            source.error_code = None
            source.error_message = None
            source.deleted_at = None
        ingestion = self.ingestion(file.id)
        if ingestion is None:
            ingestion = DocumentIngestion(
                id=str(uuid.uuid4()),
                file_asset_id=file.id,
                workspace_id=file.workspace_id,
                knowledge_source_id=source.id,
                content_sha256=content_sha256,
                chunker_version=chunker_version,
                embedding_profile_id=profile.id,
            )
            self.db.add(ingestion)
        ingestion.status = "parsing"
        ingestion.content_sha256 = content_sha256
        ingestion.chunker_version = chunker_version
        ingestion.knowledge_source_id = source.id
        ingestion.embedding_profile_id = profile.id
        ingestion.attempts = (ingestion.attempts or 0) + 1
        ingestion.error_code = None
        ingestion.error_message = None
        ingestion.started_at = now
        ingestion.completed_at = None
        ingestion.deleted_at = None
        self.db.commit()
        self.db.refresh(ingestion)
        return ingestion

    def complete_ingestion(
        self,
        ingestion_id: str,
        scope: AttachmentScope,
        document: ParsedDocument,
        chunks: list[ChunkDraft],
        embeddings: list[list[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Embedding count does not match chunk count")
        ingestion = self.db.get(DocumentIngestion, ingestion_id)
        if ingestion is None:
            raise ValueError("Ingestion disappeared while processing")
        ingestion.status = "chunking"
        ingestion.parser_name = document.parser_name
        ingestion.parser_version = document.parser_version
        source = self.db.get(KnowledgeSource, ingestion.knowledge_source_id)
        if source is None:
            raise ValueError("Knowledge source disappeared while processing")
        self._delete_nodes(source.id)
        self.db.execute(delete(DocumentPage).where(DocumentPage.ingestion_id == ingestion_id))
        page_ids: dict[int, str] = {}
        for page in document.pages:
            page_id = str(uuid.uuid4())
            page_ids[page.page_number] = page_id
            self.db.add(DocumentPage(
                id=page_id,
                ingestion_id=ingestion_id,
                page_number=page.page_number,
                heading_path=page.heading_path,
                markdown_content=page.markdown_content,
                plain_text=page.plain_text,
                token_count=count_tokens(page.plain_text),
            ))
        # The models intentionally do not expose ORM relationships. Flush the
        # parent rows explicitly so PostgreSQL never sees a chunk first.
        self.db.flush()
        ingestion.status = "embedding"
        source.status = "embedding"
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            node_id = str(uuid.uuid4())
            content_hash = sha256(chunk.content.encode()).hexdigest()
            self.db.add(KnowledgeNode(
                id=node_id,
                knowledge_source_id=source.id,
                parent_node_id=None,
                document_page_id=page_ids[chunk.page_number],
                workspace_id=scope.workspace_id,
                team_id=scope.team_id,
                issue_id=scope.issue_id,
                project_id=scope.project_id,
                node_key=f"chunk:{chunk.chunk_index}",
                node_kind="chunk",
                modality="text",
                indexable=True,
                ordinal=chunk.chunk_index,
                content=chunk.content,
                content_hash=content_hash,
                token_count=chunk.token_count,
                page_from=chunk.page_number,
                page_to=chunk.page_number,
                heading_path=chunk.heading_path,
                node_metadata=chunk.metadata,
            ))
            self.db.add(KnowledgeEmbedding(
                knowledge_node_id=node_id,
                embedding_profile_id=ingestion.embedding_profile_id,
                content_hash=content_hash,
                embedding=embedding,
            ))
        ingestion.status = "ready"
        ingestion.completed_at = datetime.now(timezone.utc)
        source.status = "ready"
        source.indexed_at = ingestion.completed_at
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def delete_ingestion(self, file_id: str) -> None:
        ingestion = self.ingestion(file_id)
        if ingestion is None:
            return
        self._delete_nodes(ingestion.knowledge_source_id)
        self.db.execute(delete(DocumentPage).where(DocumentPage.ingestion_id == ingestion.id))
        source = self.db.get(KnowledgeSource, ingestion.knowledge_source_id)
        if source is not None:
            source.status = "deleted"
            source.deleted_at = datetime.now(timezone.utc)
        ingestion.status = "deleted"
        ingestion.deleted_at = datetime.now(timezone.utc)
        ingestion.completed_at = datetime.now(timezone.utc)
        self.db.commit()

    def mark_ingestion_failed(self, file_id: str, code: str, message: str) -> None:
        ingestion = self.ingestion(file_id)
        if ingestion is not None:
            ingestion.status = "failed"
            ingestion.error_code = code[:80]
            ingestion.error_message = message[:4000]
            ingestion.completed_at = datetime.now(timezone.utc)
            source = self.db.get(KnowledgeSource, ingestion.knowledge_source_id)
            if source is not None:
                source.status = "failed"
                source.error_code = code[:80]
                source.error_message = message[:4000]
            self.db.commit()

    def counts(self, ingestion_id: str) -> tuple[int, int]:
        ingestion = self.db.get(DocumentIngestion, ingestion_id)
        if ingestion is None:
            return 0, 0
        pages = len(list(self.db.scalars(
            select(DocumentPage.id).where(DocumentPage.ingestion_id == ingestion_id)
        )))
        chunks = len(list(self.db.scalars(
            select(KnowledgeNode.id).where(
                KnowledgeNode.knowledge_source_id == ingestion.knowledge_source_id,
                KnowledgeNode.indexable.is_(True),
            )
        )))
        return pages, chunks

    def _source(self, source_type: str, source_id: str) -> KnowledgeSource | None:
        return self.db.scalar(select(KnowledgeSource).where(
            KnowledgeSource.source_type == source_type,
            KnowledgeSource.source_id == source_id,
        ))

    def _profile(
        self, provider: str, model: str, dimensions: int, *, create: bool
    ) -> EmbeddingProfile | None:
        key = embedding_profile_key(provider, model, dimensions)
        profile = self.db.scalar(
            select(EmbeddingProfile).where(EmbeddingProfile.profile_key == key)
        )
        if profile is None and create:
            profile = EmbeddingProfile(
                id=str(uuid.uuid4()), profile_key=key, provider=provider,
                model=model, dimensions=dimensions, distance_metric="cosine", normalized=True,
            )
            self.db.add(profile)
            self.db.flush()
        return profile

    def _delete_nodes(self, source_id: str) -> None:
        node_ids = list(self.db.scalars(
            select(KnowledgeNode.id).where(KnowledgeNode.knowledge_source_id == source_id)
        ))
        if node_ids:
            self.db.execute(delete(KnowledgeEmbedding).where(
                KnowledgeEmbedding.knowledge_node_id.in_(node_ids)
            ))
            self.db.execute(delete(KnowledgeNode).where(KnowledgeNode.id.in_(node_ids)))
