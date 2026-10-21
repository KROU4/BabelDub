"""Alternative takes and duplicates of one voiced line: the leader is dubbed, followers reuse it.

`link` flags extra recordings of one text key in one container (`alt_take`, `duplicate`). They
share the subtitle, but their audio often does not match it exactly, so dubbing each take wastes
GPU time, and an ICL voice prompt with a mismatched transcript can run away. Only the leader goes
through synth, post and qa; at build time every follower gets the leader's accepted audio (the
adapter converts sample rate and channels as for any replacement).

Leader of a group: the line whose entry.key equals text.key, else an unflagged member, else a
member whose key contains "best" (any case), else the lowest order/index.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import Any

from babeldub.core import layout
from babeldub.core.jsonl import latest_by, read_jsonl

TAKE_FLAGS = frozenset({"alt_take", "duplicate"})
VOICED_KINDS = ("dialogue", "cutscene", "bark")


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return sys.maxsize


def _rank(line: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
    """Lower is a better leader."""
    entry = line.get("entry") or {}
    key = str(entry.get("key") or "")
    text_key = (line.get("text") or {}).get("key")
    flagged = bool(TAKE_FLAGS.intersection(line.get("flags") or []))
    return (
        0 if key and key == text_key else 1,
        1 if flagged else 0,
        0 if "best" in key.lower() else 1,
        _int(line.get("order")),
        _int(entry.get("index")),
        str(line.get("id")),
    )


def follower_map(lines: Iterable[dict[str, Any]]) -> dict[str, str]:
    """{follower line id: leader line id} over voiced lines grouped by (asset_id, text.key).

    Only groups with at least two members, one of them flagged alt_take or duplicate, count.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for line in lines:
        if line.get("kind") not in VOICED_KINDS or "id" not in line:
            continue
        key = (line.get("text") or {}).get("key")
        if not key:
            continue
        groups.setdefault((str(line.get("asset_id")), str(key)), []).append(line)
    out: dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2 or not any(TAKE_FLAGS.intersection(m.get("flags") or []) for m in members):
            continue
        leader = min(members, key=_rank)
        for member in members:
            if member is not leader:
                out[str(member["id"])] = str(leader["id"])
    return out


def project_followers(root: Any) -> dict[str, str]:
    """follower_map of a project's inventory/lines.jsonl (`root` is a Project or a folder Path)."""
    path = root.path(layout.LINES) if hasattr(root, "path") else root / layout.LINES
    return follower_map(latest_by(read_jsonl(path)).values())
