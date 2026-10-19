from hashlib import sha256

from app.config import settings
from app.ingestion.chunking import CHUNKER_VERSION, chunk_document
from app.ingestion.embeddings import EmbeddingProvider
from app.ingestion.parsers import ParserRegistry, UnsupportedDocumentType
from app.infrastructure.object_storage import ObjectStorage
from app.ingestion.knowledge_types import KnowledgeEvent
from app.repositories.ingestion_repository import IngestionRepository


class IngestionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class IngestionService:
    def __init__(
        self,
        repository: IngestionRepository,
        storage: ObjectStorage,
        parsers: ParserRegistry,
        embeddings: EmbeddingProvider,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.parsers = parsers
        self.embeddings = embeddings

    def process(self, event: KnowledgeEvent) -> None:
        file_id = event.source_id
        if event.operation == "delete":
            self.repository.delete_ingestion(file_id)
            return
        if event.source_type != "attachment":
            raise IngestionError(
                "unsupported_event", f"Unsupported source {event.source_type}", retryable=False
            )
        file = self.repository.file(file_id)
        if file is None:
            return
        if file.deleted_at is not None or file.upload_status == "deleted":
            self.repository.delete_ingestion(file_id)
            return
        if file.upload_status != "ready":
            raise IngestionError("file_not_ready", "File upload is not ready")
        if file.scan_status not in {"clean", "not_configured"}:
            raise IngestionError("scan_incomplete", f"File scan status is {file.scan_status}")
        if file.scan_status == "not_configured" and not settings.ingestion_allow_unscanned:
            raise IngestionError(
                "scan_required", "Ingestion requires a clean malware scan", retryable=False
            )
        scope = self.repository.scope(file_id)
        if scope is None:
            raise IngestionError(
                "missing_scope", "Attachment has no issue or project scope", retryable=False
            )
        content = self.storage.read(file.storage_key)
        digest = sha256(content).hexdigest()
        if file.sha256 and file.sha256 != digest:
            raise IngestionError(
                "checksum_mismatch", "Stored file checksum does not match upload", retryable=False
            )
        if self.repository.is_current(
            file.id,
            content_sha256=digest,
            chunker_version=CHUNKER_VERSION,
            embedding_provider=self.embeddings.provider,
            embedding_model=self.embeddings.model,
            embedding_dimensions=self.embeddings.dimensions,
        ):
            return
        ingestion = self.repository.begin(
            file,
            content_sha256=digest,
            chunker_version=CHUNKER_VERSION,
            embedding_provider=self.embeddings.provider,
            embedding_model=self.embeddings.model,
            embedding_dimensions=self.embeddings.dimensions,
        )
        try:
            document = self.parsers.parse(file.content_type, content, file.original_filename)
            chunks = chunk_document(
                document,
                chunk_tokens=settings.ingestion_chunk_tokens,
                overlap_tokens=settings.ingestion_chunk_overlap_tokens,
            )
        except UnsupportedDocumentType as exc:
            raise IngestionError("unsupported_content_type", str(exc), retryable=False) from exc
        except ValueError as exc:
            raise IngestionError("unreadable_document", str(exc), retryable=False) from exc
        vectors = self.embeddings.embed([chunk.content for chunk in chunks])
        self.repository.complete_ingestion(ingestion.id, scope, document, chunks, vectors)
