from hashlib import sha256

from app.config import settings
from app.ingestion.chunking import chunk_document
from app.ingestion.embeddings import EmbeddingProvider
from app.ingestion.knowledge_types import KnowledgeEvent
from app.ingestion.renderers import RENDERER_VERSION, render_material
from app.repositories.knowledge_repository import KnowledgeRepository


class KnowledgeIngestionService:
    def __init__(self, repository: KnowledgeRepository, embeddings: EmbeddingProvider):
        self.repository = repository
        self.embeddings = embeddings

    def process(self, event: KnowledgeEvent) -> None:
        if event.operation == "delete":
            self.repository.delete_source(event.source_type, event.source_id)
            return
        material = self.repository.load(event.source_type, event.source_id)
        if material is None:
            self.repository.delete_source(event.source_type, event.source_id)
            return
        document = render_material(material)
        digest = sha256(document.pages[0].markdown_content.encode()).hexdigest()
        chunker_version = "knowledge-word-window-v1"
        if self.repository.is_current(
            material,
            content_hash=digest,
            renderer_version=RENDERER_VERSION,
            chunker_version=chunker_version,
            embedding_provider=self.embeddings.provider,
            embedding_model=self.embeddings.model,
            embedding_dimensions=self.embeddings.dimensions,
        ):
            return
        self.repository.begin(
            material,
            content_hash=digest,
            renderer_version=RENDERER_VERSION,
            chunker_version=chunker_version,
            embedding_provider=self.embeddings.provider,
            embedding_model=self.embeddings.model,
            embedding_dimensions=self.embeddings.dimensions,
        )
        chunks = chunk_document(
            document,
            chunk_tokens=settings.ingestion_chunk_tokens,
            overlap_tokens=settings.ingestion_chunk_overlap_tokens,
        )
        vectors = self.embeddings.embed([chunk.content for chunk in chunks])
        self.repository.replace(
            material,
            content_hash=digest,
            renderer_version=RENDERER_VERSION,
            chunker_version=chunker_version,
            embedding_provider=self.embeddings.provider,
            embedding_model=self.embeddings.model,
            embedding_dimensions=self.embeddings.dimensions,
            chunks=chunks,
            embeddings=vectors,
        )
