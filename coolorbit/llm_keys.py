"""Shared Anthropic API key selection: spend the PRIMARY key until its credit runs out, then the BACKUP.

Keys live only in the gitignored ``.env`` (``ANTHROPIC_API_KEY_PRIMARY``, ``ANTHROPIC_API_KEY_BACKUP``) and
are never printed, logged or written anywhere else. The switch is global (every agent and runner moves to
BACKUP at once, so comparison arms never run on different accounts) and recorded only as
``runs/llm_key_state.json`` = ``{"primary_exhausted_at": <UTC time>, "reason": <short text>}``.

    from coolorbit import llm_keys
    client = llm_keys.client()                                # anthropic.Anthropic on the active key
    result = llm_keys.call_with_failover(lambda c: c.messages.create(...))
    env = llm_keys.subprocess_env()                           # for `claude -p` sessions (sets ANTHROPIC_API_KEY)
    if llm_keys.is_credit_exhausted(session_output): llm_keys.mark_primary_exhausted("claude -p session")

A run that dies because the primary ran out mid-session is an infrastructure failure: rerun it on the
backup, don't score it against the arm.

    python -m coolorbit.llm_keys status                       # which key is active (never the key itself)
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
STATE_PATH = REPO_ROOT / "runs" / "llm_key_state.json"
PRIMARY, BACKUP = "ANTHROPIC_API_KEY_PRIMARY", "ANTHROPIC_API_KEY_BACKUP"
_EXHAUSTED_MARKERS = ("credit balance is too low", "credit balance too low", "insufficient credit")


class NoUsableKey(RuntimeError):
    """No key is configured, or the primary is exhausted and there is no backup."""


def _env_value(raw: str) -> str:
    """A .env value the way dotenv reads it: quoted -> the text inside the quotes; unquoted -> up to an inline
    ``  # comment`` (a ``#`` after whitespace). Keys never contain whitespace, so a comment can't be part of one."""
    value = raw.strip()
    if value[:1] in ("'", '"') and value[0] in value[1:]:
        return value[1:value.index(value[0], 1)]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip().strip("'\"")


def _read_env(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if line and not line.startswith("#") and "=" in line:
                name, _, value = line.partition("=")
                values[name.strip()] = _env_value(value)
    for name in (PRIMARY, BACKUP):  # the process environment wins (e.g. inside a sandbox)
        if os.environ.get(name):
            values[name] = os.environ[name]
    return values


def _read_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# Paths default to None and are resolved at call time (module defaults would be frozen at import).
def primary_exhausted(state_path: Optional[Path] = None) -> bool:
    return bool(_read_state(state_path or STATE_PATH).get("primary_exhausted_at"))


def active_key_name(env_path: Optional[Path] = None, state_path: Optional[Path] = None) -> str:
    env_path = env_path or ENV_PATH
    env = _read_env(env_path)
    if env.get(PRIMARY) and not primary_exhausted(state_path):
        return PRIMARY
    if env.get(BACKUP):
        return BACKUP
    if env.get(PRIMARY):
        raise NoUsableKey("the primary key is exhausted and no backup key is configured")
    raise NoUsableKey(f"no Anthropic key: set {PRIMARY} (and {BACKUP}) in {env_path}")


def active_key(env_path: Optional[Path] = None, state_path: Optional[Path] = None) -> str:
    return _read_env(env_path or ENV_PATH)[active_key_name(env_path, state_path)]


def is_credit_exhausted(error_or_text: Any) -> bool:
    """True for Anthropic's out-of-credit error (an exception or a session's output text)."""
    text = str(error_or_text).lower()
    return any(marker in text for marker in _EXHAUSTED_MARKERS)


def mark_primary_exhausted(reason: str, state_path: Optional[Path] = None) -> bool:
    """Record the switch to BACKUP (idempotent, process-safe). Returns True if this call made the switch."""
    state_path = state_path or STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path.with_suffix(".lock"), "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if primary_exhausted(state_path):
                return False
            state = {"primary_exhausted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "reason": str(reason)[:200]}
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp.replace(state_path)
            return True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def client(**kwargs: Any):
    """An ``anthropic.Anthropic`` client on the active key (imports anthropic lazily)."""
    import anthropic

    return anthropic.Anthropic(api_key=active_key(), **kwargs)


def call_with_failover(fn: Callable[[Any], Any], make_client: Optional[Callable[[], Any]] = None,
                       state_path: Optional[Path] = None, env_path: Optional[Path] = None) -> Any:
    """Run ``fn(client)``; if the primary is out of credit, switch everyone to the backup and retry once."""
    make = make_client or client
    used = active_key_name(env_path, state_path)
    try:
        return fn(make())
    except Exception as exc:
        if used != PRIMARY or not is_credit_exhausted(exc):
            raise
        mark_primary_exhausted(f"API error: {str(exc)[:120]}", state_path)
        if active_key_name(env_path, state_path) != BACKUP:
            raise
        return fn(make())


def subprocess_env(base: Optional[dict] = None) -> dict:
    """Environment for a child process (e.g. ``claude -p``): only the active key, as ANTHROPIC_API_KEY."""
    env = dict(os.environ if base is None else base)
    env.pop(PRIMARY, None)
    env.pop(BACKUP, None)
    env["ANTHROPIC_API_KEY"] = active_key()
    return env


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m coolorbit.llm_keys", description="Which Anthropic key is active.")
    ap.add_argument("command", choices=["status"])
    ap.parse_args()
    env = _read_env(ENV_PATH)
    state = _read_state(STATE_PATH)
    print(f"{PRIMARY}: {'set' if env.get(PRIMARY) else 'MISSING'}"
          f"{' (exhausted at ' + state['primary_exhausted_at'] + ')' if state.get('primary_exhausted_at') else ''}")
    print(f"{BACKUP}:  {'set' if env.get(BACKUP) else 'MISSING'}")
    try:
        print(f"active: {active_key_name()}")
    except NoUsableKey as exc:
        print(f"active: NONE ({exc})")


if __name__ == "__main__":
    main()
