import argparse
from functools import lru_cache
import logging
import time

from app.config import settings
from app.database import SessionLocal
from app.ingestion.embeddings import create_embedding_provider
from app.ingestion.knowledge_service import KnowledgeIngestionService
from app.ingestion.parsers import ParserRegistry
from app.ingestion.service import IngestionError, IngestionService
from app.infrastructure.event_bus import KafkaEventBus
from app.infrastructure.object_storage import get_object_storage
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.knowledge_repository import KnowledgeRepository


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("commonplan.ingestion")


@lru_cache(maxsize=1)
def parser_registry() -> ParserRegistry:
    return ParserRegistry()


@lru_cache(maxsize=1)
def embedding_provider():
    return create_embedding_provider()


def process_event(event) -> None:
    with SessionLocal() as db:
        try:
            if event.source_type == "attachment":
                IngestionService(
                    IngestionRepository(db), get_object_storage(),
                    parser_registry(), embedding_provider(),
                ).process(event)
            else:
                KnowledgeIngestionService(
                    KnowledgeRepository(db), embedding_provider()
                ).process(event)
        except Exception as exc:
            if event.source_type == "attachment":
                repository = IngestionRepository(db)
                repository.rollback()
                code = exc.code if isinstance(exc, IngestionError) else exc.__class__.__name__
                message = (
                    str(exc) if isinstance(exc, IngestionError)
                    else "Document processing failed; retry or contact support"
                )
                repository.mark_ingestion_failed(
                    event.source_id, code, message,
                )
            else:
                KnowledgeRepository(db).fail(event.source_type, event.source_id, exc)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Consume CommonPlan knowledge events")
    parser.add_argument("--once", action="store_true", help="process at most one event")
    args = parser.parse_args()
    bus = KafkaEventBus()
    try:
        while True:
            delivery = bus.receive(settings.event_bus_poll_timeout_seconds)
            if delivery is None:
                if args.once:
                    return
                continue
            for attempt in range(1, settings.ingestion_max_attempts + 1):
                try:
                    process_event(delivery.event)
                    delivery.acknowledge()
                    logger.info(
                        "indexed source=%s/%s event=%s",
                        delivery.event.source_type, delivery.event.source_id,
                        delivery.event.event_id,
                    )
                    break
                except Exception as exc:
                    logger.exception(
                        "indexing attempt=%s failed source=%s/%s",
                        attempt, delivery.event.source_type, delivery.event.source_id,
                    )
                    terminal = isinstance(exc, IngestionError) and not exc.retryable
                    if terminal or attempt == settings.ingestion_max_attempts:
                        delivery.dead_letter(exc)
                        break
                    else:
                        time.sleep(min(30, 2 ** (attempt - 1)))
            if args.once:
                return
    finally:
        bus.close()


if __name__ == "__main__":
    main()
