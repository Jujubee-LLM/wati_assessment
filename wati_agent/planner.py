from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .config import load_dotenv
from .models import JsonDict, Plan, PlanStep
from .tools import tool_schema_for_prompt


class PlannerError(Exception):
    pass


def make_planner(provider: str = "auto") -> "Planner":
    load_dotenv()

    provider = provider.lower()
    if provider == "fallback":
        return FallbackPlanner()
    if provider in {"auto", "qwen"}:
        if not os.getenv("DASHSCOPE_API_KEY"):
            raise PlannerError(
                "DASHSCOPE_API_KEY must be set for the default LLM planner. "
                "Use --provider fallback only for offline deterministic demos."
            )
        return QwenPlanner()
    return FallbackPlanner()


class Planner:
    def plan(self, instruction: str) -> Plan:
        raise NotImplementedError


class QwenPlanner(Planner):
    def __init__(self, fallback: Planner | None = None) -> None:
        self.api_key = os.environ["DASHSCOPE_API_KEY"]
        self.model = os.getenv("QWEN_MODEL", "qwen-plus")
        self.endpoint = os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        self.timeout_seconds = _int_env("QWEN_TIMEOUT_SECONDS", default=90, minimum=1)
        self.max_retries = _int_env("QWEN_MAX_RETRIES", default=2, minimum=1)
        self.fallback = fallback

    def plan(self, instruction: str) -> Plan:
        try:
            return self._plan_with_qwen(instruction)
        except Exception as exc:
            if self.fallback:
                return self.fallback.plan(instruction)
            raise PlannerError(f"Qwen planner failed: {exc}") from exc

    def _plan_with_qwen(self, instruction: str) -> Plan:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(),
                },
                {"role": "user", "content": instruction},
            ],
        }
        raw = self._post_chat_completion(payload)

        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
        return normalize_plan(instruction, plan_from_dict(_json_from_text(content)))

    def _post_chat_completion(self, payload: JsonDict) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            request = urllib.request.Request(
                self.endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    return response.read().decode("utf-8")
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1)
        raise PlannerError(
            f"{last_error} after {self.max_retries} attempt(s); "
            "set QWEN_TIMEOUT_SECONDS to a larger value if the model is slow"
        )

    def _system_prompt(self) -> str:
        return (
            "You are a planner for a WATI WhatsApp automation agent. "
            "Convert the user's natural-language instruction into strict JSON only. "
            "Do not call APIs. Use only the provided tools. "
            "If required information is missing, return a clarification instead of guessing.\n\n"
            "Important workflow rules:\n"
            "- If the user says 'escalate <whatsappNumber>', create exactly two steps: "
            "tickets.assign_team with teamName='Support', then tags.add with tag='escalated'.\n"
            "- Use teamName='Support', not 'Support Team'.\n"
            "- For VIP renewal, first use contacts.search_by_tag with tag='VIP', then "
            "messages.send_template_batch with template_name='renewal_reminder'.\n"
            "- For flash_sale Jakarta campaign, use broadcasts.send_to_segment with "
            "template_name='flash_sale' and segmentName='city=Jakarta' after a "
            "contacts.search_by_attribute step for name='city' and value='Jakarta'.\n"
            "- For create/update contact or add tag requests, return a concrete plan "
            "with contacts.add, contacts.update_attributes, and/or tags.add, or ask "
            "for missing fields.\n\n"
            "Allowed tools:\n"
            f"{json.dumps(tool_schema_for_prompt(), ensure_ascii=False, indent=2)}\n\n"
            "Return one of these JSON shapes:\n"
            "{"
            '"summary":"...", "requires_confirmation":true, '
            '"steps":[{"tool":"tool.name","args":{}}]'
            "}\n"
            "or\n"
            '{"summary":"Need clarification","clarification":"question to ask","steps":[]}'
        )


