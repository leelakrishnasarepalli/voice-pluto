"""Configuration loader for Pluto MVP."""

from __future__ import annotations

import os
import re
import json
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

load_dotenv(os.getenv("PLUTO_ENV_FILE", ".env"))


class PlutoSettings(BaseModel):
    """Runtime settings sourced from environment."""

    openai_api_key: str = Field(default="")
    intent_model: str = Field(default="gpt-5-mini")
    intent_timeout_sec: float = Field(default=12.0, gt=0, le=120)
    research_model: str = Field(default="gpt-5-mini")
    wakeword_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    wakeword_models: list[str] = Field(default_factory=lambda: ["alexa", "hey_mycroft"])
    wakeword_model_dir: Path = Field(default=Path("./app/audio/models"))
    whitelist_path: Path = Field(default=Path("./config/sites.json"))
    workflows_path: Path = Field(default=Path("./config/workflows.json"))
    poll_interval_sec: int = Field(default=30, ge=1, le=3600)
    timezone: str = Field(default="America/Toronto")
    do_not_disturb: bool = Field(default=False)
    quiet_hours_enabled: bool = Field(default=False)
    quiet_hours_start: str = Field(default="22:00")
    quiet_hours_end: str = Field(default="07:00")
    announcer_enabled: bool = Field(default=True)
    announcer_reminder_lookahead_min: int = Field(default=15, ge=1, le=1440)
    announcer_event_lookahead_min: int = Field(default=30, ge=1, le=1440)

    stt_model: str = Field(default="tiny")
    session_state_path: Path = Field(default=Path("./app/state/session.json"))
    timers_state_path: Path = Field(default=Path("./app/state/timers.json"))
    announcer_state_path: Path = Field(default=Path("./app/state/announcer.json"))

    silence_rms_threshold: float = Field(default=350.0, ge=1)
    min_utterance_sec: float = Field(default=0.8, gt=0, le=30)
    max_utterance_sec: float = Field(default=10.0, gt=0, le=120)
    silence_stop_sec: float = Field(default=1.0, gt=0, le=30)
    wakeword_cooldown_sec: float = Field(default=1.2, ge=0, le=30)
    post_action_suppression_sec: float = Field(default=3.0, ge=0, le=30)
    debug_log_interval_sec: float = Field(default=2.0, gt=0, le=60)

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def validate_hhmm(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("must be in HH:MM 24-hour format")
        return value

    @model_validator(mode="after")
    def validate_consistency(self):
        if not self.wakeword_models:
            raise ValueError("wakeword_models cannot be empty")
        if self.min_utterance_sec > self.max_utterance_sec:
            raise ValueError("min_utterance_sec cannot be greater than max_utterance_sec")
        return self


def get_settings() -> PlutoSettings:
    def parse_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    wakeword_models_raw = os.getenv("PLUTO_WAKEWORD_MODELS", "alexa,hey_mycroft")
    wakeword_models = [item.strip() for item in wakeword_models_raw.split(",") if item.strip()]

    return PlutoSettings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        intent_model=os.getenv("PLUTO_INTENT_MODEL", "gpt-5-mini"),
        intent_timeout_sec=float(os.getenv("PLUTO_INTENT_TIMEOUT_SEC", "12")),
        research_model=os.getenv("PLUTO_RESEARCH_MODEL", "gpt-5-mini"),
        wakeword_threshold=float(os.getenv("PLUTO_WAKEWORD_THRESHOLD", "0.5")),
        wakeword_models=wakeword_models,
        wakeword_model_dir=Path(os.getenv("PLUTO_WAKEWORD_MODEL_DIR", "./app/audio/models")),
        whitelist_path=Path(os.getenv("PLUTO_WHITELIST_PATH", "./config/sites.json")),
        workflows_path=Path(os.getenv("PLUTO_WORKFLOWS_PATH", "./config/workflows.json")),
        poll_interval_sec=int(os.getenv("PLUTO_POLL_INTERVAL_SEC", "30")),
        timezone=os.getenv("PLUTO_TIMEZONE", "America/Toronto"),
        do_not_disturb=parse_bool("PLUTO_DO_NOT_DISTURB", False),
        quiet_hours_enabled=parse_bool("PLUTO_QUIET_HOURS_ENABLED", False),
        quiet_hours_start=os.getenv("PLUTO_QUIET_HOURS_START", "22:00"),
        quiet_hours_end=os.getenv("PLUTO_QUIET_HOURS_END", "07:00"),
        announcer_enabled=parse_bool("PLUTO_ANNOUNCER_ENABLED", True),
        announcer_reminder_lookahead_min=int(os.getenv("PLUTO_ANNOUNCER_REMINDER_LOOKAHEAD_MIN", "15")),
        announcer_event_lookahead_min=int(os.getenv("PLUTO_ANNOUNCER_EVENT_LOOKAHEAD_MIN", "30")),
        stt_model=os.getenv("PLUTO_STT_MODEL", "tiny"),
        session_state_path=Path(os.getenv("PLUTO_SESSION_STATE_PATH", "./app/state/session.json")),
        timers_state_path=Path(os.getenv("PLUTO_TIMERS_STATE_PATH", "./app/state/timers.json")),
        announcer_state_path=Path(os.getenv("PLUTO_ANNOUNCER_STATE_PATH", "./app/state/announcer.json")),
        silence_rms_threshold=float(os.getenv("PLUTO_SILENCE_RMS_THRESHOLD", "350")),
        min_utterance_sec=float(os.getenv("PLUTO_MIN_UTTERANCE_SEC", "0.8")),
        max_utterance_sec=float(os.getenv("PLUTO_MAX_UTTERANCE_SEC", "10")),
        silence_stop_sec=float(os.getenv("PLUTO_SILENCE_STOP_SEC", "1.0")),
        wakeword_cooldown_sec=float(os.getenv("PLUTO_WAKEWORD_COOLDOWN_SEC", "1.2")),
        post_action_suppression_sec=float(os.getenv("PLUTO_POST_ACTION_SUPPRESSION_SEC", "3.0")),
        debug_log_interval_sec=float(os.getenv("PLUTO_DEBUG_LOG_INTERVAL_SEC", "2.0")),
    )


