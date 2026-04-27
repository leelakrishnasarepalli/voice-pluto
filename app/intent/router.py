"""Intent router using OpenAI extraction with deterministic fallback."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError

from app.config import PlutoSettings
from app.intent.schema import ParsedIntent


@dataclass
class AllowedSite:
    name: str
    url: str


class IntentRouter:
    """Parses natural language into strict intent JSON."""

    def __init__(self, settings: PlutoSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.allowed_sites = self._load_allowed_sites(settings.whitelist_path)

    def parse(self, utterance: str) -> ParsedIntent:
        utterance = utterance.strip()
        if not utterance:
            return ParsedIntent(intent="unknown", utterance=utterance, confidence=1.0, reason="empty_utterance")

        deterministic = self._parse_deterministic_priority(utterance)
        if deterministic is not None:
            return deterministic

        llm_result = self._parse_with_llm(utterance)
        if llm_result is not None:
            self.logger.debug("Intent parsed via LLM: %s", llm_result.intent)
            return llm_result

        self.logger.debug("Intent parser using fallback for utterance=%r", utterance)
        return self._parse_with_fallback(utterance)

    def _parse_deterministic_priority(self, utterance: str) -> ParsedIntent | None:
        normalized = self._normalize_text(utterance)
        workflow_name = self._extract_workflow_name(normalized)
        if workflow_name is None:
            return None
        return ParsedIntent(
            intent="run_workflow",
            utterance=utterance,
            confidence=0.92,
            workflow_name=workflow_name,
        )

    def _load_allowed_sites(self, whitelist_path: Path) -> list[AllowedSite]:
        if not whitelist_path.exists():
            self.logger.warning("Whitelist file not found at %s; open_site intent will be limited", whitelist_path)
            return []

        try:
            payload = json.loads(whitelist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.logger.warning("Whitelist file is invalid JSON at %s", whitelist_path)
            return []

        sites = payload.get("allowed_sites", [])
        output: list[AllowedSite] = []
        for item in sites:
            if isinstance(item, dict) and "name" in item and "url" in item:
                output.append(AllowedSite(name=str(item["name"]), url=str(item["url"])))
        return output

    def _parse_with_llm(self, utterance: str) -> ParsedIntent | None:
        if not self.settings.openai_api_key:
            return None

        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover
            self.logger.warning("OpenAI SDK unavailable for intent parsing, using fallback. Details: %s", exc)
            return None

        site_desc = ", ".join(f"{site.name} ({site.url})" for site in self.allowed_sites) or "none"
        schema_keys = [
            "intent", "utterance", "confidence", "reminder_text", "reminder_time", "calendar_title",
            "calendar_time", "note_text", "site_name", "site_url", "timer_seconds", "timer_label",
            "timer_target", "workflow_name", "reason",
        ]

        system_prompt = (
            "You are an intent extractor for a local voice assistant. "
            "Return strict JSON only with no markdown. "
            "Valid intents: add_reminder, list_reminders, add_calendar_event, upcoming_calendar, "
            "create_note, read_notes, open_site, set_timer, list_timers, cancel_timer, run_workflow, unknown. "
            "For workflow requests like 'run research' or 'start research workflow', return run_workflow "
            "with workflow_name set to the requested workflow name. "
            f"Allowed sites: {site_desc}. "
            "If the request is just opening the browser/chrome with no specific site, "
            "return intent open_site with site_name='browser' and site_url=null. "
            "For open_site intent, choose only from allowed sites; otherwise return unknown. "
            "Fill non-applicable fields with null. "
            f"Use exactly these keys: {', '.join(schema_keys)}"
        )

        client = OpenAI(api_key=self.settings.openai_api_key, timeout=self.settings.intent_timeout_sec)
        for attempt in range(1, 3):
            try:
                response = client.responses.create(
                    model=self.settings.intent_model,
                    max_output_tokens=260,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": utterance},
                    ],
                )
                raw = (response.output_text or "").strip()
                payload = self._extract_json(raw)
                if payload is None:
                    self.logger.warning("LLM intent output was not valid JSON, using fallback")
                    return None

                payload["utterance"] = utterance
                parsed = ParsedIntent.model_validate(payload)
                return self._enforce_open_site_whitelist(parsed)
            except (ValidationError, ValueError) as exc:
                self.logger.warning("LLM intent validation failed, using fallback. Details: %s", exc)
                return None
            except Exception as exc:  # pragma: no cover
                if attempt < 2:
                    self.logger.warning("LLM intent attempt %s/2 failed: %s", attempt, exc)
                    time.sleep(0.25)
                    continue
                self.logger.warning("LLM intent request failed, using fallback. Details: %s", exc)
                return None
        return None

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        if not text:
            return None
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return json.loads(stripped)

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return json.loads(stripped[start : end + 1])

    def _parse_with_fallback(self, utterance: str) -> ParsedIntent:
        normalized = self._normalize_text(utterance)

        workflow_name = self._extract_workflow_name(normalized)
        if workflow_name is not None:
            return ParsedIntent(
                intent="run_workflow",
                utterance=utterance,
                confidence=0.88,
                workflow_name=workflow_name,
            )

        if self._looks_like_list_reminders(normalized):
            return ParsedIntent(intent="list_reminders", utterance=utterance, confidence=0.84)

        if self._looks_like_add_reminder(normalized):
            reminder_text = self._extract_after_keywords(normalized, ["remind me to", "set reminder to", "add reminder"]) \
                or utterance
            return ParsedIntent(intent="add_reminder", utterance=utterance, confidence=0.78, reminder_text=reminder_text)

        if self._looks_like_upcoming_calendar(normalized):
            return ParsedIntent(intent="upcoming_calendar", utterance=utterance, confidence=0.86)

        if self._looks_like_add_calendar_event(normalized):
            title = self._extract_calendar_title(utterance)
            return ParsedIntent(
                intent="add_calendar_event",
                utterance=utterance,
                confidence=0.74,
                calendar_title=title,
            )

        if self._looks_like_read_notes(normalized):
            return ParsedIntent(intent="read_notes", utterance=utterance, confidence=0.86)

        if self._looks_like_create_note(normalized):
            note_text = self._extract_after_keywords(normalized, ["note that", "create note", "take a note", "add note"]) or utterance
            return ParsedIntent(intent="create_note", utterance=utterance, confidence=0.76, note_text=note_text)

        site = self._extract_site(normalized)
        if site is not None:
            return ParsedIntent(
                intent="open_site",
                utterance=utterance,
                confidence=0.88,
                site_name=site.name,
                site_url=site.url,
            )
        if self._is_open_request(normalized):
            return ParsedIntent(
                intent="unknown",
                utterance=utterance,
                confidence=0.66,
                reason="open_site_not_whitelisted",
            )

        if self._looks_like_cancel_timer(normalized):
            target = self._extract_after_keywords(normalized, ["cancel timer", "stop timer", "delete timer"])
            return ParsedIntent(intent="cancel_timer", utterance=utterance, confidence=0.83, timer_target=target)

        timer_seconds = self._parse_timer_seconds(normalized)
        if timer_seconds is not None:
            label = self._extract_timer_label(utterance)
            return ParsedIntent(
                intent="set_timer",
                utterance=utterance,
                confidence=0.9,
                timer_seconds=timer_seconds,
                timer_label=label,
            )

        if self._looks_like_list_timers(normalized):
            return ParsedIntent(intent="list_timers", utterance=utterance, confidence=0.88)

        return ParsedIntent(intent="unknown", utterance=utterance, confidence=0.45, reason="fallback_no_match")

    def _enforce_open_site_whitelist(self, intent: ParsedIntent) -> ParsedIntent:
        if intent.intent != "open_site":
            return intent

        requested_name = (intent.site_name or "").strip().lower()
        if requested_name in {"browser", "chrome", "google chrome"}:
            intent.site_name = "browser"
            intent.site_url = None
            return intent

        if intent.site_url:
            for site in self.allowed_sites:
                if intent.site_url.rstrip("/") == site.url.rstrip("/"):
                    if not intent.site_name:
                        intent.site_name = site.name
                    return intent

        if intent.site_name:
            matched = self._match_site_name(intent.site_name.lower())
            if matched is not None:
                intent.site_name = matched.name
                intent.site_url = matched.url
                return intent

        return ParsedIntent(
            intent="unknown",
            utterance=intent.utterance,
            confidence=0.5,
            reason="open_site_not_whitelisted",
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        lowered = text.lower()
        replacements = {
            "remider": "reminder",
            "remainder": "reminder",
            "remndr": "reminder",
            "calender": "calendar",
            "calandar": "calendar",
            "upcomming": "upcoming",
            "timr": "timer",
            "nots": "notes",
            "wikipdia": "wikipedia",
        }
        for src, dst in replacements.items():
            lowered = lowered.replace(src, dst)

        lowered = re.sub(r"[^a-z0-9:\/.\s]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    @staticmethod
    def _contains_any(text: str, phrases: list[str]) -> bool:
        return any(phrase in text for phrase in phrases)

    def _looks_like_list_reminders(self, text: str) -> bool:
        return self._contains_any(
            text,
            ["list reminders", "show reminders", "my reminders", "what reminders", "read reminders"],
        )

    def _looks_like_add_reminder(self, text: str) -> bool:
        return self._contains_any(text, ["remind me", "add reminder", "set reminder"]) and "list" not in text

    def _looks_like_upcoming_calendar(self, text: str) -> bool:
        return self._contains_any(
            text,
            ["upcoming calendar", "upcoming events", "next events", "what is on my calendar", "calendar today"],
        )

    def _looks_like_add_calendar_event(self, text: str) -> bool:
        has_event = self._contains_any(text, ["calendar event", "add event", "schedule", "book meeting"])
        has_time_hint = self._contains_any(text, [" at ", " tomorrow", " today", " next ", " on "])
        return has_event or ("calendar" in text and has_time_hint)

    def _looks_like_create_note(self, text: str) -> bool:
        return self._contains_any(text, ["create note", "add note", "take a note", "note that"]) and "read" not in text

    def _looks_like_read_notes(self, text: str) -> bool:
        return self._contains_any(text, ["read notes", "show notes", "list notes", "my notes"])

    def _looks_like_cancel_timer(self, text: str) -> bool:
        return self._contains_any(text, ["cancel timer", "stop timer", "delete timer", "clear timer"])

    def _looks_like_list_timers(self, text: str) -> bool:
        return self._contains_any(text, ["list timers", "show timers", "active timers", "what timers", "my timers"])

    def _extract_site(self, normalized_utterance: str) -> AllowedSite | None:
        if not self._is_open_request(normalized_utterance):
            return None

        if self._contains_any(
            normalized_utterance,
            [
                "open browser",
                "open chrome",
                "launch browser",
                "launch chrome",
                "opening browser",
                "opening chrome",
            ],
        ):
            return AllowedSite(name="browser", url="")

        for site in self.allowed_sites:
            name = site.name.lower()
            host = urlparse(site.url).netloc.lower().replace("www.", "")
            if name in normalized_utterance or host in normalized_utterance:
                return site

        tokens = normalized_utterance.split()
        for token in tokens:
            matched = self._match_site_name(token)
            if matched is not None:
                return matched

        return None

    def _is_open_request(self, normalized_utterance: str) -> bool:
        return self._contains_any(normalized_utterance, ["open", "go to", "navigate", "launch"])

    @staticmethod
    def _extract_workflow_name(text: str) -> str | None:
        patterns = [
            r"^(?:run|start|open)\s+(.+?)\s+workflow$",
            r"^(?:run|start)\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, text)
            if not match:
                continue
            name = match.group(1).strip().strip(".,!?;:")
            if not name or name in {"a", "the", "workflow"}:
                continue
            return name
        return None

    def _match_site_name(self, candidate: str) -> AllowedSite | None:
        names = [s.name.lower() for s in self.allowed_sites]
        match = get_close_matches(candidate, names, n=1, cutoff=0.78)
        if not match:
            return None
        target = match[0]
        for site in self.allowed_sites:
            if site.name.lower() == target:
                return site
        return None

    @staticmethod
    def _extract_after_keywords(text: str, keywords: list[str]) -> str | None:
        for keyword in keywords:
            if keyword in text:
                part = text.split(keyword, 1)[1].strip()
                return part if part else None
        return None

    @staticmethod
    def _parse_timer_seconds(text: str) -> int | None:
        if "timer" not in text:
            return None

        if "half an hour" in text:
            return 30 * 60

        words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "fifteen": 15,
            "twenty": 20,
            "thirty": 30,
            "forty": 40,
            "fifty": 50,
            "sixty": 60,
        }

        match = re.search(r"(\d+|[a-z]+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)", text)
        if not match:
            return None

        raw_num, unit = match.group(1), match.group(2)
        value = int(raw_num) if raw_num.isdigit() else words.get(raw_num)
        if value is None:
            return None

        if unit.startswith("sec"):
            return value
        if unit.startswith("min"):
            return value * 60
        if unit.startswith("hour") or unit.startswith("hr"):
            return value * 3600
        return None

    @staticmethod
    def _extract_timer_label(utterance: str) -> str | None:
        lowered = utterance.lower()
        markers = [" called ", " named ", " for "]
        for marker in markers:
            if marker in lowered:
                idx = lowered.index(marker)
                part = utterance[idx + len(marker) :].strip()
                return part if part else None
        return None

    @staticmethod
    def _extract_calendar_title(utterance: str) -> str:
        lowered = utterance.lower()
        for marker in ["add event", "schedule", "book meeting", "calendar event"]:
            if marker in lowered:
                idx = lowered.index(marker) + len(marker)
                title = utterance[idx:].strip(" :")
                if title:
                    return title
        return utterance