class FallbackPlanner(Planner):
    def plan(self, instruction: str) -> Plan:
        normalized = instruction.strip()
        lowered = normalized.lower()

        if "escalate" in lowered:
            number = self._extract_number(normalized)
            if not number:
                return Plan(
                    summary="Need contact number",
                    steps=[],
                    clarification="Which WhatsApp number should be escalated?",
                )
            return Plan(
                summary=f"Escalate {number} to Support and add escalated tag",
                requires_confirmation=True,
                steps=[
                    PlanStep(
                        "tickets.assign_team",
                        {"whatsappNumber": number, "teamName": "Support"},
                    ),
                    PlanStep(
                        "tags.add",
                        {"whatsappNumber": number, "tag": "escalated"},
                    ),
                ],
            )

        if "vip" in lowered and "renewal_reminder" in lowered:
            return Plan(
                summary="Send renewal_reminder template to VIP contacts",
                requires_confirmation=True,
                steps=[
                    PlanStep("contacts.search_by_tag", {"tag": "VIP"}),
                    PlanStep(
                        "messages.send_template_batch",
                        {
                            "contacts_from_step": 1,
                            "template_name": "renewal_reminder",
                            "broadcast_name": "renewal_reminder_vip",
                            "parameter_mapping": {"body_1": "contact.name"},
                        },
                    ),
                ],
            )

        if "flash_sale" in lowered and ("jakarta" in lowered or "city" in lowered):
            city = self._extract_city(normalized) or "Jakarta"
            return Plan(
                summary=f"Verify city={city} audience and send flash_sale broadcast",
                requires_confirmation=True,
                steps=[
                    PlanStep(
                        "contacts.search_by_attribute",
                        {"name": "city", "value": city},
                    ),
                    PlanStep(
                        "broadcasts.send_to_segment",
                        {
                            "template_name": "flash_sale",
                            "broadcast_name": f"flash_sale_{city.lower()}",
                            "segmentName": f"city={city}",
                            "audience_from_step": 1,
                        },
                    ),
                ],
            )

        if ("create" in lowered or "add contact" in lowered or "new contact" in lowered) and "contact" in lowered:
            number = self._extract_number(normalized)
            name = self._extract_name(normalized)
            custom_params = self._extract_custom_params(normalized)
            tag = self._extract_tag(normalized)
            if number and name:
                steps = [
                    PlanStep(
                        "contacts.add",
                        {
                            "whatsappNumber": number,
                            "name": name,
                            "customParams": custom_params,
                        },
                    )
                ]
                if tag:
                    steps.append(
                        PlanStep(
                            "tags.add",
                            {"whatsappNumber": number, "tag": tag},
                        )
                    )
                return Plan(
                    summary=f"Create contact {name} and apply requested attributes",
                    requires_confirmation=True,
                    steps=steps,
                )
            return Plan(
                summary="Need contact details",
                steps=[],
                clarification="What WhatsApp number and contact name should be used?",
            )

        if "update" in lowered and ("attribute" in lowered or "city" in lowered):
            number = self._extract_number(normalized)
            custom_params = self._extract_custom_params(normalized)
            if number and custom_params:
                return Plan(
                    summary=f"Update attributes for {number}",
                    requires_confirmation=True,
                    steps=[
                        PlanStep(
                            "contacts.update_attributes",
                            {
                                "whatsappNumber": number,
                                "customParams": custom_params,
                            },
                        )
                    ],
                )
            return Plan(
                summary="Need attribute details",
                steps=[],
                clarification="Which WhatsApp number and attributes should be updated?",
            )

        if "tag" in lowered and ("add" in lowered or "tag" in lowered):
            number = self._extract_number(normalized)
            tag = self._extract_tag(normalized)
            if number and tag:
                return Plan(
                    summary=f"Add {tag} tag to {number}",
                    requires_confirmation=True,
                    steps=[
                        PlanStep(
                            "tags.add",
                            {"whatsappNumber": number, "tag": tag},
                        )
                    ],
                )
            if "contact" in lowered:
                return Plan(
                    summary="Need tag details",
                    steps=[],
                    clarification="Which WhatsApp number and tag should be used?",
                )

        if ("send" in lowered or "message" in lowered) and self._extract_number(normalized):
            number = self._extract_number(normalized)
            template = self._extract_template(normalized)
            if template and number:
                return Plan(
                    summary=f"Send {template} template to {number}",
                    requires_confirmation=True,
                    steps=[
                        PlanStep(
                            "messages.send_template",
                            {
                                "whatsappNumber": number,
                                "template_name": template,
                                "broadcast_name": f"{template}_direct",
                            },
                        )
                    ],
                )

        if "send" in lowered and "template" in lowered:
            return Plan(
                summary="Need template and audience details",
                steps=[],
                clarification=(
                    "Which template should be sent, and who should receive it?"
                ),
            )

        return Plan(
            summary="Unsupported instruction",
            steps=[],
            clarification=(
                "I can help with VIP renewal, escalation, or city broadcast workflows. "
                "Please provide one of those instructions."
            ),
        )

    def _extract_number(self, text: str) -> str | None:
        match = re.search(r"\b\d{8,16}\b", text)
        return match.group(0) if match else None

    def _extract_city(self, text: str) -> str | None:
        match = re.search(r"city\s*=?\s*['\"]?([A-Za-z][A-Za-z\s-]+)['\"]?", text, re.I)
        if match:
            return match.group(1).strip().split()[0].title()
        if "jakarta" in text.lower():
            return "Jakarta"
        return None

    def _extract_tag(self, text: str) -> str | None:
        match = re.search(r"tag(?:ged)?\s+['\"]?([A-Za-z0-9_-]+)['\"]?", text, re.I)
        if match:
            return match.group(1)
        match = re.search(r"add\s+['\"]?([A-Za-z0-9_-]+)['\"]?\s+tag", text, re.I)
        if match:
            return match.group(1)
        return None

    def _extract_template(self, text: str) -> str | None:
        match = re.search(r"template\s+['\"]?([A-Za-z0-9_-]+)['\"]?", text, re.I)
        if match:
            return match.group(1)
        match = re.search(r"['\"]([A-Za-z0-9_-]+)['\"]\s+template", text, re.I)
        if match:
            return match.group(1)
        return None

    def _extract_name(self, text: str) -> str | None:
        match = re.search(r"(?:named|name\s*=?|for)\s+['\"]?([A-Z][A-Za-z\s-]{1,40})['\"]?", text)
        if match:
            return match.group(1).strip().split()[0]
        match = re.search(r"contact\s+['\"]?([A-Z][A-Za-z\s-]{1,40})['\"]?", text)
        if match:
            return match.group(1).strip().split()[0]
        return None

    def _extract_custom_params(self, text: str) -> list[JsonDict]:
        params: list[JsonDict] = []
        city = self._extract_city(text)
        if city:
            params.append({"name": "city", "value": city})
        for key, value in re.findall(r"attribute\s+['\"]?([A-Za-z0-9_-]+)['\"]?\s*=\s*['\"]?([A-Za-z0-9_-]+)['\"]?", text, re.I):
            if key.lower() != "city":
                params.append({"name": key, "value": value})
        return params