def validate_startup_config(settings: PlutoSettings) -> dict:
    """Structured startup config checks with explicit validation errors."""

    issues: list[str] = []
    summary: dict[str, object] = {
        "whitelist_path": str(settings.whitelist_path),
        "workflows_path": str(settings.workflows_path),
        "poll_interval_sec": settings.poll_interval_sec,
        "quiet_hours_enabled": settings.quiet_hours_enabled,
        "do_not_disturb": settings.do_not_disturb,
    }

    if not settings.whitelist_path.exists():
        issues.append(f"whitelist file not found: {settings.whitelist_path}")
    else:
        try:
            payload = json.loads(settings.whitelist_path.read_text(encoding="utf-8"))
            sites = payload.get("allowed_sites")
            if not isinstance(sites, list) or not sites:
                issues.append("whitelist must contain a non-empty allowed_sites list")
            else:
                for idx, item in enumerate(sites):
                    if not isinstance(item, dict):
                        issues.append(f"whitelist item {idx} must be an object")
                        continue
                    name = str(item.get("name", "")).strip()
                    url = str(item.get("url", "")).strip()
                    host = urlparse(url).netloc.lower().replace("www.", "")
                    if not name:
                        issues.append(f"whitelist item {idx} has empty name")
                    if not host:
                        issues.append(f"whitelist item {idx} has invalid url: {url!r}")
                summary["allowed_sites"] = len(sites)
        except json.JSONDecodeError as exc:
            issues.append(f"whitelist JSON invalid: {exc}")

    if not settings.workflows_path.exists():
        issues.append(f"workflow config not found: {settings.workflows_path}")
    else:
        try:
            from app.automation.workflows import WorkflowRunner

            workflows = WorkflowRunner(settings.workflows_path, settings.timezone).load_workflows()
            summary["workflows"] = len(workflows)
        except Exception as exc:
            issues.append(f"workflow config invalid: {exc}")

    if issues:
        raise RuntimeError("Startup config validation failed: " + "; ".join(issues))

    return summary
