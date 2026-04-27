"""Reminder/Calendar integration via EventKit (PyObjC)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.integrations.base import ActionResult


@dataclass
class ParsedDateResult:
    dt: datetime | None
    parse_note: str | None = None


class EventKitAdapter:
    """Interacts with Reminders and Calendar through EventKit."""

    def __init__(self, timezone_name: str = "America/Toronto") -> None:
        self.timezone_name = timezone_name

        try:
            import EventKit
            import Foundation
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"PyObjC EventKit not available: {exc}") from exc

        self.EventKit = EventKit
        self.Foundation = Foundation
        self.store = EventKit.EKEventStore.alloc().init()

    def add_reminder(self, title: str, when_text: str | None = None) -> ActionResult:
        granted, err = self._request_access(self.EventKit.EKEntityTypeReminder)
        if not granted:
            return ActionResult(success=False, spoken_response="I don't have Reminders permission.", error=err)

        reminder = self.EventKit.EKReminder.reminderWithEventStore_(self.store)
        reminder.setCalendar_(self.store.defaultCalendarForNewReminders())
        reminder.setTitle_(title.strip() or "Reminder")

        parsed = self._parse_datetime_text(when_text)
        if parsed.dt is not None:
            reminder.setDueDateComponents_(self._to_date_components(parsed.dt))

        ok, save_error = self._save_reminder(reminder)
        if not ok:
            return ActionResult(success=False, spoken_response="I couldn't add that reminder.", error=save_error)

        note = f" {parsed.parse_note}" if parsed.parse_note else ""
        return ActionResult(success=True, spoken_response=f"Reminder added.{note}".strip())

    def list_upcoming_reminders(self, limit: int = 5) -> ActionResult:
        granted, err = self._request_access(self.EventKit.EKEntityTypeReminder)
        if not granted:
            return ActionResult(success=False, spoken_response="I don't have Reminders permission.", error=err)

        start = self.Foundation.NSDate.date()
        end = self.Foundation.NSDate.dateWithTimeIntervalSinceNow_(60 * 60 * 24 * 30)
        predicate = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(start, end, None)

        done = threading.Event()
        holder: dict[str, object] = {"reminders": None}

        def completion(reminders):
            holder["reminders"] = reminders
            done.set()

        self.store.fetchRemindersMatchingPredicate_completion_(predicate, completion)
        if not done.wait(timeout=10):
            return ActionResult(success=False, spoken_response="Fetching reminders timed out.", error="timeout")

        reminders = list(holder.get("reminders") or [])
        reminders = reminders[: max(1, min(limit, 20))]

        items: list[str] = []
        for item in reminders:
            title = str(item.title() or "Untitled")
            due_dt = self._reminder_due_datetime(item)
            if due_dt is None:
                items.append(title)
            else:
                items.append(f"{title} at {due_dt.strftime('%Y-%m-%d %H:%M')}")

        if not items:
            return ActionResult(success=True, spoken_response="You have no upcoming reminders.", data={"reminders": []})

        return ActionResult(success=True, spoken_response=f"You have {len(items)} upcoming reminders.", data={"reminders": items})

    def add_calendar_event(self, title: str, when_text: str | None = None, duration_min: int = 30) -> ActionResult:
        granted, err = self._request_access(self.EventKit.EKEntityTypeEvent)
        if not granted:
            return ActionResult(success=False, spoken_response="I don't have Calendar permission.", error=err)

        parsed = self._parse_datetime_text(when_text)
        start_dt = parsed.dt or (datetime.now(ZoneInfo(self.timezone_name)) + timedelta(minutes=5))
        end_dt = start_dt + timedelta(minutes=max(5, duration_min))

        event = self.EventKit.EKEvent.eventWithEventStore_(self.store)
        event.setCalendar_(self.store.defaultCalendarForNewEvents())
        event.setTitle_(title.strip() or "Pluto Event")
        event.setStartDate_(self.Foundation.NSDate.dateWithTimeIntervalSince1970_(start_dt.timestamp()))
        event.setEndDate_(self.Foundation.NSDate.dateWithTimeIntervalSince1970_(end_dt.timestamp()))

        ok, save_error = self._save_event(event)
        if not ok:
            return ActionResult(success=False, spoken_response="I couldn't add that calendar event.", error=save_error)

        note = f" {parsed.parse_note}" if parsed.parse_note else ""
        return ActionResult(success=True, spoken_response=f"Calendar event added.{note}".strip())

    def list_next_events(self, limit: int = 5) -> ActionResult:
        granted, err = self._request_access(self.EventKit.EKEntityTypeEvent)
        if not granted:
            return ActionResult(success=False, spoken_response="I don't have Calendar permission.", error=err)

        start = self.Foundation.NSDate.date()
        end = self.Foundation.NSDate.dateWithTimeIntervalSinceNow_(60 * 60 * 24 * 30)
        predicate = self.store.predicateForEventsWithStartDate_endDate_calendars_(start, end, None)
        events = list(self.store.eventsMatchingPredicate_(predicate) or [])
        events.sort(key=lambda e: float(e.startDate().timeIntervalSince1970()))

        trimmed = events[: max(1, min(limit, 20))]
        items: list[str] = []
        for event in trimmed:
            title = str(event.title() or "Untitled")
            start_dt = datetime.fromtimestamp(float(event.startDate().timeIntervalSince1970()), ZoneInfo(self.timezone_name))
            items.append(f"{title} at {start_dt.strftime('%Y-%m-%d %H:%M')}")

        if not items:
            return ActionResult(success=True, spoken_response="No upcoming calendar events found.", data={"events": []})

        return ActionResult(success=True, spoken_response=f"You have {len(items)} upcoming events.", data={"events": items})

    def get_due_reminders(self, lookahead_min: int = 15) -> list[dict]:
        granted, _ = self._request_access(self.EventKit.EKEntityTypeReminder)
        if not granted:
            return []

        start = self.Foundation.NSDate.date()
        end = self.Foundation.NSDate.dateWithTimeIntervalSinceNow_(max(1, lookahead_min) * 60)
        predicate = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(start, end, None)

        done = threading.Event()
        holder: dict[str, object] = {"reminders": None}

        def completion(reminders):
            holder["reminders"] = reminders
            done.set()

        self.store.fetchRemindersMatchingPredicate_completion_(predicate, completion)
        if not done.wait(timeout=10):
            return []

        items = []
        for reminder in list(holder.get("reminders") or []):
            due = self._reminder_due_datetime(reminder)
            if due is None:
                continue
            rid = self._reminder_identifier(reminder)
            items.append(
                {
                    "id": rid,
                    "title": str(reminder.title() or "Untitled"),
                    "due_epoch": due.timestamp(),
                }
            )
        items.sort(key=lambda x: x["due_epoch"])
        return items

    def get_upcoming_events(self, lookahead_min: int = 30) -> list[dict]:
        granted, _ = self._request_access(self.EventKit.EKEntityTypeEvent)
        if not granted:
            return []

        start = self.Foundation.NSDate.date()
        end = self.Foundation.NSDate.dateWithTimeIntervalSinceNow_(max(1, lookahead_min) * 60)
        predicate = self.store.predicateForEventsWithStartDate_endDate_calendars_(start, end, None)
        events = list(self.store.eventsMatchingPredicate_(predicate) or [])
        events.sort(key=lambda e: float(e.startDate().timeIntervalSince1970()))

        items = []
        for event in events:
            start_epoch = float(event.startDate().timeIntervalSince1970())
            items.append(
                {
                    "id": self._event_identifier(event),
                    "title": str(event.title() or "Untitled"),
                    "start_epoch": start_epoch,
                }
            )
        return items

    def _request_access(self, entity_type: int) -> tuple[bool, str | None]:
        done = threading.Event()
        state: dict[str, object] = {"granted": False, "error": None}

        def completion(granted, error):
            state["granted"] = bool(granted)
            state["error"] = str(error) if error is not None else None
            done.set()

        try:
            if entity_type == self.EventKit.EKEntityTypeReminder and hasattr(
                self.store, "requestFullAccessToRemindersWithCompletion_"
            ):
                self.store.requestFullAccessToRemindersWithCompletion_(completion)
            elif entity_type == self.EventKit.EKEntityTypeEvent and hasattr(
                self.store, "requestFullAccessToEventsWithCompletion_"
            ):
                self.store.requestFullAccessToEventsWithCompletion_(completion)
            else:
                self.store.requestAccessToEntityType_completion_(entity_type, completion)
        except Exception as exc:
            return False, str(exc)

        if not done.wait(timeout=10):
            return False, "permission_timeout"

        return bool(state["granted"]), state["error"] if not state["granted"] else None

    def _save_reminder(self, reminder) -> tuple[bool, str | None]:
        candidates = [
            lambda: self.store.saveReminder_commit_error_(reminder, True, None),
        ]
        return self._invoke_save(candidates)

    def _save_event(self, event) -> tuple[bool, str | None]:
        candidates = [
            lambda: self.store.saveEvent_span_commit_error_(event, self.EventKit.EKSpanThisEvent, True, None),
            lambda: self.store.saveEvent_span_error_(event, self.EventKit.EKSpanThisEvent, None),
        ]
        return self._invoke_save(candidates)

    def _invoke_save(self, candidates) -> tuple[bool, str | None]:
        last_error: str | None = None
        for fn in candidates:
            try:
                result = fn()
            except Exception as exc:
                last_error = str(exc)
                continue

            if isinstance(result, tuple) and len(result) >= 1:
                ok = bool(result[0])
                err = str(result[1]) if len(result) > 1 and result[1] is not None else None
                return ok, err
            if isinstance(result, bool):
                return result, None if result else "save_failed"
            return True, None

        return False, last_error or "save_failed"

    def _parse_datetime_text(self, text: str | None) -> ParsedDateResult:
        if not text:
            return ParsedDateResult(dt=None)

        raw = text.strip().lower()
        if not raw:
            return ParsedDateResult(dt=None)

        now = datetime.now(ZoneInfo(self.timezone_name)).replace(second=0, microsecond=0)

        import re

        m = re.search(r"in\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)", raw)
        if m:
            qty = int(m.group(1))
            unit = m.group(2)
            delta = timedelta(minutes=qty) if unit.startswith("min") else timedelta(hours=qty)
            return ParsedDateResult(dt=now + delta)

        m = re.search(r"tomorrow(?:\s+at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = m.group(3)
            if ampm:
                if ampm == "pm" and hour < 12:
                    hour += 12
                if ampm == "am" and hour == 12:
                    hour = 0
            target = (now + timedelta(days=1)).replace(hour=hour % 24, minute=minute)
            return ParsedDateResult(dt=target)

        m = re.search(r"(?:today\s+at\s+|at\s+)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = m.group(3)
            if ampm:
                if ampm == "pm" and hour < 12:
                    hour += 12
                if ampm == "am" and hour == 12:
                    hour = 0
            target = now.replace(hour=hour % 24, minute=minute)
            if target < now:
                target += timedelta(days=1)
            return ParsedDateResult(dt=target)

        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(raw, fmt).replace(tzinfo=ZoneInfo(self.timezone_name))
                return ParsedDateResult(dt=parsed)
            except ValueError:
                continue

        return ParsedDateResult(dt=None, parse_note="I could not parse a date/time, so I used a default time.")

    def _to_date_components(self, dt: datetime):
        comp = self.Foundation.NSDateComponents.alloc().init()
        comp.setYear_(dt.year)
        comp.setMonth_(dt.month)
        comp.setDay_(dt.day)
        comp.setHour_(dt.hour)
        comp.setMinute_(dt.minute)
        return comp

    def _reminder_due_datetime(self, reminder) -> datetime | None:
        comp = reminder.dueDateComponents()
        if comp is None:
            return None

        date_value = comp.date()
        if date_value is None:
            return None

        try:
            ts = float(date_value.timeIntervalSince1970())
            return datetime.fromtimestamp(ts, ZoneInfo(self.timezone_name))
        except Exception:
            return None

    @staticmethod
    def _reminder_identifier(reminder) -> str:
        try:
            rid = reminder.calendarItemIdentifier()
            if rid:
                return str(rid)
        except Exception:
            pass
        return f"rem-{hash(str(reminder.title()))}"

    @staticmethod
    def _event_identifier(event) -> str:
        try:
            eid = event.calendarItemIdentifier()
            if eid:
                return str(eid)
        except Exception:
            pass
        return f"evt-{hash(str(event.title()))}-{int(float(event.startDate().timeIntervalSince1970()))}"
