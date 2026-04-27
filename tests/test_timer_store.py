from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from app.state.timer_store import TimerStore


class TimerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp_dir.name) / "timers.json"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_persistence_across_restarts(self) -> None:
        store = TimerStore(self.path)
        t = store.set_timer(duration_seconds=120, label="tea")
        self.assertTrue(self.path.exists())

        reloaded = TimerStore(self.path)
        active = reloaded.list_active()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].timer_id, t.timer_id)
        self.assertEqual(active[0].label, "tea")

    def test_cancel_by_name_or_id(self) -> None:
        store = TimerStore(self.path)
        t1 = store.set_timer(duration_seconds=100, label="tea")
        _ = store.set_timer(duration_seconds=200, label="pasta")

        by_name = store.cancel("tea")
        self.assertIsNotNone(by_name)
        self.assertEqual(by_name.timer_id, t1.timer_id)

        active = store.list_active()
        self.assertEqual(len(active), 1)

        by_id = store.cancel(active[0].timer_id)
        self.assertIsNotNone(by_id)
        self.assertEqual(len(store.list_active()), 0)

    def test_pop_due(self) -> None:
        store = TimerStore(self.path)
        _ = store.set_timer(duration_seconds=1, label="quick")
        time.sleep(1.2)
        due = store.pop_due()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].label, "quick")
        self.assertEqual(len(store.list_active()), 0)


if __name__ == "__main__":
    unittest.main()
