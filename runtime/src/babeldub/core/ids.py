"""Stable identifiers for lines and assets.

A line id addresses audio by container position, not by name: engines often reuse names
(Low-Budget Repairs has duplicated subsound names), but (container, sub-container, index)
is unique and is exactly what a repacker needs. The engine key is kept in `entry.key`.
"""

from __future__ import annotations

import hashlib
import re


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def line_id(adapter: str, relpath: str, sub: str, index: int) -> str:
    """fmod:StreamingAssets/Dialogue_en-US.bank#fsb1:0042"""
    rel = relpath.replace("\\", "/")
    return f"{adapter}:{rel}#{sub}:{index:04d}"


def uid(identifier: str) -> str:
    """Short filesystem-safe key derived from an id (first 12 hex of sha1)."""
    return sha1_hex(identifier.encode("utf-8"))[:12]


def asset_id(adapter: str, relpath: str) -> str:
    return f"{adapter}:{relpath.replace(chr(92), '/')}"


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str = "game") -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:48] or fallback
