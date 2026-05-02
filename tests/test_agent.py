from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from wati_agent.client import HttpWatiClient
from wati_agent.cli import main
from wati_agent.executor import Executor, PlanValidationError
from wati_agent.models import Plan, PlanStep
from wati_agent.planner import FallbackPlanner, PlannerError, QwenPlanner, _json_from_text, make_planner


class FallbackPlannerTests(unittest.TestCase):
    def test_vip_renewal_plan(self) -> None:
        plan = FallbackPlanner().plan(
            "Find all contacts tagged VIP and send them the renewal_reminder template with their name filled in"
        )

        self.assertEqual(plan.summary, "Send renewal_reminder template to VIP contacts")
        self.assertEqual([step.tool for step in plan.steps], ["contacts.search_by_tag", "messages.send_template_batch"])

    def test_escalation_plan(self) -> None:
        plan = FallbackPlanner().plan("escalate 6281234567890")

        self.assertEqual([step.tool for step in plan.steps], ["tickets.assign_team", "tags.add"])
        self.assertEqual(plan.steps[0].args["teamName"], "Support")
        self.assertEqual(plan.steps[1].args["tag"], "escalated")

    def test_clarification(self) -> None:
        plan = FallbackPlanner().plan("send a template")

        self.assertTrue(plan.is_clarification)
        self.assertIn("Which template", plan.clarification or "")

    def test_create_contact_and_tag_plan(self) -> None:
        plan = FallbackPlanner().plan(
            "Create a new contact for Alice with phone 6289999999999 city = Jakarta and add tag lead"
        )

        self.assertEqual([step.tool for step in plan.steps], ["contacts.add", "tags.add"])
        self.assertEqual(plan.steps[0].args["name"], "Alice")
        self.assertEqual(plan.steps[0].args["customParams"], [{"name": "city", "value": "Jakarta"}])
        self.assertEqual(plan.steps[1].args["tag"], "lead")


class PlannerJsonParsingTests(unittest.TestCase):
    def test_json_from_text_returns_dict(self) -> None:
        self.assertEqual(
            _json_from_text('{"summary":"x","steps":[]}'),
            {"summary": "x", "steps": []},
        )

    def test_json_from_text_handles_markdown_fence(self) -> None:
        self.assertEqual(
            _json_from_text('```json\n{"summary":"x","steps":[]}\n```'),
            {"summary": "x", "steps": []},
        )

    def test_json_from_text_rejects_non_object(self) -> None:
        with self.assertRaises(PlannerError):
            _json_from_text('["not", "a", "plan"]')


class QwenPlannerTests(unittest.TestCase):
    def test_explicit_qwen_requires_api_key(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            with self.assertRaises(PlannerError):
                make_planner("qwen")

    def test_default_auto_requires_api_key(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            with self.assertRaises(PlannerError) as ctx:
                make_planner("auto")

        self.assertIn("--provider fallback", str(ctx.exception))

    def test_qwen_response_is_parsed_and_normalized(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "Escalate contact",
                                "steps": [
                                    {
                                        "tool": "tickets.assign_team",
                                        "args": {"teamName": "Support Team"},
                                    }
                                ],
                            }
                        )
                    }
                }
            ]
        }

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(response).encode("utf-8")

        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}):
            with patch("wati_agent.planner.urllib.request.urlopen", return_value=FakeResponse()):
                plan = QwenPlanner().plan("escalate 6281234567890")

        self.assertEqual([step.tool for step in plan.steps], ["tickets.assign_team", "tags.add"])
        self.assertEqual(plan.steps[0].args["whatsappNumber"], "6281234567890")
        self.assertEqual(plan.steps[0].args["teamName"], "Support")
        self.assertEqual(plan.steps[1].args["tag"], "escalated")

    def test_qwen_retries_transient_timeout(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "Escalate contact",
                                "steps": [
                                    {
                                        "tool": "tickets.assign_team",
                                        "args": {"teamName": "Support"},
                                    }
                                ],
                            }
                        )
                    }
                }
            ]
        }

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(response).encode("utf-8")

        with patch.dict(
            os.environ,
            {
                "DASHSCOPE_API_KEY": "test",
                "QWEN_TIMEOUT_SECONDS": "1",
                "QWEN_MAX_RETRIES": "2",
            },
        ):
            with patch("wati_agent.planner.time.sleep"):
                with patch(
                    "wati_agent.planner.urllib.request.urlopen",
                    side_effect=[TimeoutError("timed out"), FakeResponse()],
                ):
                    plan = QwenPlanner().plan("escalate 6281234567890")

        self.assertEqual([step.tool for step in plan.steps], ["tickets.assign_team", "tags.add"])

    def test_qwen_normalizes_custom_params_dict(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "Create contact",
                                "steps": [
                                    {
                                        "tool": "contacts.add",
                                        "args": {
                                            "whatsappNumber": "6289999999999",
                                            "name": "Alice",
                                            "customParams": {"city": "Jakarta"},
                                        },
                                    },
                                    {
                                        "tool": "tags.add",
                                        "args": {
                                            "whatsappNumber": "6289999999999",
                                            "tag": "lead",
                                        },
                                    },
                                ],
                            }
                        )
                    }
                }
            ]
        }

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(response).encode("utf-8")

        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}):
            with patch("wati_agent.planner.urllib.request.urlopen", return_value=FakeResponse()):
                plan = QwenPlanner().plan(
                    "Create a new contact for Alice with phone 6289999999999 city = Jakarta and add tag lead"
                )

        self.assertEqual(
            plan.steps[0].args["customParams"],
            [{"name": "city", "value": "Jakarta"}],
        )


