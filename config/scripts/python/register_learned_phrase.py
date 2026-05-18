#!/usr/bin/env python3
"""Register a new phrase that the local intent matcher should handle.
Appends an entry to /config/custom_sentences/en/learned.yaml mapping the phrase
to a target script + optional variables, then reloads conversation so the new
sentence is picked up immediately.

Idempotent: re-registering the same phrase is a no-op.

Usage:
  python3 register_learned_phrase.py "<phrase>" <target_script_entity_id> [done_message] [vars_json]

Example:
  python3 register_learned_phrase.py "play disco" script.shield_play_spotify "Playing disco" '{"query":"disco"}'
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from ha_client import call_service

SENTENCES_FILE = "/config/custom_sentences/en/learned.yaml"


def _load_sentences():
    """Load learned.yaml, returning a dict structured for RunLearnedScript intent."""
    import yaml
    if os.path.exists(SENTENCES_FILE):
        d = yaml.safe_load(open(SENTENCES_FILE)) or {}
    else:
        d = {}
    d.setdefault("language", "en")
    d.setdefault("intents", {})
    d["intents"].setdefault("RunLearnedScript", {"data": []})
    return d


def _save_sentences(d):
    import yaml
    os.makedirs(os.path.dirname(SENTENCES_FILE), exist_ok=True)
    with open(SENTENCES_FILE, "w") as f:
        yaml.safe_dump(d, f, sort_keys=False, default_flow_style=False, allow_unicode=True)


def register(phrase, target_script, done_message="Okay", script_vars=None):
    """Idempotently add phrase → target_script mapping. Reloads conversation."""
    phrase_norm = phrase.lower().strip()
    if not phrase_norm:
        raise ValueError("phrase is empty")
    if not target_script.startswith("script."):
        raise ValueError(f"target_script must be a script.* entity id (got {target_script!r})")

    d = _load_sentences()
    entries = d["intents"]["RunLearnedScript"]["data"]

    # Check for existing identical phrase
    for entry in entries:
        for s in entry.get("sentences", []):
            if s.lower().strip() == phrase_norm:
                print(f"already registered: {phrase!r}")
                return False

    slots = {
        "target_script": target_script,
        "done_message": done_message,
    }
    if script_vars:
        slots["script_vars_json"] = json.dumps(script_vars)
    entries.append({
        "sentences": [phrase_norm],
        "slots": slots,
    })

    _save_sentences(d)
    call_service("conversation", "reload")
    print(f"registered: {phrase!r} → {target_script} ({len(entries)} total learned phrases)")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip())
    phrase = sys.argv[1]
    target_script = sys.argv[2]
    done_message = sys.argv[3].strip() if len(sys.argv) > 3 and sys.argv[3].strip() else "Okay"
    raw_vars = sys.argv[4].strip() if len(sys.argv) > 4 else ""
    script_vars = json.loads(raw_vars) if raw_vars else None
    register(phrase, target_script, done_message, script_vars)
