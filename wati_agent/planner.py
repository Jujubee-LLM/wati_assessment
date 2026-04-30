from __future__ import annotations

import json
import os
import re
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
    if provider in {"auto", "qwen"} and os.getenv("DASHSCOPE_API_KEY"):
        return QwenPlanner(fallback=FallbackPlanner())
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
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise PlannerError(str(exc)) from exc

        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
        return normalize_plan(instruction, plan_from_dict(_json_from_text(content)))

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
            "template_name='flash_sale' and segmentName='city=Jakarta'.\n\n"
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
                summary=f"Send flash_sale broadcast to city={city} segment",
                requires_confirmation=True,
                steps=[
                    PlanStep(
                        "broadcasts.send_to_segment",
                        {
                            "template_name": "flash_sale",
                            "broadcast_name": f"flash_sale_{city.lower()}",
                            "segmentName": f"city={city}",
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
    steps = list(plan.steps)

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

    return plan
