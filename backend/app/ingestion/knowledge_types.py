from dataclasses import dataclass, field


@dataclass(frozen=True)
class KnowledgeEvent:
    event_id: str
    workspace_id: str
    source_type: str
    source_id: str
    source_version: int
    operation: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SourceMaterial:
    workspace_id: str
    team_id: str
    source_type: str
    source_id: str
    source_version: int
    title: str
    sections: tuple[tuple[str, str], ...]
    metadata: dict
    project_id: str | None = None
    issue_id: str | None = None
