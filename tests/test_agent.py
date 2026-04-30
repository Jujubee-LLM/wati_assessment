from __future__ import annotations

import unittest

from wati_agent.executor import Executor
from wati_agent.planner import FallbackPlanner


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
            report.step_results[0].requests[0].body,
            {
                "template_name": "flash_sale",
                "broadcast_name": "flash_sale_jakarta",
                "segmentName": "city=Jakarta",
            },
        )


if __name__ == "__main__":
    unittest.main()