def plan_from_dict(data: JsonDict) -> Plan:
    steps = [
        PlanStep(tool=str(step["tool"]), args=dict(step.get("args", {})))
        for step in data.get("steps", [])
    ]
    return Plan(
        summary=str(data.get("summary", "")),
        requires_confirmation=bool(data.get("requires_confirmation", True)),
        steps=steps,
        clarification=data.get("clarification"),
    )


def _json_from_text(text: str) -> JsonDict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise PlannerError("Planner response must be a JSON object")
    return data


def normalize_plan(instruction: str, plan: Plan) -> Plan:
    """Apply deterministic business rules after LLM planning.

    This keeps the LLM useful for intent parsing while preventing small wording
    differences from breaking the fixed demo workflows.
    """
    lowered = instruction.lower()
    steps = [_normalize_step_args(step) for step in plan.steps]

    if "escalate" in lowered:
        number = FallbackPlanner()._extract_number(instruction)
        if number:
            normalized_steps: list[PlanStep] = []
            has_assign = False
            has_tag = False
            for step in steps:
                if step.tool == "tickets.assign_team":
                    args = dict(step.args)
                    args["whatsappNumber"] = args.get("whatsappNumber") or number
                    if str(args.get("teamName", "")).lower() in {
                        "support team",
                        "support",
                    }:
                        args["teamName"] = "Support"
                    normalized_steps.append(PlanStep(step.tool, args))
                    has_assign = True
                elif step.tool == "tags.add":
                    args = dict(step.args)
                    args["whatsappNumber"] = args.get("whatsappNumber") or number
                    if str(args.get("tag", "")).lower() in {"", "escalate", "escalated"}:
                        args["tag"] = "escalated"
                    normalized_steps.append(PlanStep(step.tool, args))
                    has_tag = True
                else:
                    normalized_steps.append(step)
            if not has_assign:
                normalized_steps.insert(
                    0,
                    PlanStep(
                        "tickets.assign_team",
                        {"whatsappNumber": number, "teamName": "Support"},
                    ),
                )
            if not has_tag:
                normalized_steps.append(
                    PlanStep(
                        "tags.add",
                        {"whatsappNumber": number, "tag": "escalated"},
                    )
                )
            return Plan(
                summary=f"Escalate {number} to Support and add escalated tag",
                requires_confirmation=True,
                steps=normalized_steps,
                clarification=plan.clarification,
            )

    if "flash_sale" in lowered and ("jakarta" in lowered or "city" in lowered):
        city = FallbackPlanner()._extract_city(instruction) or "Jakarta"
        steps = [
            PlanStep("contacts.search_by_attribute", {"name": "city", "value": city}),
            PlanStep(
                "broadcasts.send_to_segment",
                {
                    "template_name": "flash_sale",
                    "broadcast_name": f"flash_sale_{city.lower()}",
                    "segmentName": f"city={city}",
                    "audience_from_step": 1,
                },
            ),
        ]
        return Plan(
            summary=f"Verify city={city} audience and send flash_sale broadcast",
            requires_confirmation=True,
            steps=steps,
            clarification=plan.clarification,
        )

    return Plan(
        summary=plan.summary,
        requires_confirmation=plan.requires_confirmation,
        steps=steps,
        clarification=plan.clarification,
    )


def _normalize_step_args(step: PlanStep) -> PlanStep:
    args = dict(step.args)
    if step.tool in {"contacts.add", "contacts.update_attributes"}:
        args["customParams"] = _normalize_custom_params(args.get("customParams", []))
    return PlanStep(step.tool, args)


def _normalize_custom_params(value: object) -> list[JsonDict]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [
            {"name": str(name), "value": str(param_value)}
            for name, param_value in value.items()
        ]
    if isinstance(value, list):
        normalized: list[JsonDict] = []
        for item in value:
            if isinstance(item, dict) and "name" in item and "value" in item:
                normalized.append(
                    {"name": str(item["name"]), "value": str(item["value"])}
                )
        return normalized
    return []


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)
