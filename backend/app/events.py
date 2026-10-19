from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Protocol
import uuid

from fastapi import Depends

from app.repositories.outbox_repository import OutboxRepository, OutboxRepositoryDep


@dataclass(frozen=True)
class DomainEvent:
    workspace_id: str
    source_type: str
    source_id: str
    operation: str = "upsert"
    source_version: int = 1
    metadata: dict = field(default_factory=dict)

    @property
    def event_type(self) -> str:
        return {
            "upsert": "knowledge.source.upserted",
            "delete": "knowledge.source.deleted",
        }[self.operation]

    @property
    def partition_key(self) -> str:
        return f"{self.workspace_id}:{self.source_type}:{self.source_id}"


class EventPublisher(Protocol):
    def publish(self, event: DomainEvent) -> None: ...


class OutboxEventPublisher:
    """Records events in the caller's database transaction; it never calls Kafka."""

    def __init__(self, repository: OutboxRepository):
        self.repository = repository

    def publish(self, event: DomainEvent) -> None:
        event_id = str(uuid.uuid4())
        occurred_at = datetime.now(timezone.utc).isoformat()
        self.repository.append(
            event_id=event_id,
            workspace_id=event.workspace_id,
            aggregate_type=event.source_type,
            aggregate_id=event.source_id,
            event_type=event.event_type,
            partition_key=event.partition_key,
            payload={
                "event_id": event_id,
                "schema_version": 1,
                "event_type": event.event_type,
                "workspace_id": event.workspace_id,
                "source_type": event.source_type,
                "source_id": event.source_id,
                "source_version": event.source_version,
                "operation": event.operation,
                "occurred_at": occurred_at,
                "metadata": event.metadata,
            },
        )


def get_event_publisher(repository: OutboxRepositoryDep) -> EventPublisher:
    return OutboxEventPublisher(repository)


EventPublisherDep = Annotated[EventPublisher, Depends(get_event_publisher)]
