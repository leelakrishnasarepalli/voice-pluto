"""Background announcer loop for due reminders and upcoming calendar events."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.alerts.voice import speak
from app.config import PlutoSettings
from app.integrations.eventkit_adapter import EventKitAdapter
from app.state.announcement_store import AnnouncementStore


class BackgroundAnnouncer:
    """Checks EventKit data and announces each item once."""

    def __init__(self, settings: PlutoSettings, logger: logging.Logger, eventkit: EventKitAdapter | None) -> None:
        self.settings = settings
        self.logger = logger
        self.eventkit = eventkit
        self.store = AnnouncementStore(settings.announcer_state_path)

    def tick(self) -> None:
        if not self.settings.announcer_enabled:
            return
        if self.eventkit is None:
            return

        for attempt in range(1, 3):
            try:
                self._announce_due_reminders()
                self._announce_upcoming_events()
                return
            except Exception as exc:
                if attempt < 2:
                    self.logger.warning("Background announcer tick failed (attempt %s/2): %s", attempt, exc)
                    continue
                self.logger.warning("Background announcer tick failed: %s", exc)

    def _announce_due_reminders(self) -> None:
        due = self.eventkit.get_due_reminders(lookahead_min=self.settings.announcer_reminder_lookahead_min)
        for item in due:
            key = f"reminder:{item['id']}"
            if self.store.has_announced(key):
                continue
            msg = f"Reminder due: {item['title']}"
            spoke = speak(msg, self.settings)
            self.store.mark_announced(key)
            self.logger.info("Announced reminder due: %s (spoke=%s)", item["title"], spoke)

    def _announce_upcoming_events(self) -> None:
        upcoming = self.eventkit.get_upcoming_events(lookahead_min=self.settings.announcer_event_lookahead_min)
        tz = ZoneInfo(self.settings.timezone)
        for item in upcoming:
            key = f"event:{item['id']}"
            if self.store.has_announced(key):
                continue
            when = datetime.fromtimestamp(float(item["start_epoch"]), tz).strftime("%I:%M %p")
            msg = f"Upcoming event at {when}: {item['title']}"
            spoke = speak(msg, self.settings)
            self.store.mark_announced(key)
            self.logger.info("Announced event: %s (spoke=%s)", item["title"], spoke)
