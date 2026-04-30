from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Plan


@dataclass(frozen=True)
class ToolSpec:
    name: str
    required_args: tuple[str, ...]
    description: str


TOOL_SPECS: dict[str, ToolSpec] = {
    "contacts.search_by_tag": ToolSpec(
        "contacts.search_by_tag",
        ("tag",),
        "Find contacts with a specific tag.",
    ),
    "contacts.search_by_attribute": ToolSpec(
        "contacts.search_by_attribute",
        ("name", "value"),
        "Find contacts whose custom attribute equals a value.",
    ),
    "tags.add": ToolSpec(
        "tags.add",
        ("whatsappNumber", "tag"),
        "Add a tag to one contact.",
    ),
    "messages.send_template": ToolSpec(
        "messages.send_template",
        ("whatsappNumber", "template_name"),
        "Send one template message to one contact.",
    ),
    "messages.send_template_batch": ToolSpec(
        "messages.send_template_batch",
        ("contacts_from_step", "template_name"),
        "Send one template message to contacts returned by an earlier step.",
    ),
    "broadcasts.send_to_segment": ToolSpec(
        "broadcasts.send_to_segment",
        ("template_name", "broadcast_name", "segmentName"),
        "Send a template broadcast to a segment.",
    ),
    "tickets.assign_team": ToolSpec(
        "tickets.assign_team",
        ("whatsappNumber", "teamName"),
        "Assign a contact conversation or ticket to a team.",
    ),
}


def tool_schema_for_prompt() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "required_args": list(spec.required_args),
            "description": spec.description,
        }
        for spec in TOOL_SPECS.values()
    ]


def validate_plan(plan: Plan) -> list[str]:
    errors: list[str] = []
    if plan.is_clarification:
        return errors
    if not plan.steps:
        errors.append("Plan has no steps")
        return errors
    for index, step in enumerate(plan.steps, start=1):
        spec = TOOL_SPECS.get(step.tool)
        if not spec:
            errors.append(f"Step {index}: unknown tool {step.tool}")
            continue
        for arg in spec.required_args:
            if arg not in step.args or step.args[arg] in (None, ""):
                errors.append(f"Step {index}: missing required arg {arg}")
        ref = step.args.get("contacts_from_step")
        if ref is not None:
            if not isinstance(ref, int):
                errors.append(f"Step {index}: contacts_from_step must be an integer")
            elif ref < 1 or ref >= index:
                errors.append(
                    f"Step {index}: contacts_from_step must reference an earlier step"
                )
    return errors
