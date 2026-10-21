"""Output contract shared by every command.

Each command prints exactly one compact JSON object to stdout (target: under 2 KB, so an
agent can read it without flooding its context) and returns one of the exit codes below.
Long data goes to files inside the project; the summary points at them.
"""

from __future__ import annotations

import json
import sys
from typing import Any

EXIT_OK = 0  # stage finished, nothing needs attention
EXIT_ERROR = 1  # unexpected failure; see "error" and the stage log
EXIT_USAGE = 2  # bad arguments, missing prerequisite stage, or invalid agent file
EXIT_ATTENTION = 3  # finished, but some items need a decision (agent or human)
EXIT_ENV = 4  # environment is missing something: run `babeldub doctor` / `babeldub setup`

_PRETTY = False


class BabelDubError(Exception):
    """Expected failure with an exit code and an actionable hint for the agent."""

    def __init__(self, message: str, code: int = EXIT_ERROR, hint: str | None = None, **extra: Any):
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.extra = extra


def set_pretty(value: bool) -> None:
    global _PRETTY
    _PRETTY = value


def clip(items: list[Any], limit: int = 10) -> list[Any]:
    """Keep summaries short: show the first `limit` items and how many were dropped."""
    if len(items) <= limit:
        return list(items)
    return list(items[:limit]) + [f"... +{len(items) - limit} more"]


def emit(payload: dict[str, Any], code: int = EXIT_OK) -> int:
    payload = {"ok": code in (EXIT_OK, EXIT_ATTENTION), **payload}
    if _PRETTY:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return code


def emit_error(err: BabelDubError) -> int:
    payload: dict[str, Any] = {"error": str(err)}
    if err.hint:
        payload["hint"] = err.hint
    payload.update(err.extra)
    return emit(payload, err.code)
