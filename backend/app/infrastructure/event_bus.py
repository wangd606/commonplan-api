import json
from typing import Protocol

from app.config import settings
from app.ingestion.knowledge_types import KnowledgeEvent


class EventDelivery(Protocol):
    event: KnowledgeEvent
    def acknowledge(self) -> None: ...
    def dead_letter(self, error: Exception) -> None: ...


class KafkaDelivery:
    def __init__(self, consumer, producer, message, event: KnowledgeEvent):
        self.consumer = consumer
        self.producer = producer
        self.message = message
        self.event = event

    def acknowledge(self) -> None:
        self.consumer.commit(message=self.message, asynchronous=False)

    def dead_letter(self, error: Exception) -> None:
        envelope = {
            "event": _decode_payload(self.message.value()),
            "error": str(error),
            "source_topic": self.message.topic(),
            "source_partition": self.message.partition(),
            "source_offset": self.message.offset(),
        }
        self.producer.produce(
            settings.event_bus_dlq_topic,
            key=self.message.key(),
            value=json.dumps(envelope).encode(),
        )
        if self.producer.flush(10):
            raise RuntimeError("Dead-letter event was not acknowledged by the broker")
        self.acknowledge()


class KafkaEventBus:
    def __init__(self):
        try:
            from confluent_kafka import Consumer, Producer
        except ImportError as exc:
            raise RuntimeError("confluent-kafka is required by the ingestion worker") from exc
        common = {
            "bootstrap.servers": settings.event_bus_bootstrap_servers,
            "security.protocol": settings.event_bus_security_protocol,
        }
        if settings.event_bus_sasl_username:
            common.update({
                "sasl.mechanism": settings.event_bus_sasl_mechanism,
                "sasl.username": settings.event_bus_sasl_username,
                "sasl.password": settings.event_bus_sasl_password,
            })
        self.consumer = Consumer({
            **common,
            "group.id": settings.event_bus_consumer_group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            # Docling and local CPU embeddings can legitimately take several
            # minutes. Keep ownership while a single record is processed.
            "max.poll.interval.ms": settings.event_bus_max_poll_interval_seconds * 1000,
        })
        self.producer = Producer(common)
        self.consumer.subscribe([settings.event_bus_topic])

    def receive(self, timeout: float = 1.0) -> KafkaDelivery | None:
        message = self.consumer.poll(timeout)
        if message is None:
            return None
        if message.error():
            raise RuntimeError(str(message.error()))
        payload = _decode_payload(message.value())
        event = KnowledgeEvent(
            event_id=str(payload.get("event_id") or ""),
            workspace_id=str(payload["workspace_id"]),
            source_type=str(payload["source_type"]),
            source_id=str(payload["source_id"]),
            source_version=int(payload.get("source_version", 1)),
            operation=str(payload.get("operation", "upsert")),
            metadata=dict(payload.get("metadata") or {}),
        )
        return KafkaDelivery(self.consumer, self.producer, message, event)

    def close(self) -> None:
        self.consumer.close()


def _decode_payload(value: bytes | str | dict) -> dict:
    payload = json.loads(value) if isinstance(value, (bytes, str)) else value
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("Knowledge event payload must be a JSON object")
    return payload
