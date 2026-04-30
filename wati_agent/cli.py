from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from .client import HttpWatiClient, MockWatiClient
from .config import load_dotenv
from .executor import Executor, PlanValidationError
from .models import ExecutionReport, Plan, RequestRecord, StepResult
from .planner import make_planner


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="wati_agent",
        description="Generate and execute WATI WhatsApp automation plans.",
    )
    parser.add_argument("instruction", nargs="+", help="Natural-language workflow")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the generated plan. Default is dry-run preview.",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "qwen", "fallback"],
        default="auto",
        help="Planner provider. auto uses Qwen when DASHSCOPE_API_KEY is set.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    parser.add_argument(
        "--client",
        choices=["mock", "real"],
        default="mock",
        help="WATI client implementation. real requires WATI_TENANT_ID and WATI_TOKEN.",
    )
    args = parser.parse_args(argv)

    instruction = " ".join(args.instruction)
    planner = make_planner(args.provider)
    plan = planner.plan(instruction)

    if plan.is_clarification:
        if args.json:
            print(json.dumps(_plan_to_dict(plan), ensure_ascii=False, indent=2))
        else:
            print(f"Clarification needed: {plan.clarification}")
        return 2

    client = _make_client(args.client)
    executor = Executor(client=client)
    try:
        report = executor.run(plan, dry_run=not args.execute)
    except PlanValidationError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"Plan validation failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(_report_to_dict(report), ensure_ascii=False, indent=2))
    else:
        print(_format_report(report))
    return 0 if report.ok else 1


def _make_client(client_name: str) -> MockWatiClient | HttpWatiClient:
    if client_name == "mock":
        return MockWatiClient()

    tenant_id = os.getenv("WATI_TENANT_ID")
    token = os.getenv("WATI_TOKEN")
    base_url = os.getenv("WATI_API_BASE_URL")
    if not (tenant_id or base_url) or not token:
        raise SystemExit(
            "WATI_TENANT_ID or WATI_API_BASE_URL, plus WATI_TOKEN, must be set when using --client real"
        )
    return HttpWatiClient(tenant_id=base_url or tenant_id or "", token=token)


def _format_report(report: ExecutionReport) -> str:
    lines: list[str] = []
    lines.append("Intent understood:")
    lines.append(report.plan.summary)
    lines.append("")
    lines.append("Execution mode:")
    lines.append("Dry run. No side-effecting API calls executed." if report.dry_run else "Execute. API calls executed through the configured WATI client.")
    lines.append("")
    lines.append("Planned WATI API calls:")
    for step in report.step_results:
        lines.append(_format_step(step))
    lines.append("")
    lines.append("Result:")
    lines.append("All steps completed." if report.ok else "One or more steps failed.")
    return "\n".join(lines)


def _format_step(step: StepResult) -> str:
    lines = [f"{step.index}. {step.tool}"]
    for request in step.requests:
        lines.append(f"   {request.method} {request.endpoint}")
        if request.body:
            body = json.dumps(request.body, ensure_ascii=False)
            lines.append(f"   body: {body}")
    if step.error:
        lines.append(f"   error: {step.error}")
    elif step.output is not None:
        lines.append(f"   output: {_compact_json(step.output)}")
    return "\n".join(lines)


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _report_to_dict(report: ExecutionReport) -> dict[str, object]:
    return {
        "dry_run": report.dry_run,
        "ok": report.ok,
        "plan": _plan_to_dict(report.plan),
        "steps": [_step_to_dict(step) for step in report.step_results],
    }


def _plan_to_dict(plan: Plan) -> dict[str, object]:
    return {
        "summary": plan.summary,
        "requires_confirmation": plan.requires_confirmation,
        "clarification": plan.clarification,
        "steps": [
            {"tool": step.tool, "args": step.args}
            for step in plan.steps
        ],
    }


def _step_to_dict(step: StepResult) -> dict[str, object]:
    return {
        "index": step.index,
        "tool": step.tool,
        "ok": step.ok,
        "requests": [_request_to_dict(request) for request in step.requests],
        "output": step.output,
        "error": step.error,
    }


def _request_to_dict(request: RequestRecord) -> dict[str, object]:
    return {
        "method": request.method,
        "endpoint": request.endpoint,
        "body": request.body,
    }
