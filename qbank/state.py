"""Run state — chapter-granular resume, same file location as v1."""

from __future__ import annotations

import json
from pathlib import Path

from . import config


def load_state() -> dict:
    if config.STATE_FILE.exists():
        try:
            return json.loads(config.STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"pdf_progress": {}}


def save_state(state: dict) -> None:
    config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(json.dumps(state, indent=2))


def progress(state: dict, subject: str) -> dict:
    return state.setdefault("pdf_progress", {}).setdefault(
        subject, {"chapters_done": []})
