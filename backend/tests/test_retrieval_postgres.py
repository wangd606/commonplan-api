import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import (
    EmbeddingProfile,
    KnowledgeEmbedding,
    KnowledgeNode,
    KnowledgeSource,
    Project,
    Team,
    User,
    Workspace,
)
from app.repositories.retrieval_repository import SqlAlchemyRetrievalRepository
from app.retrieval.types import RetrievalScope


DATABASE_URL = os.getenv("TEST_DATABASE_URL")
CANONICAL_PROFILE_KEY = (
    "model_gateway:commonplan-embedding-v1:Qwen/Qwen3-Embedding-0.6B@"
    "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3:1024"
)
BACKEND_DIR = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL retrieval integration tests",
)


def _alembic_config():
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    return config


def test_retrieval_migration_round_trip_and_catalog_contract():
    """Exercise the real migration, not only its source text."""
    from alembic import command
    from app.config import settings

    original_database_url = settings.database_url
    settings.database_url = DATABASE_URL
    config = _alembic_config()
    engine = create_engine(DATABASE_URL)
    try:
        command.downgrade(config, "20261025_0022")
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'knowledge_embeddings' "
                "AND column_name = 'embedding_profile_key'"
            )).scalar_one_or_none() is None
            assert connection.execute(text(
                "SELECT 1 FROM pg_indexes "
                "WHERE indexname = 'ix_knowledge_embeddings_hnsw_qwen_v1_cosine'"
            )).scalar_one_or_none() is None

        command.upgrade(config, "head")
        with engine.connect() as connection:
            nullable = connection.execute(text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'knowledge_embeddings' "
                "AND column_name = 'embedding_profile_key'"
            )).scalar_one()
            index_definition = connection.execute(text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'ix_knowledge_embeddings_hnsw_qwen_v1_cosine'"
            )).scalar_one()
            assert nullable == "NO"
            assert "USING hnsw" in index_definition
            assert "vector_cosine_ops" in index_definition
            assert CANONICAL_PROFILE_KEY in index_definition
    finally:
        # Never leave a developer test database downgraded after an assertion fails.
        command.upgrade(config, "head")
        settings.database_url = original_database_url
        engine.dispose()


def _add_source(
    db: Session,
    *,
    source_id: str,
    team_id: str,
    project_id: str,
    profile: EmbeddingProfile,
    node_id: str,
    content: str,
    vector: list[float],
) -> None:
    source = KnowledgeSource(
        id=f"source-{source_id}",
        workspace_id="retrieval-workspace",
        team_id=team_id,
        source_type="project",
        source_id=source_id,
        project_id=project_id,
        source_version=1,
        content_hash=f"source-hash-{source_id}",
        renderer_version="test",
        chunker_version="test",
        active_embedding_profile_id=profile.id,
        status="ready",
    )
    db.add(source)
    db.flush()
    node = KnowledgeNode(
        id=node_id,
        knowledge_source_id=source.id,
        workspace_id="retrieval-workspace",
        team_id=team_id,
        project_id=project_id,
        node_key="chunk:0",
        node_kind="chunk",
        modality="text",
        indexable=True,
        ordinal=0,
        content=content,
        content_hash=f"hash-{node_id}",
        token_count=8,
        node_metadata={},
    )
    db.add(node)
    db.flush()
    db.add(KnowledgeEmbedding(
        knowledge_node_id=node.id,
        embedding_profile_id=profile.id,
        embedding_profile_key=profile.profile_key,
        content_hash=node.content_hash,
        embedding=vector,
    ))