class ExecutorTests(unittest.TestCase):
    def test_dry_run_vip_renewal_expands_batch_requests(self) -> None:
        plan = FallbackPlanner().plan(
            "Find all contacts tagged VIP and send them the renewal_reminder template with their name filled in"
        )
        report = Executor().run(plan, dry_run=True)

        self.assertTrue(report.ok)
        self.assertEqual(len(report.step_results), 2)
        self.assertEqual(len(report.step_results[1].requests), 2)
        self.assertEqual(
            report.step_results[1].requests[0].endpoint,
            "/api/v2/sendTemplateMessage/6281111111111",
        )

    def test_execute_escalation(self) -> None:
        plan = FallbackPlanner().plan("escalate 6281234567890")
        report = Executor().run(plan, dry_run=False)

        self.assertTrue(report.ok)
        self.assertEqual(report.step_results[0].requests[0].endpoint, "/api/v1/tickets/assign")
        self.assertEqual(
            report.step_results[1].requests[0].endpoint,
            "/api/v1/addTag/6281234567890",
        )

    def test_city_broadcast(self) -> None:
        plan = FallbackPlanner().plan(
            "Send a broadcast with the flash_sale template to all contacts who have city = Jakarta"
        )
        report = Executor().run(plan, dry_run=True)

        self.assertTrue(report.ok)
        self.assertEqual(
            [step.tool for step in report.step_results],
            ["contacts.search_by_attribute", "broadcasts.send_to_segment"],
        )
        self.assertEqual(plan.steps[1].args["audience_from_step"], 1)
        self.assertEqual(report.step_results[0].requests[0].endpoint, "/api/v1/getContacts")
        self.assertEqual(
            report.step_results[1].requests[0].body,
            {
                "template_name": "flash_sale",
                "broadcast_name": "flash_sale_jakarta",
                "segmentName": "city=Jakarta",
            },
        )

    def test_execute_create_contact_and_tag(self) -> None:
        plan = FallbackPlanner().plan(
            "Create a new contact for Alice with phone 6289999999999 city = Jakarta and add tag lead"
        )
        report = Executor().run(plan, dry_run=False)

        self.assertTrue(report.ok)
        self.assertEqual(report.step_results[0].requests[0].endpoint, "/api/v1/addContact/6289999999999")
        self.assertEqual(report.step_results[1].requests[0].endpoint, "/api/v1/addTag/6289999999999")

    def test_validation_rejects_bad_phone_number(self) -> None:
        plan = Plan(
            summary="bad",
            steps=[PlanStep("tags.add", {"whatsappNumber": "abc", "tag": "VIP"})],
        )

        with self.assertRaises(PlanValidationError):
            Executor().run(plan)

    def test_validation_rejects_bad_parameter_mapping(self) -> None:
        plan = Plan(
            summary="bad",
            steps=[
                PlanStep("contacts.search_by_tag", {"tag": "VIP"}),
                PlanStep(
                    "messages.send_template_batch",
                    {
                        "contacts_from_step": 1,
                        "template_name": "renewal_reminder",
                        "parameter_mapping": ["bad"],
                    },
                ),
            ],
        )

        with self.assertRaises(PlanValidationError):
            Executor().run(plan)


class HttpClientTests(unittest.TestCase):
    def test_attribute_search_reads_pages_until_short_page(self) -> None:
        calls: list[str] = []

        def fake_request(self, method, endpoint, body=None, normalizer=None):
            calls.append(endpoint)
            if "pageNumber=1" in endpoint:
                payload = [
                    {"whatsappNumber": str(index), "customParams": {"city": "Jakarta"}}
                    for index in range(100)
                ]
            else:
                payload = [{"whatsappNumber": "last", "customParams": {"city": "Bandung"}}]
            return type(
                "Result",
                (),
                {
                    "ok": True,
                    "output": payload,
                    "requests": [],
                    "error": None,
                },
            )()

        client = HttpWatiClient("tenant", "token")
        with patch.object(HttpWatiClient, "_request", fake_request):
            result = client.search_contacts_by_attribute("city", "Jakarta")

        self.assertTrue(result.ok)
        self.assertEqual(len(result.output), 100)
        self.assertEqual(len(calls), 2)


class CliTests(unittest.TestCase):
    def test_default_cli_requires_llm_key(self) -> None:
        output = io.StringIO()

        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
            with redirect_stdout(output):
                code = main(["--json", "escalate", "6281234567890"])

        self.assertEqual(code, 2)
        error = json.loads(output.getvalue())["error"]
        self.assertIn("DASHSCOPE_API_KEY", error)
        self.assertIn("--provider fallback", error)

    def test_real_client_config_error_is_json_formatted(self) -> None:
        output = io.StringIO()
        env = {
            "DASHSCOPE_API_KEY": "",
            "WATI_TENANT_ID": "",
            "WATI_API_BASE_URL": "",
            "WATI_TOKEN": "",
        }

        with patch.dict(os.environ, env):
            with redirect_stdout(output):
                code = main(
                    [
                        "--json",
                        "--provider",
                        "fallback",
                        "--client",
                        "real",
                        "escalate",
                        "6281234567890",
                    ]
                )

        self.assertEqual(code, 2)
        self.assertIn("WATI_TENANT_ID", json.loads(output.getvalue())["error"])


if __name__ == "__main__":
    unittest.main()
