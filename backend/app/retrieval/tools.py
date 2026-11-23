from dataclasses import dataclass
from typing import Protocol

from app.retrieval.workflow import (
    HybridRetrievalRuntime,
    run_hybrid_retrieval,
)


@dataclass(frozen=True)
class ToolRequest:
    query: str


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    query: str
    retrieval: dict
    steps: list[dict]


class RetrievalTool(Protocol):
    """Small adapter boundary for an evidence source used by AnswerGraph."""

    name: str
    description: str

    def search(self, request: ToolRequest) -> ToolResult: ...


class InternalKnowledgeTool:
    name = "internal_knowledge"
    description = (
        "Search authorized CommonPlan documents, projects, issues, and comments "
        "with hybrid lexical and semantic retrieval."
    )

    def __init__(self, runtime: HybridRetrievalRuntime) -> None:
        self.runtime = runtime

    def search(self, request: ToolRequest) -> ToolResult:
        result = run_hybrid_retrieval(request.query, self.runtime)
        return ToolResult(
            tool_name=self.name,
            query=request.query,
            retrieval=result.retrieval,
            steps=result.steps,
        )


class ToolRegistry:
    """Explicit allow-list of retrieval tools available to a graph run."""

    def __init__(self, tools: list[RetrievalTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("Retrieval tool names must be unique")

    def descriptions(self) -> list[dict[str, str]]:
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
        ]

    def resolve(self, names: tuple[str, ...]) -> list[RetrievalTool]:
        unknown = set(names) - set(self._tools)
        if unknown:
            raise ValueError(f"Planner selected unavailable tools: {sorted(unknown)}")
        return [self._tools[name] for name in names]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)
