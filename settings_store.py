"""Settings defaults for new work (not mid-flight session rewrite).

Precedence when applied at process/boot or /run:
  explicit CLI flag > env > settings.json > hardcoded default
"""

from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from typing import Any

DEFAULTS: dict[str, Any] = {
    "model_backend": "auto",
    "base_url": "http://localhost:11434/v1",
    "model": "qwen2.5vl",
    "api_key": "",
    "max_steps": 15,
    "idle_timeout": 60.0,
    "control_ttl_s": 120,
    "default_screen_id": None,
    "presets": [
        {
            "id": "holo",
            "label": "Holo production",
            "model_backend": "holo",
            "model": "holo-2",
            "base_url": "",
        },
        {
            "id": "generic-a",
            "label": "Generic / OpenRouter A",
            "model_backend": "generic",
            "model": "qwen/qwen2.5-vl-72b-instruct",
            "base_url": "https://openrouter.ai/api/v1",
        },
        {
            "id": "generic-b",
            "label": "Generic slot B",
            "model_backend": "generic",
            "model": "qwen2.5vl",
            "base_url": "http://localhost:11434/v1",
        },
        {
            "id": "claude-bench",
            "label": "Claude bench",
            "model_backend": "generic",
            "model": "anthropic/claude-sonnet-4",
            "base_url": "https://openrouter.ai/api/v1",
        },
    ],
}

_WRITABLE = frozenset({
    "model_backend", "base_url", "model", "api_key",
    "max_steps", "idle_timeout", "control_ttl_s", "default_screen_id",
    "presets",
})


class SettingsStore:
    """Load/save settings.json under a root directory."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self._data: dict[str, Any] = deepcopy(DEFAULTS)
        self.load()

    def load(self) -> dict[str, Any]:
        with self.lock:
            if os.path.isfile(self.path):
                try:
                    with open(self.path) as f:
                        raw = json.load(f)
                    if isinstance(raw, dict):
                        self._data = self._merge(DEFAULTS, raw)
                except (OSError, json.JSONDecodeError):
                    self._data = deepcopy(DEFAULTS)
            else:
                self._data = deepcopy(DEFAULTS)
            return deepcopy(self._data)

    def get(self) -> dict[str, Any]:
        with self.lock:
            return deepcopy(self._data)

    def _persist_unlocked(self) -> dict[str, Any]:
        """Write self._data to disk. Caller must hold self.lock."""
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)
        return deepcopy(self._data)

    def save(self, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            if patch:
                cleaned = {k: v for k, v in patch.items() if k in _WRITABLE}
                self._data = self._merge(self._data, cleaned)
                self._validate(self._data)
            return self._persist_unlocked()

    def apply_preset(self, preset_id: str) -> dict[str, Any]:
        with self.lock:
            presets = self._data.get("presets") or []
            match = next((p for p in presets if p.get("id") == preset_id), None)
            if match is None:
                raise KeyError(f"unknown preset: {preset_id}")
            for key in ("model_backend", "model", "base_url"):
                if key in match and match[key] not in (None, ""):
                    self._data[key] = match[key]
            self._validate(self._data)
            return self._persist_unlocked()

    @staticmethod
    def _merge(base: dict, patch: dict) -> dict:
        out = deepcopy(base)
        for k, v in patch.items():
            if k not in _WRITABLE:
                continue
            out[k] = deepcopy(v)
        return out

    @staticmethod
    def _validate(data: dict) -> None:
        backend = data.get("model_backend", "auto")
        if backend not in ("auto", "generic", "holo"):
            raise ValueError("model_backend must be auto|generic|holo")
        try:
            data["max_steps"] = int(data.get("max_steps", 15))
        except (TypeError, ValueError) as e:
            raise ValueError("max_steps must be int") from e
        if data["max_steps"] < 1:
            raise ValueError("max_steps must be >= 1")
        try:
            data["idle_timeout"] = float(data.get("idle_timeout", 60))
        except (TypeError, ValueError) as e:
            raise ValueError("idle_timeout must be number") from e
        try:
            data["control_ttl_s"] = int(data.get("control_ttl_s", 120))
        except (TypeError, ValueError) as e:
            raise ValueError("control_ttl_s must be int") from e
        if data["control_ttl_s"] < 1:
            raise ValueError("control_ttl_s must be >= 1")
        ds = data.get("default_screen_id")
        if ds is not None and ds != "":
            data["default_screen_id"] = str(ds)
        else:
            data["default_screen_id"] = None
        if not isinstance(data.get("model"), str):
            data["model"] = str(data.get("model") or DEFAULTS["model"])
        if not isinstance(data.get("base_url"), str):
            data["base_url"] = str(data.get("base_url") or DEFAULTS["base_url"])
        if data.get("api_key") is None:
            data["api_key"] = ""
        else:
            data["api_key"] = str(data["api_key"])


def public_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets for API responses."""
    out = deepcopy(data)
    key = out.get("api_key") or ""
    out["api_key_set"] = bool(str(key).strip())
    out["api_key"] = ""  # never echo stored key
    return out


def merge_runtime_cfg(cli_cfg, settings: dict[str, Any],
                      env: dict[str, str] | None = None):
    """Build effective cfg fields: CLI/env already on cli_cfg win over settings.

    Call after argparse. For fields that still hold their argparse default
    and env was empty at parse time, fill from settings.json.
    This helper only fills when the attribute equals the known argparse
    default and settings has a value — used when wiring /run.
    """
    env = env if env is not None else os.environ
    # Model: prefer settings when env LOCAL_LOOP_MODEL was not the source
    # and we want next /run to read settings. For live /run we always
    # re-read settings for model/max_steps/idle when not overridden.
    return settings
