from __future__ import annotations

from .client import MockWatiClient
from .models import ExecutionReport, JsonDict, Plan, PlanStep, RequestRecord, StepResult, ToolResult
from .tools import validate_plan


class PlanValidationError(Exception):
    pass


class Executor:
    def __init__(self, client: MockWatiClient | None = None) -> None:
        self.client = client or MockWatiClient()

    def run(self, plan: Plan, dry_run: bool = True) -> ExecutionReport:
        errors = validate_plan(plan)
        if errors:
            raise PlanValidationError("; ".join(errors))

        context: dict[int, object] = {}
        step_results: list[StepResult] = []
        for index, step in enumerate(plan.steps, start=1):
            result = self._preview_step(step, context) if dry_run else self._execute_step(step, context)
            context[index] = result.output
            step_results.append(
                StepResult(
                    index=index,
                    tool=step.tool,
                    ok=result.ok,
                    requests=result.requests,
                    output=result.output,
                    error=result.error,
                )
            )
            if not result.ok and self._is_blocking_failure(step):
                break
        return ExecutionReport(plan=plan, dry_run=dry_run, step_results=step_results)

    def _preview_step(self, step: PlanStep, context: dict[int, object]) -> ToolResult:
        return self._execute_step(step, context, mutate=False)

    def _execute_step(
        self, step: PlanStep, context: dict[int, object], mutate: bool = True
    ) -> ToolResult:
        if step.tool == "contacts.search_by_tag":
            return self.client.search_contacts_by_tag(step.args["tag"])
        if step.tool == "contacts.search_by_attribute":
            return self.client.search_contacts_by_attribute(
                step.args["name"], step.args["value"]
            )
        if step.tool == "tags.add":
            if mutate:
                return self.client.add_tag(step.args["whatsappNumber"], step.args["tag"])
            return ToolResult(
                True,
                output={"preview": True},
                requests=[
                    RequestRecord(
                        "POST",
                        f"/api/v1/addTag/{step.args['whatsappNumber']}",
                        {"tag": step.args["tag"]},
                    )
                ],
            )
        if step.tool == "messages.send_template":
            return self._send_template(step.args, mutate=mutate)
        if step.tool == "messages.send_template_batch":
            return self._send_template_batch(step.args, context, mutate=mutate)
        if step.tool == "broadcasts.send_to_segment":
            if mutate:
                return self.client.send_broadcast_to_segment(
                    step.args["template_name"],
                    step.args["broadcast_name"],
                    step.args["segmentName"],
                )
            return ToolResult(
                True,
                output={"preview": True},
                requests=[
                    RequestRecord(
                        "POST",
                        "/api/v1/sendBroadcastToSegment",
                        {
                            "template_name": step.args["template_name"],
                            "broadcast_name": step.args["broadcast_name"],
                            "segmentName": step.args["segmentName"],
                        },
                    )
                ],
            )
        if step.tool == "tickets.assign_team":
            if mutate:
                return self.client.assign_ticket(
                    step.args["whatsappNumber"], step.args["teamName"]
                )
            return ToolResult(
                True,
                output={"preview": True},
                requests=[
                    RequestRecord(
                        "POST",
                        "/api/v1/tickets/assign",
                        {
                            "whatsappNumber": step.args["whatsappNumber"],
                            "teamName": step.args["teamName"],
                        },
                    )
                ],
            )
        return ToolResult(False, error=f"Unsupported tool: {step.tool}")

    def _send_template(self, args: JsonDict, mutate: bool) -> ToolResult:
        parameters = args.get("parameters", [])
        if mutate:
            return self.client.send_template_message(
                args["whatsappNumber"],
                args["template_name"],
                parameters=parameters,
                broadcast_name=args.get("broadcast_name"),
            )
        body = {
            "template_name": args["template_name"],
            "broadcast_name": args.get("broadcast_name", f"{args['template_name']}_direct"),
            "parameters": parameters,
        }
        return ToolResult(
            True,
            output={"preview": True},
            requests=[
                RequestRecord(
                    "POST",
                    f"/api/v2/sendTemplateMessage/{args['whatsappNumber']}",
                    body,
                )
            ],
        )

    def _send_template_batch(
        self, args: JsonDict, context: dict[int, object], mutate: bool
    ) -> ToolResult:
        contacts = context.get(args["contacts_from_step"])
        if not isinstance(contacts, list):
            return ToolResult(False, error="Referenced step did not return contacts")

        requests: list[RequestRecord] = []
        successes: list[str] = []
        failures: list[dict[str, str]] = []
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            parameters = self._build_template_parameters(
                args.get("parameter_mapping", {}), contact
            )
            if mutate:
                result = self.client.send_template_message(
                    contact["whatsappNumber"],
                    args["template_name"],
                    parameters=parameters,
                    broadcast_name=args.get("broadcast_name", f"{args['template_name']}_batch"),
                )
                requests.extend(result.requests)
                if result.ok:
                    successes.append(contact["whatsappNumber"])
                else:
                    failures.append(
                        {
                            "whatsappNumber": contact["whatsappNumber"],
                            "error": result.error or "Unknown error",
                        }
                    )
            else:
                body = {
                    "template_name": args["template_name"],
                    "broadcast_name": args.get(
                        "broadcast_name", f"{args['template_name']}_batch"
                    ),
                    "parameters": parameters,
                }
                requests.append(
                    RequestRecord(
                        "POST",
                        f"/api/v2/sendTemplateMessage/{contact['whatsappNumber']}",
                        body,
                    )
                )
                successes.append(contact["whatsappNumber"])

        return ToolResult(
            ok=not failures,
            output={"successful": successes, "failed": failures},
            requests=requests,
            error=f"{len(failures)} message(s) failed" if failures else None,
        )

    def _build_template_parameters(
        self, mapping: dict[str, str], contact: dict[str, object]
    ) -> list[JsonDict]:
        parameters: list[JsonDict] = []
        for name, expression in mapping.items():
            parameters.append({"name": name, "value": self._resolve_expression(expression, contact)})
        return parameters

    def _resolve_expression(self, expression: str, contact: dict[str, object]) -> str:
        if expression == "contact.name":
            return str(contact.get("name", ""))
        if expression.startswith("contact.customParams."):
            key = expression.rsplit(".", 1)[-1]
            custom_params = contact.get("customParams", {})
            if isinstance(custom_params, dict):
                return str(custom_params.get(key, ""))
        return expression

    def _is_blocking_failure(self, step: PlanStep) -> bool:
        return step.tool.startswith("contacts.") or step.tool.startswith("broadcasts.")
