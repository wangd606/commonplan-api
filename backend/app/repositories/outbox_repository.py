from typing import Annotated, Protocol

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import DbSession
from app.models import OutboxEvent


class OutboxRepository(Protocol):
    def append(
        self, *, event_id: str, workspace_id: str, aggregate_type: str,
        aggregate_id: str, event_type: str, partition_key: str, payload: dict,
    ) -> None: ...


class SqlAlchemyOutboxRepository:
    def __init__(self, db: Session):
        self.db = db

    def append(
        self, *, event_id: str, workspace_id: str, aggregate_type: str,
        aggregate_id: str, event_type: str, partition_key: str, payload: dict,
    ) -> None:
        self.db.add(OutboxEvent(
            id=event_id,
            workspace_id=workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            schema_version=1,
            partition_key=partition_key,
        ))


def get_outbox_repository(db: DbSession) -> OutboxRepository:
    return SqlAlchemyOutboxRepository(db)


OutboxRepositoryDep = Annotated[OutboxRepository, Depends(get_outbox_repository)]
