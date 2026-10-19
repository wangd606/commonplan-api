import json

from app.events import DomainEvent, OutboxEventPublisher
from app.infrastructure.event_bus import _decode_payload
from app.ingestion.knowledge_types import SourceMaterial
from app.ingestion.renderers import RENDERER_VERSION, render_material


class RecordingOutbox:
    def __init__(self):
        self.rows = []

    def append(self, **values):
        self.rows.append(values)


def test_outbox_publisher_persists_reference_event_without_calling_a_broker():
    repository = RecordingOutbox()
    publisher = OutboxEventPublisher(repository)

    publisher.publish(DomainEvent(
        workspace_id="workspace-1",
        source_type="issue",
        source_id="issue-1",
        source_version=7,
        metadata={"team_id": "team-1"},
    ))

    assert len(repository.rows) == 1
    row = repository.rows[0]
    assert row["event_type"] == "knowledge.source.upserted"
    assert row["partition_key"] == "workspace-1:issue:issue-1"
    assert row["payload"]["source_version"] == 7
    assert row["payload"]["metadata"] == {"team_id": "team-1"}


def test_delete_event_uses_tombstone_operation():
    event = DomainEvent(
        workspace_id="workspace-1", source_type="issue_comment",
        source_id="comment-1", operation="delete",
    )
    assert event.event_type == "knowledge.source.deleted"


def test_database_renderer_is_deterministic_and_preserves_context():
    material = SourceMaterial(
        workspace_id="workspace-1", team_id="team-1", source_type="issue",
        source_id="issue-1", source_version=3, title="TA-1: Fix login",
        sections=(("Description", "Handle OAuth cancellation"), ("Labels", "auth, bug")),
        metadata={"priority": 2}, issue_id="issue-1",
    )

    first = render_material(material)
    second = render_material(material)

    assert first == second
    assert first.parser_version == RENDERER_VERSION
    assert first.pages[0].markdown_content == (
        "# TA-1: Fix login\n\n## Description\n\nHandle OAuth cancellation"
        "\n\n## Labels\n\nauth, bug"
    )


def test_debezium_payload_decoder_accepts_expanded_or_string_json():
    payload = {"source_type": "project", "source_id": "project-1"}
    assert _decode_payload(json.dumps(payload).encode()) == payload
    assert _decode_payload(json.dumps(json.dumps(payload))) == payload
