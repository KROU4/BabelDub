"""Version strings of Windows binaries without executing them.

A PE file keeps VS_FIXEDFILEINFO (signature 0xFEEF04BD) in its version resource; the numeric
FileVersion follows the signature. The StringFileInfo block right after it holds UTF-16 strings
such as ProductVersion ("2022.3.62f3 (...)" in UnityPlayer.dll), so a small window after the
signature is also searched with a caller-supplied regex.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

_SIGNATURE = b"\xbd\x04\xef\xfe"
_CHUNK = 4 * 1024 * 1024


def _find_signature(path: Path, max_bytes: int = 256 * 1024 * 1024) -> tuple[int, bytes] | None:
    """Offset of the last VS_FIXEDFILEINFO signature and 8 KB of bytes starting there."""
    found: int | None = None
    try:
        with path.open("rb") as fh:
            offset = 0
            tail = b""
            while offset < max_bytes:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                data = tail + chunk
                base = offset - len(tail)
                pos = data.rfind(_SIGNATURE)
                if pos >= 0:
                    found = base + pos
                tail = data[-3:]
                offset += len(chunk)
            if found is None:
                return None
            fh.seek(found)
            return found, fh.read(8192)
    except OSError:
        return None


_KEYS_RE = re.compile(r"(FileVersion|ProductVersion|ProductName|CompanyName|FileDescription)\x00+([^\x00]+)")


def _strings(block: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for shift in (0, 1):
        text = block[shift:].decode("utf-16-le", errors="ignore")
        for match in _KEYS_RE.finditer(text):
            out.setdefault(match.group(1), match.group(2).strip())
        if out:
            break
    return out


def string_info(path: Path) -> dict[str, str]:
    """StringFileInfo values (FileVersion, ProductVersion, ProductName, ...) of a PE file."""
    hit = _find_signature(path)
    return _strings(hit[1]) if hit else {}


def file_version(path: Path) -> str | None:
    """FileVersion of a PE file: the StringFileInfo text when present ('2.3.6 (build 149358)'),
    else the numeric VS_FIXEDFILEINFO value 'a.b.c.d'."""
    hit = _find_signature(path)
    if not hit:
        return None
    _, block = hit
    text = _strings(block).get("FileVersion")
    if text:
        return text
    if len(block) < 16:
        return None
    ms, ls = struct.unpack_from("<II", block, 8)
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def version_string(path: Path, pattern: str) -> str | None:
    """First match of `pattern` in the UTF-16 version strings that follow VS_FIXEDFILEINFO."""
    hit = _find_signature(path)
    if not hit:
        return None
    _, block = hit
    for shift in (0, 1):
        text = block[shift:].decode("utf-16-le", errors="ignore")
        match = re.search(pattern, text)
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return None
