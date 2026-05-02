from __future__ import annotations

from dataclasses import dataclass
import re
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
    "contacts.add": ToolSpec(
        "contacts.add",
        ("whatsappNumber", "name"),
        "Create a contact with optional custom attributes.",
    ),
    "contacts.update_attributes": ToolSpec(
        "contacts.update_attributes",
        ("whatsappNumber", "customParams"),
        "Update custom attributes for one contact.",
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
        errors.extend(_validate_arg_types(index, step.tool, step.args))
        ref = step.args.get("contacts_from_step")
        if ref is not None:
            errors.extend(_validate_step_reference(index, "contacts_from_step", ref))
        ref = step.args.get("audience_from_step")
        if ref is not None:
            errors.extend(_validate_step_reference(index, "audience_from_step", ref))
    return errors


def _validate_step_reference(index: int, name: str, ref: Any) -> list[str]:
    if not isinstance(ref, int):
        return [f"Step {index}: {name} must be an integer"]
    if ref < 1 or ref >= index:
        return [f"Step {index}: {name} must reference an earlier step"]
    return []


def _validate_arg_types(index: int, tool: str, args: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("whatsappNumber", "tag", "template_name", "broadcast_name", "segmentName", "teamName", "name", "value"):
        if key in args and not isinstance(args[key], str):
            errors.append(f"Step {index}: {key} must be a string")
    if "whatsappNumber" in args and isinstance(args["whatsappNumber"], str):
        if not re.fullmatch(r"\d{8,16}", args["whatsappNumber"]):
            errors.append(f"Step {index}: whatsappNumber must be 8-16 digits")
    if tool == "messages.send_template":
        parameters = args.get("parameters", [])
        if not isinstance(parameters, list):
            errors.append(f"Step {index}: parameters must be a list")
    if tool in {"contacts.add", "contacts.update_attributes"}:
        custom_params = args.get("customParams", [])
        if not isinstance(custom_params, list):
            errors.append(f"Step {index}: customParams must be a list")
        elif not all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("value"), str)
            for item in custom_params
        ):
            errors.append(f"Step {index}: customParams items must include string name and value")
    if tool == "messages.send_template_batch":
        mapping = args.get("parameter_mapping", {})
        if mapping is not None and not isinstance(mapping, dict):
            errors.append(f"Step {index}: parameter_mapping must be an object")
        elif isinstance(mapping, dict):
            for name, expression in mapping.items():
                if not isinstance(name, str) or not isinstance(expression, str):
                    errors.append(f"Step {index}: parameter_mapping keys and values must be strings")
                    break
    if tool == "broadcasts.send_to_segment":
        segment_name = args.get("segmentName")
        if isinstance(segment_name, str) and "=" not in segment_name:
            errors.append(f"Step {index}: segmentName should use name=value format")
    return errors
