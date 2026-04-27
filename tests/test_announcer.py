from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.automation.announcer import BackgroundAnnouncer
from app.config import PlutoSettings


class FakeEventKit:
    def __init__(self) -> None:
        self.reminders = [
            {"id": "r1", "title": "Pay rent", "due_epoch": 1.0},
            {"id": "r2", "title": "Call mom", "due_epoch": 2.0},
        ]
        self.events = [
            {"id": "e1", "title": "Standup", "start_epoch": 1700000000.0},
        ]

    def get_due_reminders(self, lookahead_min: int = 15):
        return list(self.reminders)

    def get_upcoming_events(self, lookahead_min: int = 30):
        return list(self.events)


class AnnouncerDedupeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        whitelist = Path(self.tmp_dir.name) / "sites.json"
        whitelist.write_text('{"allowed_sites": [{"name":"Wikipedia","url":"https://www.wikipedia.org"}]}', encoding="utf-8")
        self.settings = PlutoSettings(
            whitelist_path=whitelist,
            wakeword_models=["alexa"],
            announcer_state_path=Path(self.tmp_dir.name) / "announcer.json",
            announcer_enabled=True,
        )
        self.eventkit = FakeEventKit()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    @patch("app.automation.announcer.speak")
    def test_announcements_are_not_duplicated(self, mock_speak) -> None:
        mock_speak.return_value = True
        logger = logging.getLogger("test.announcer")
        announcer = BackgroundAnnouncer(self.settings, logger, self.eventkit)

        announcer.tick()
        self.assertEqual(mock_speak.call_count, 3)

        announcer.tick()
        self.assertEqual(mock_speak.call_count, 3)

        self.eventkit.reminders.append({"id": "r3", "title": "Water plants", "due_epoch": 3.0})
        announcer.tick()
        self.assertEqual(mock_speak.call_count, 4)


if __name__ == "__main__":
    unittest.main()
