from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class PlanStep:
    tool: str
    args: JsonDict


@dataclass(frozen=True)
class Plan:
    summary: str
    steps: list[PlanStep]
    requires_confirmation: bool = True
    clarification: str | None = None

    @property
    def is_clarification(self) -> bool:
        return bool(self.clarification)


@dataclass(frozen=True)
class RequestRecord:
    method: str
    endpoint: str
    body: JsonDict | None = None


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    requests: list[RequestRecord] = field(default_factory=list)
    error: str | None = None


@dataclass
class StepResult:
    index: int
    tool: str
    ok: bool
    requests: list[RequestRecord] = field(default_factory=list)
    output: Any = None
    error: str | None = None


@dataclass
class ExecutionReport:
    plan: Plan
    dry_run: bool
    step_results: list[StepResult]

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.step_results)
