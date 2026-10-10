"""What OpenNollie last stored on each controller (~/.local/state/opennollie/stored.json), per serial:

- "animation": fingerprint of the uploaded animation file, so an unchanged one isn't rewritten (the
  controller pauses its LEDs while its flash is written);
- "saved": the last profile saved from the Effects studio, so it can be saved again when the color
  balance or LED counts change;
- "loop" / "boot": the built-in effect groups as set up in OpenNollie, and the canvases written for them
  (builtin.py).
"""

import json
import os
from pathlib import Path

STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "opennollie" / "stored.json"


def get(serial: str) -> dict:
    try:
        entry = json.loads(STATE_FILE.read_text()).get(serial) or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {"animation": entry} if "crc" in entry else entry        # older files: just the fingerprint


def remember(serial: str, **values) -> None:
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state[serial] = get(serial) | values
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))
