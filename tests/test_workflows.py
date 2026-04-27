from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.automation.workflows import WorkflowConfigError, WorkflowRunner
from app.config import PlutoSettings
from app.integrations.base import ActionResult
from app.integrations.executor import AssistantExecutor
from app.intent.schema import ParsedIntent


class WorkflowRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp_dir.name) / "workflows.json"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def write_workflows(self, payload: dict) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_loads_and_validates_config(self) -> None:
        self.write_workflows(
            {
                "workflows": [
                    {
                        "name": "research",
                        "aliases": ["deep research"],
                        "steps": [{"intent": "open_site", "site_name": "Hacker News"}],
                    }
                ]
            }
        )

        runner = WorkflowRunner(self.path, "America/Toronto")
        workflow = runner.find_workflow("deep research")

        self.assertIsNotNone(workflow)
        self.assertEqual(workflow.name, "research")
        self.assertFalse(runner.needs_confirmation(workflow))

    def test_rejects_unsupported_step_intent(self) -> None:
        self.write_workflows(
            {
                "workflows": [
                    {
                        "name": "bad",
                        "steps": [{"intent": "delete_everything"}],
                    }
                ]
            }
        )

        runner = WorkflowRunner(self.path, "America/Toronto")
        with self.assertRaises(WorkflowConfigError):
            runner.load_workflows()

    def test_runs_steps_in_order(self) -> None:
        self.write_workflows(
            {
                "workflows": [
                    {
                        "name": "research",
                        "steps": [
                            {"label": "Open HN", "intent": "open_site", "site_name": "Hacker News"},
                            {"label": "Create note", "intent": "create_note", "note_text": "Research {date}"},
                        ],
                    }
                ]
            }
        )
        runner = WorkflowRunner(self.path, "America/Toronto")
        workflow = runner.find_workflow("research")
        seen: list[ParsedIntent] = []

        def execute_step(intent: ParsedIntent) -> ActionResult:
            seen.append(intent)
            return ActionResult(success=True, spoken_response="ok")

        result = runner.run(workflow, execute_step)

        self.assertTrue(result.success)
        self.assertEqual([intent.intent for intent in seen], ["open_site", "create_note"])
        self.assertEqual(seen[0].site_name, "Hacker News")
        self.assertIn("Research ", seen[1].note_text)

    def test_stops_on_first_failure(self) -> None:
        self.write_workflows(
            {
                "workflows": [
                    {
                        "name": "research",
                        "steps": [
                            {"label": "Open HN", "intent": "open_site", "site_name": "Hacker News"},
                            {"label": "Create note", "intent": "create_note", "note_text": "Research"},
                        ],
                    }
                ]
            }
        )
        runner = WorkflowRunner(self.path, "America/Toronto")
        workflow = runner.find_workflow("research")
        calls = 0

        def execute_step(_intent: ParsedIntent) -> ActionResult:
            nonlocal calls
            calls += 1
            return ActionResult(success=False, spoken_response="blocked", error="non_whitelisted_site")

        result = runner.run(workflow, execute_step)

        self.assertFalse(result.success)
        self.assertEqual(calls, 1)
        self.assertIn("Open HN", result.spoken_response)


class WorkflowExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.workflow_path = Path(self.tmp_dir.name) / "workflows.json"
        self.whitelist_path = Path(self.tmp_dir.name) / "sites.json"
        self.whitelist_path.write_text(
            json.dumps({"allowed_sites": [{"name": "Hacker News", "url": "https://news.ycombinator.com/"}]}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    @patch("app.integrations.executor.speak")
    def test_risky_workflow_requires_confirmation(self, _mock_speak) -> None:
        self.workflow_path.write_text(
            json.dumps(
                {
                    "workflows": [
                        {
                            "name": "planning",
                            "steps": [{"intent": "add_reminder", "reminder_text": "Review notes"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        settings = PlutoSettings(
            workflows_path=self.workflow_path,
            whitelist_path=self.whitelist_path,
            wakeword_models=["alexa"],
        )
        executor = AssistantExecutor(settings, logger=Mock())

        result = executor.execute(
            ParsedIntent(intent="run_workflow", utterance="run planning", confidence=1.0, workflow_name="planning")
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "workflow_confirmation_required")
        self.assertIn("Say yes", result.spoken_response)


if __name__ == "__main__":
    unittest.main()
