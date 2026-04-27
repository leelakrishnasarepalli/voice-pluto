"""Intent execution dispatch to local macOS integrations."""

from __future__ import annotations

import logging

from app.alerts.voice import speak
from app.automation.research import ResearchCollector, parse_sources
from app.automation.workflows import WorkflowConfigError, WorkflowDefinition, WorkflowRunner
from app.config import PlutoSettings
from app.intent.schema import ParsedIntent
from app.integrations.base import ActionResult
from app.integrations.chrome_adapter import ChromeAdapter
from app.integrations.eventkit_adapter import EventKitAdapter
from app.integrations.notes_adapter import NotesAdapter
from app.state.timer_store import TimerStore


class AssistantExecutor:
    """Executes parsed intents through integration adapters."""

    def __init__(self, settings: PlutoSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

        self.chrome = ChromeAdapter(settings.whitelist_path)
        self.notes = NotesAdapter()
        self.timers = TimerStore(settings.timers_state_path)
        self.workflows = WorkflowRunner(settings.workflows_path, settings.timezone)
        self.research = ResearchCollector(settings, self.notes, logger=logger)

        try:
            self.eventkit = EventKitAdapter(settings.timezone)
        except Exception as exc:
            self.eventkit = None
            self.logger.warning("EventKit adapter unavailable: %s", exc)

    def execute(self, intent: ParsedIntent) -> ActionResult:
        self.logger.info("Executing intent=%s confidence=%.2f", intent.intent, intent.confidence)
        try:
            result = self._execute_intent(intent)
        except Exception as exc:
            self.logger.exception("Execution error")
            result = ActionResult(success=False, spoken_response="I hit an execution error.", error=str(exc))

        speak(result.spoken_response, self.settings)
        self.logger.info("Execution finished success=%s error=%s", result.success, result.error)
        return result

    def workflow_for_intent(self, intent: ParsedIntent) -> WorkflowDefinition | None:
        if intent.intent != "run_workflow":
            return None
        return self.workflows.find_workflow(intent.workflow_name)

    def workflow_needs_confirmation(self, workflow: WorkflowDefinition) -> bool:
        return self.workflows.needs_confirmation(workflow)

    def workflow_confirmation_prompt(self, workflow: WorkflowDefinition) -> str:
        return self.workflows.confirmation_prompt(workflow)

    def execute_confirmed_workflow(self, workflow: WorkflowDefinition) -> ActionResult:
        result = self.workflows.run(workflow, self._execute_intent)
        speak(result.spoken_response, self.settings)
        self.logger.info("Confirmed workflow finished success=%s error=%s", result.success, result.error)
        return result

    def _execute_intent(self, intent: ParsedIntent) -> ActionResult:
        if intent.intent == "add_reminder":
            if not self.eventkit:
                return ActionResult(success=False, spoken_response="Reminders integration is unavailable.", error="eventkit_unavailable")
            title = (intent.reminder_text or intent.utterance).strip()
            return self.eventkit.add_reminder(title=title, when_text=intent.reminder_time)

        if intent.intent == "list_reminders":
            if not self.eventkit:
                return ActionResult(success=False, spoken_response="Reminders integration is unavailable.", error="eventkit_unavailable")
            return self.eventkit.list_upcoming_reminders(limit=5)

        if intent.intent == "add_calendar_event":
            if not self.eventkit:
                return ActionResult(success=False, spoken_response="Calendar integration is unavailable.", error="eventkit_unavailable")
            title = (intent.calendar_title or intent.utterance).strip()
            return self.eventkit.add_calendar_event(title=title, when_text=intent.calendar_time, duration_min=30)

        if intent.intent == "upcoming_calendar":
            if not self.eventkit:
                return ActionResult(success=False, spoken_response="Calendar integration is unavailable.", error="eventkit_unavailable")
            return self.eventkit.list_next_events(limit=5)

        if intent.intent == "create_note":
            note_text = (intent.note_text or intent.utterance).strip()
            return self.notes.create_note(note_text)

        if intent.intent == "read_notes":
            return self.notes.read_recent_notes(limit=5)

        if intent.intent == "open_site":
            return self.chrome.open_site(
                site_name=intent.site_name,
                site_url=intent.site_url,
                utterance=intent.utterance,
            )

        if intent.intent == "set_timer":
            duration = intent.timer_seconds
            if not duration:
                return ActionResult(success=False, spoken_response="I need a timer duration.", error="missing_timer_duration")

            label = intent.timer_label or "timer"
            timer = self.timers.set_timer(duration_seconds=duration, label=label)
            return ActionResult(
                success=True,
                spoken_response=f"Timer set for {duration} seconds. ID {timer.timer_id}.",
                data={"timer_id": timer.timer_id, "label": timer.label, "duration_seconds": duration},
            )

        if intent.intent == "list_timers":
            active = self.timers.list_active()
            if not active:
                return ActionResult(success=True, spoken_response="No active timers.", data={"timers": []})
            rendered = [
                {
                    "timer_id": t.timer_id,
                    "label": t.label,
                    "remaining_seconds": t.remaining_seconds(),
                }
                for t in active
            ]
            return ActionResult(
                success=True,
                spoken_response=f"You have {len(rendered)} active timers.",
                data={"timers": rendered},
            )

        if intent.intent == "cancel_timer":
            target = intent.timer_target or intent.timer_label
            if target is None:
                lowered = intent.utterance.lower()
                for marker in ("cancel timer", "stop timer", "delete timer", "clear timer"):
                    if marker in lowered:
                        target = intent.utterance[lowered.index(marker) + len(marker) :].strip()
                        break
            cancelled = self.timers.cancel(target)
            if not cancelled:
                return ActionResult(success=False, spoken_response="I couldn't find that timer.", error="timer_not_found")
            return ActionResult(
                success=True,
                spoken_response=f"Cancelled timer {cancelled.label}.",
                data={"timer_id": cancelled.timer_id, "label": cancelled.label},
            )

        if intent.intent == "collect_research":
            speak("Starting research. This can take a minute.", self.settings)
            result = self.research.collect_to_note(
                intent.sources,
                limit_per_source=intent.limit_per_source or 5,
                read_mode=intent.read_mode or "concise",
            )
            if not result.success:
                return result

            if intent.read_aloud:
                digest = str(result.data.get("spoken_digest", "")).strip()
                if digest:
                    speak(digest, self.settings)

            open_errors = []
            if intent.open_sources:
                for source in parse_sources(intent.sources):
                    opened = self.chrome.open_site(site_name=source.name, site_url=source.url, utterance=f"open {source.name}")
                    if not opened.success:
                        open_errors.append({"source": source.name, "error": opened.error})
                if open_errors:
                    result.data["open_errors"] = open_errors
            return result

        if intent.intent == "run_workflow":
            try:
                workflow = self.workflow_for_intent(intent)
            except WorkflowConfigError as exc:
                return ActionResult(success=False, spoken_response="Workflow configuration is invalid.", error=str(exc))
            if workflow is None:
                return ActionResult(success=False, spoken_response="I couldn't find that workflow.", error="workflow_not_found")
            if self.workflows.needs_confirmation(workflow):
                return ActionResult(
                    success=False,
                    spoken_response=self.workflows.confirmation_prompt(workflow),
                    error="workflow_confirmation_required",
                    data={"workflow": workflow.name},
                )
            return self.workflows.run(workflow, self._execute_intent)

        if intent.reason == "open_site_not_whitelisted":
            return ActionResult(
                success=False,
                spoken_response="That site is blocked. I can only open whitelisted sites.",
                error="non_whitelisted_site",
            )

        return ActionResult(success=False, spoken_response="I didn't understand that command.", error="unknown_intent")