def test_postgres_search_rejects_other_teams_and_embedding_spaces():
    engine = create_engine(DATABASE_URL)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection) as db:
            db.add(User(
                id=991001,
                email="retrieval-test@example.com",
                name="Retrieval Test",
                auth_issuer="test",
                auth_subject="retrieval",
                is_deleted=False,
            ))
            db.flush()
            db.add(Workspace(
                id="retrieval-workspace",
                slug="retrieval-test",
                name="Retrieval",
                created_by_user_id=991001,
            ))
            db.flush()
            for team_id, prefix in (
                ("retrieval-team", "RT"),
                ("forbidden-team", "NO"),
            ):
                db.add(Team(
                    id=team_id,
                    workspace_id="retrieval-workspace",
                    name=team_id,
                    issue_prefix=prefix,
                    next_issue_number=1,
                ))
            db.flush()
            for project_id, team_id, slug in (
                ("retrieval-project", "retrieval-team", "search"),
                ("forbidden-project", "forbidden-team", "private"),
            ):
                db.add(Project(
                    id=project_id,
                    team_id=team_id,
                    name=project_id,
                    slug=slug,
                    status="planned",
                ))

            canonical_profile = EmbeddingProfile(
                id="retrieval-profile",
                profile_key=CANONICAL_PROFILE_KEY,
                provider="model_gateway",
                model="Qwen/Qwen3-Embedding-0.6B",
                dimensions=1024,
                distance_metric="cosine",
                normalized=True,
            )
            competing_profile = EmbeddingProfile(
                id="competing-profile",
                profile_key="test:competing-model:1024",
                provider="test",
                model="competing-model",
                dimensions=1024,
                distance_metric="cosine",
                normalized=True,
            )
            db.add_all((canonical_profile, competing_profile))
            db.flush()

            query_vector = [1.0] + [0.0] * 1023
            authorized_vector = [0.99, 0.01] + [0.0] * 1022
            _add_source(
                db,
                source_id="retrieval-project",
                team_id="retrieval-team",
                project_id="retrieval-project",
                profile=canonical_profile,
                node_id="authorized-node",
                content="TA-12 was delayed by an ONNX runtime failure",
                vector=authorized_vector,
            )
            db.add(KnowledgeNode(
                id="authorized-neighbor",
                knowledge_source_id="source-retrieval-project",
                workspace_id="retrieval-workspace",
                team_id="retrieval-team",
                project_id="retrieval-project",
                node_key="chunk:1",
                node_kind="chunk",
                modality="text",
                indexable=True,
                ordinal=1,
                content="The next chunk records the remediation and rollout plan",
                content_hash="hash-authorized-neighbor",
                token_count=9,
                node_metadata={},
            ))
            # This is a better vector/lexical match but belongs to a team outside
            # the caller scope and must never leak into either result set.
            _add_source(
                db,
                source_id="forbidden-project",
                team_id="forbidden-team",
                project_id="forbidden-project",
                profile=canonical_profile,
                node_id="forbidden-node",
                content="TA-12 ONNX private incident details",
                vector=query_vector,
            )
            # Same dimensions and authorized team, but a different embedding
            # space. It must not compete with the selected canonical profile.
            _add_source(
                db,
                source_id="competing-source",
                team_id="retrieval-team",
                project_id="retrieval-project",
                profile=competing_profile,
                node_id="competing-node",
                content="Unrelated product planning notes",
                vector=query_vector,
            )
            db.flush()

            repository = SqlAlchemyRetrievalRepository(db)
            scope = RetrievalScope("retrieval-workspace", ("retrieval-team",))
            vector_hits = repository.vector_search(
                query_vector, canonical_profile.profile_key, scope, 5,
            )
            lexical_hits = repository.lexical_search("TA-12 ONNX", scope, 5)
            contexts = repository.expand_context(
                ["authorized-node", "forbidden-node"], scope, 1, True,
            )

            assert [hit.node_id for hit in vector_hits] == ["authorized-node"]
            assert [hit.node_id for hit in lexical_hits] == ["authorized-node"]
            assert 0.0 <= lexical_hits[0].score <= 1.0
            assert vector_hits[0].citation_target == "project:retrieval-project"
            assert [row.passage_id for row in contexts["authorized-node"]] == [
                "authorized-neighbor"
            ]
            assert "forbidden-node" not in contexts
            assert db.execute(text(
                "SELECT current_setting('hnsw.iterative_scan')"
            )).scalar_one() == "strict_order"
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
