"""Intent schema definitions for Pluto."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IntentType = Literal[
    "add_reminder",
    "list_reminders",
    "add_calendar_event",
    "upcoming_calendar",
    "create_note",
    "read_notes",
    "open_site",
    "set_timer",
    "list_timers",
    "cancel_timer",
    "run_workflow",
    "collect_research",
    "unknown",
]


class ParsedIntent(BaseModel):
    """Strict intent object emitted by the parser/router."""

    intent: IntentType
    utterance: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    reminder_text: str | None = None
    reminder_time: str | None = None

    calendar_title: str | None = None
    calendar_time: str | None = None

    note_text: str | None = None

    site_name: str | None = None
    site_url: str | None = None

    timer_seconds: int | None = Field(default=None, ge=1)
    timer_label: str | None = None
    timer_target: str | None = None

    workflow_name: str | None = None

    sources: list[dict] | None = None
    limit_per_source: int | None = Field(default=None, ge=1, le=20)
    open_sources: bool | None = None
    read_aloud: bool | None = None
    read_mode: str | None = None

    reason: str | None = None

    model_config = {
        "extra": "forbid",
    }
