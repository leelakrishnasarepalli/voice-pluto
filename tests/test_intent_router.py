from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import PlutoSettings
from app.intent.router import IntentRouter


class IntentRouterFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        whitelist_path = Path(self.tmp_dir.name) / "sites.json"
        whitelist_path.write_text(
            json.dumps(
                {
                    "allowed_sites": [
                        {"name": "Wikipedia", "url": "https://www.wikipedia.org"},
                        {"name": "OpenAI Docs", "url": "https://platform.openai.com/docs"},
                        {"name": "YouTube", "url": "https://www.youtube.com"},
                        {"name": "Example", "url": "https://example.com"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        settings = PlutoSettings(
            openai_api_key="",  # Force deterministic fallback parser in unit tests.
            whitelist_path=whitelist_path,
            wakeword_models=["alexa"],
        )
        self.router = IntentRouter(settings=settings, logger=logging.getLogger("test.intent"))

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_fallback_intents(self) -> None:
        cases = [
            ("remind me to call mom at 5", "add_reminder"),
            ("add remider to buy milk", "add_reminder"),
            ("list my reminders", "list_reminders"),
            ("show my remiders", "list_reminders"),
            ("schedule project sync tomorrow at 3pm", "add_calendar_event"),
            ("what's on my calender today", "upcoming_calendar"),
            ("create note that the server reboot is tonight", "create_note"),
            ("read notes", "read_notes"),
            ("open wikipdia", "open_site"),
            ("go to openai docs", "open_site"),
            ("open browser", "open_site"),
            ("opening chrome", "open_site"),
            ("set a timer for ten minutes", "set_timer"),
            ("set timr for 30 sec", "set_timer"),
            ("show active timers", "list_timers"),
            ("cancel timer", "cancel_timer"),
            ("please stop timer called pasta", "cancel_timer"),
            ("open reddit", "unknown"),
            ("could you please remind me to stretch", "add_reminder"),
            ("what reminders do i have right now", "list_reminders"),
            ("book meeting with sam tomorrow at 2 pm", "add_calendar_event"),
            ("next events on my calendar", "upcoming_calendar"),
            ("take a note buy batteries", "create_note"),
            ("show me my notes", "read_notes"),
            ("navigate to wikipdia", "open_site"),
            ("launch chrome", "open_site"),
            ("set timer for half an hour", "set_timer"),
            ("what timers are running", "list_timers"),
            ("run research", "run_workflow"),
            ("run research.", "run_workflow"),
            ("start research workflow", "run_workflow"),
        ]

        for utterance, expected_intent in cases:
            with self.subTest(utterance=utterance):
                result = self.router.parse(utterance)
                self.assertEqual(result.intent, expected_intent)

    def test_set_timer_extracts_seconds(self) -> None:
        result = self.router.parse("set a timer for 2 minutes")
        self.assertEqual(result.intent, "set_timer")
        self.assertEqual(result.timer_seconds, 120)

    def test_workflow_name_strips_trailing_punctuation(self) -> None:
        result = self.router.parse("run research.")
        self.assertEqual(result.intent, "run_workflow")
        self.assertEqual(result.workflow_name, "research")

    @patch.object(IntentRouter, "_parse_with_llm")
    def test_workflow_parse_skips_llm(self, mock_llm) -> None:
        result = self.router.parse("run research.")
        self.assertEqual(result.intent, "run_workflow")
        self.assertEqual(result.workflow_name, "research")
        mock_llm.assert_not_called()

    def test_open_site_enforces_whitelist(self) -> None:
        result = self.router.parse("open wikipedia")
        self.assertEqual(result.intent, "open_site")
        self.assertEqual(result.site_name, "Wikipedia")
        self.assertEqual(result.site_url, "https://www.wikipedia.org")

        browser = self.router.parse("open chrome")
        self.assertEqual(browser.intent, "open_site")
        self.assertEqual(browser.site_name, "browser")

        blocked = self.router.parse("open twitter")
        self.assertEqual(blocked.intent, "unknown")
        self.assertEqual(blocked.reason, "open_site_not_whitelisted")


if __name__ == "__main__":
    unittest.main()
