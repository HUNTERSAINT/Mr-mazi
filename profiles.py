"""Remembers each user's business name and usual video settings (so a daily post is two taps)."""
import json

import config

_FILE = config.DATA / "profiles.json"


def _load() -> dict:
    try:
        return json.loads(_FILE.read_text())
    except Exception:
        return {}


def get(uid: int) -> dict:
    return _load().get(str(uid), {})


def update(uid: int, **fields) -> None:
    data = _load()
    data.setdefault(str(uid), {}).update(fields)
    _FILE.write_text(json.dumps(data))
