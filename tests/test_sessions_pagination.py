"""SessionStore list filters (Part E)."""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from desktop_use import ui as ui_mod


class TestSessionPagination(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = ui_mod.SESS_DIR
        ui_mod.SESS_DIR = self.tmp.name
        self.store = ui_mod.SessionStore()

    def tearDown(self):
        ui_mod.SESS_DIR = self._old
        self.tmp.cleanup()

    def _mk(self, task, status="complete", model="m"):
        meta = self.store.create(task, model)
        self.store.update(meta["id"], status=status, ended=time.time())
        # ensure distinct started order
        time.sleep(0.01)
        return meta

    def test_limit_offset(self):
        for i in range(5):
            self._mk(f"task-{i}")
        rows, total = self.store.list_page(limit=2, offset=0)
        self.assertEqual(total, 5)
        self.assertEqual(len(rows), 2)
        rows2, _ = self.store.list_page(limit=2, offset=2)
        self.assertEqual(len(rows2), 2)
        ids = {r["id"] for r in rows + rows2}
        self.assertEqual(len(ids), 4)

    def test_status_filter(self):
        self._mk("a", status="complete")
        self._mk("b", status="error")
        rows, total = self.store.list_page(status="error")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["status"], "error")

    def test_q_filter(self):
        self._mk("open chrome browser")
        self._mk("type into terminal")
        rows, total = self.store.list_page(q="chrome")
        self.assertEqual(total, 1)
        self.assertIn("chrome", rows[0]["task"])


if __name__ == "__main__":
    unittest.main()
