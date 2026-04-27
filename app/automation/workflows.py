"""Config-defined workflow execution for Pluto."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from app.integrations.base import ActionResult
from app.intent.schema import ParsedIntent


RISKY_INTENTS = {"add_reminder", "add_calendar_event"}
SUPPORTED_STEP_INTENTS = {
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
    "collect_research",
}


@dataclass(frozen=True)
class WorkflowStep:
    intent: str
    label: str
    fields: dict


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    aliases: list[str]
    description: str
    steps: list[WorkflowStep]


class WorkflowConfigError(ValueError):
    """Raised when workflow configuration is missing or invalid."""


class WorkflowRunner:
    """Loads and runs local workflow definitions."""

    def __init__(self, path: Path, timezone_name: str) -> None:
        self.path = path
        self.timezone_name = timezone_name

    def find_workflow(self, name_or_alias: str | None) -> WorkflowDefinition | None:
        query = (name_or_alias or "").strip().lower().strip(".,!?;:")
        if not query:
            return None

        for workflow in self.load_workflows():
            candidates = {workflow.name.lower(), *(alias.lower() for alias in workflow.aliases)}
            if query in candidates:
                return workflow
        return None

    def load_workflows(self) -> list[WorkflowDefinition]:
        if not self.path.exists():
            raise WorkflowConfigError(f"workflow config not found: {self.path}")

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkflowConfigError(f"workflow config JSON invalid: {exc}") from exc

        raw_workflows = payload.get("workflows")
        if not isinstance(raw_workflows, list) or not raw_workflows:
            raise WorkflowConfigError("workflow config must contain a non-empty workflows list")

        workflows: list[WorkflowDefinition] = []
        seen: set[str] = set()
        for idx, raw in enumerate(raw_workflows):
            if not isinstance(raw, dict):
                raise WorkflowConfigError(f"workflow {idx} must be an object")

            name = str(raw.get("name", "")).strip()
            if not name:
                raise WorkflowConfigError(f"workflow {idx} has empty name")
            if name.lower() in seen:
                raise WorkflowConfigError(f"duplicate workflow name: {name}")
            seen.add(name.lower())

            aliases_raw = raw.get("aliases", [])
            if not isinstance(aliases_raw, list):
                raise WorkflowConfigError(f"workflow {name} aliases must be a list")
            aliases = [str(alias).strip() for alias in aliases_raw if str(alias).strip()]

            steps_raw = raw.get("steps")
            if not isinstance(steps_raw, list) or not steps_raw:
                raise WorkflowConfigError(f"workflow {name} must contain at least one step")

            steps = [self._parse_step(name, step_idx, step) for step_idx, step in enumerate(steps_raw)]
            workflows.append(
                WorkflowDefinition(
                    name=name,
                    aliases=aliases,
                    description=str(raw.get("description", "")).strip(),
                    steps=steps,
                )
            )

        return workflows

    def needs_confirmation(self, workflow: WorkflowDefinition) -> bool:
        return any(step.intent in RISKY_INTENTS for step in workflow.steps)

    def confirmation_prompt(self, workflow: WorkflowDefinition) -> str:
        counts: dict[str, int] = {}
        labels = {
            "add_reminder": "reminder",
            "add_calendar_event": "calendar event",
        }
        for step in workflow.steps:
            if step.intent in RISKY_INTENTS:
                label = labels[step.intent]
                counts[label] = counts.get(label, 0) + 1

        rendered = []
        for label, count in counts.items():
            noun = label if count == 1 else f"{label}s"
            rendered.append(f"{count} {noun}")

        summary = " and ".join(rendered) if rendered else "risky changes"
        return f"This workflow will add {summary}. Say yes to run it or no to cancel."

    def run(
        self,
        workflow: WorkflowDefinition,
        execute_step: Callable[[ParsedIntent], ActionResult],
    ) -> ActionResult:
        completed: list[dict] = []
        for idx, step in enumerate(workflow.steps, start=1):
            intent = self._step_to_intent(workflow, step)
            result = execute_step(intent)
            completed.append(
                {
                    "step": idx,
                    "label": step.label,
                    "intent": step.intent,
                    "success": result.success,
                    "error": result.error,
                }
            )
            if not result.success:
                return ActionResult(
                    success=False,
                    spoken_response=f"Workflow {workflow.name} stopped at step {idx}: {step.label}.",
                    error=result.error or "workflow_step_failed",
                    data={"workflow": workflow.name, "completed_steps": completed},
                )

        return ActionResult(
            success=True,
            spoken_response=f"Workflow {workflow.name} completed.",
            data={"workflow": workflow.name, "completed_steps": completed},
        )

    def _parse_step(self, workflow_name: str, step_idx: int, raw: object) -> WorkflowStep:
        if not isinstance(raw, dict):
            raise WorkflowConfigError(f"workflow {workflow_name} step {step_idx} must be an object")

        intent = str(raw.get("intent", "")).strip()
        if intent not in SUPPORTED_STEP_INTENTS:
            raise WorkflowConfigError(f"workflow {workflow_name} step {step_idx} has unsupported intent: {intent}")

        label = str(raw.get("label", "")).strip() or intent.replace("_", " ")
        fields = {key: value for key, value in raw.items() if key not in {"intent", "label"}}
        fields["intent"] = intent
        fields["utterance"] = f"workflow {workflow_name}: {label}"
        fields["confidence"] = 1.0
        return WorkflowStep(intent=intent, label=label, fields=fields)

    def _step_to_intent(self, workflow: WorkflowDefinition, step: WorkflowStep) -> ParsedIntent:
        payload = dict(step.fields)
        if isinstance(payload.get("note_text"), str):
            payload["note_text"] = self._render_template(payload["note_text"], workflow)
        return ParsedIntent.model_validate(payload)

    def _render_template(self, value: str, workflow: WorkflowDefinition) -> str:
        now = datetime.now(ZoneInfo(self.timezone_name))
        replacements = {
            "workflow_name": workflow.name,
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "datetime": now.strftime("%Y-%m-%d %H:%M"),
        }
        rendered = value
        for key, replacement in replacements.items():
            rendered = rendered.replace("{" + key + "}", replacement)
        return rendered
