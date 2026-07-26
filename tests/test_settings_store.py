"""SettingsStore: real filesystem under tmp (TDD Part B)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from settings_store import DEFAULTS, SettingsStore, public_settings


class TestSettingsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "settings.json")
        self.store = SettingsStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_when_missing_file(self):
        data = self.store.get()
        self.assertEqual(data["model_backend"], DEFAULTS["model_backend"])
        self.assertEqual(data["max_steps"], 15)
        self.assertEqual(data["idle_timeout"], 60.0)
        self.assertEqual(data["control_ttl_s"], 120)
        self.assertIsNone(data["default_screen_id"])

    def test_put_model_and_get(self):
        self.store.save({"model": "test-model-x"})
        self.assertEqual(SettingsStore(self.path).get()["model"], "test-model-x")

    def test_put_each_field(self):
        self.store.save({
            "model_backend": "holo",
            "base_url": "http://example/v1",
            "model": "m1",
            "api_key": "secret",
            "max_steps": 7,
            "idle_timeout": 30.0,
            "control_ttl_s": 45,
            "default_screen_id": "scr-abc",
        })
        d = SettingsStore(self.path).get()
        self.assertEqual(d["model_backend"], "holo")
        self.assertEqual(d["base_url"], "http://example/v1")
        self.assertEqual(d["model"], "m1")
        self.assertEqual(d["api_key"], "secret")
        self.assertEqual(d["max_steps"], 7)
        self.assertEqual(d["idle_timeout"], 30.0)
        self.assertEqual(d["control_ttl_s"], 45)
        self.assertEqual(d["default_screen_id"], "scr-abc")
        with open(self.path) as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["model"], "m1")

    def test_public_redacts_api_key(self):
        self.store.save({"api_key": "sekrit"})
        pub = public_settings(self.store.get())
        self.assertEqual(pub["api_key"], "")
        self.assertTrue(pub["api_key_set"])

    def test_bad_backend_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save({"model_backend": "nope"})

    def test_preset_apply(self):
        out = self.store.apply_preset("claude-bench")
        self.assertEqual(out["model_backend"], "generic")
        self.assertIn("claude", out["model"].lower())

    def test_unknown_preset(self):
        with self.assertRaises(KeyError):
            self.store.apply_preset("does-not-exist")


if __name__ == "__main__":
    unittest.main()
