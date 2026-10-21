"""Loose audio files: each file is one line, rebuilt by re-encoding in the original format.

discover() groups audio files into assets: a voice asset is the shallowest folder whose name
says "voice" (voice/, vo/, dialogue/, speech/, narration/, game/voice in Ren'Py ...), with all
files below it; other audio files are grouped by their folder (role "audio": music, sfx).
Line ids follow the sorted list of files inside the asset (sub "file").

build() writes only the replaced files to out_path/<path inside the asset>, encoded like the
original (same container/codec, sample rate and channel count), so `install` can copy them
over the originals.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from babeldub.adapters.base import Adapter, Asset
from babeldub.adapters.loose import media
from babeldub.core.ids import asset_id, line_id, uid
from babeldub.core.output import EXIT_USAGE, BabelDubError
from babeldub.recon.kinds import VOICED_KINDS, guess_kind
from babeldub.recon.langs import locale_from_token, locale_in_path
from babeldub.recon.walk import walk

ADAPTER = "loose"
MAX_AUDIO_GROUPS = 64
SKIP_TOP = frozenset(
    {"_redist", "__macosx", "redist", "directx", "vcredist", "_commonredist", "commonredist"}
)
_SCENE_RE = re.compile(
    r"^(act|chapter|ch|scene|sc|episode|ep|part|day|mission|level|quest|stage)[_\- ]?\d+[a-z]?$", re.I
)
_NOT_NAME = frozenset(
    {
        "audio",
        "sound",
        "sounds",
        "snd",
        "data",
        "assets",
        "resources",
        "game",
        "common",
        "misc",
        "other",
        "main",
        "streaming",
        "streamingassets",
        "content",
        "localized",
        "localization",
        "loc",
        "lang",
        "languages",
        "male",
        "female",
        "generic",
        "extra",
        "extras",
        "unused",
        "old",
        "new",
        "temp",
        "tmp",
        "wav",
        "ogg",
        "mp3",
    }
)


_CHARACTER_WORDS = frozenset({"narrator"})  # kind words that are also speakers


def _is_voice_dir(name: str) -> bool:
    return guess_kind(name) in VOICED_KINDS


def _speaker_from_dirs(parts: list[str]) -> str | None:
    """First folder below the voice root that looks like a character name."""
    for part in parts:
        low = part.lower()
        if low in _NOT_NAME or _SCENE_RE.match(part) or locale_from_token(part):
            continue
        if guess_kind(part) and low not in _CHARACTER_WORDS:
            continue
        if not re.search(r"[^\W\d_]", part) or len(part) < 2 or len(part) > 32 or part.isdigit():
            continue
        return part
    return None


def _scene_from_dirs(parts: list[str]) -> str | None:
    for part in parts:
        if _SCENE_RE.match(part):
            return part
    return None


class LooseAudioAdapter(Adapter):
    name = ADAPTER
    level = "A"
    description = "Loose audio files (ogg/wav/mp3/flac/opus/m4a/aac); rebuild = re-encode like the original"

    # ---- discovery -----------------------------------------------------
    def discover(self, game_dir: Path) -> list[Asset]:
        index = walk(game_dir)
        files = [
            f
            for f in index.files_with_ext(*media.AUDIO_EXTS)
            if f.rel.split("/", 1)[0].lower() not in SKIP_TOP
        ]
        if not files:
            return []
        voice_roots: dict[str, list[Any]] = {}
        audio_groups: dict[str, list[Any]] = {}
        for entry in files:
            parts = entry.rel.split("/")[:-1]
            root = None
            for depth in range(len(parts)):
                if _is_voice_dir(parts[depth]):
                    root = "/".join(parts[: depth + 1])
                    break
            if root is not None:
                voice_roots.setdefault(root, []).append(entry)
            else:
                audio_groups.setdefault(entry.parent or ".", []).append(entry)
        recursive_audio = False
        if len(audio_groups) > MAX_AUDIO_GROUPS:  # many sfx folders: one asset per top-level folder
            merged: dict[str, list[Any]] = {}
            for rel, entries in audio_groups.items():
                merged.setdefault(rel.split("/", 1)[0] if rel != "." else ".", []).extend(entries)
            audio_groups = merged
            recursive_audio = True
        assets: list[Asset] = []
        for rel, entries in sorted(voice_roots.items()):
            assets.append(self._asset(rel, "voice", entries, recursive=True, exclude=[]))
        for rel, entries in sorted(audio_groups.items()):
            exclude = (
                [v for v in voice_roots if rel == "." or v.startswith(rel + "/")] if recursive_audio else []
            )
            assets.append(self._asset(rel, "audio", entries, recursive=recursive_audio, exclude=exclude))
        return assets

    def _asset(self, rel: str, role: str, entries: list[Any], recursive: bool, exclude: list[str]) -> Asset:
        exts = Counter(e.ext for e in entries)
        locales = {locale_in_path(e.rel) for e in entries}
        locales.discard(None)
        info: dict[str, Any] = {
            "container": "dir",
            "files": len(entries),
            "exts": dict(sorted(exts.items())),
            "recursive": recursive,
            "kind": guess_kind(rel) if rel != "." else None,
            "mtime_ns": max(e.mtime_ns for e in entries),
        }
        if exclude:
            info["exclude"] = sorted(exclude)
        if len(locales) > 1:
            info["locales"] = sorted(locales)  # type: ignore[arg-type]
        return Asset(
            asset_id=asset_id(self.name, rel),
            adapter=self.name,
            relpath=rel,
            role=role,
            size=sum(e.size for e in entries),
            locale=next(iter(locales)) if len(locales) == 1 else None,
            level="A",
            info=info,
        )

    def _files(self, game_dir: Path, asset: Asset) -> list[tuple[str, Path]]:
        """(path inside the asset, absolute path) sorted by the inside path."""
        root = game_dir / asset.relpath
        if not root.is_dir():
            raise BabelDubError(
                f"{asset.relpath} is not a folder in the game", code=EXIT_USAGE, hint="re-run `babeldub scan`"
            )
        recursive = bool(asset.info.get("recursive", asset.role == "voice"))
        excluded = [game_dir / e for e in asset.info.get("exclude", [])]
        found: list[tuple[str, Path]] = []
        candidates = root.rglob("*") if recursive else root.iterdir()
        for path in candidates:
            if not path.is_file() or path.suffix.lower() not in media.AUDIO_EXTS:
                continue
            if excluded and any(ex in path.parents for ex in excluded):
                continue
            found.append((path.relative_to(root).as_posix(), path))
        found.sort(key=lambda item: item[0])
        return found

    @staticmethod
    def _game_rel(asset: Asset, inner: str) -> str:
        return inner if asset.relpath in (".", "") else f"{asset.relpath}/{inner}"

    # ---- lines ---------------------------------------------------------
    def list_lines(self, game_dir: Path, asset: Asset) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        for index, (inner, path) in enumerate(self._files(game_dir, asset)):
            file_rel = self._game_rel(asset, inner)
            ident = line_id(self.name, asset.relpath, "file", index)
            inner_dirs = inner.split("/")[:-1]
            flags: list[str] = []
            try:
                probe = media.probe(path)
                audio = {k: probe.get(k) for k in ("sr", "ch", "dur_s", "codec")}
            except BabelDubError:
                audio = {"sr": None, "ch": None, "dur_s": None, "codec": path.suffix.lower().lstrip(".")}
                flags.append("no_probe")
            if asset.role == "voice":
                guessed = guess_kind(inner)
                kind = guessed if guessed in VOICED_KINDS else "dialogue"
            else:
                kind = guess_kind(file_rel) or "unknown"
            lines.append(
                {
                    "id": ident,
                    "uid": uid(ident),
                    "adapter": self.name,
                    "asset_id": asset.asset_id,
                    "entry": {"key": path.stem, "sub": "file", "index": index, "file": file_rel},
                    "kind": kind,
                    "locale": locale_in_path(file_rel) or asset.locale,
                    "audio": audio,
                    "speaker_hint": _speaker_from_dirs(inner_dirs) if asset.role == "voice" else None,
                    "scene": _scene_from_dirs(inner_dirs),
                    "order": index,
                    "fixed_length": kind == "cutscene",
                    "flags": flags,
                }
            )
        return lines

    def extract_audio(
        self, game_dir: Path, asset: Asset, lines: list[dict[str, Any]], out_dir: Path
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for line in lines:
            src = game_dir / line["entry"]["file"]
            dst = out_dir / f"{line['uid']}.wav"
            try:
                info = media.decode_to_wav(src, dst)
                results.append({"id": line["id"], "wav": str(dst), **info})
            except BabelDubError as err:
                results.append({"id": line["id"], "error": str(err)})
        return results

    # ---- rebuild -------------------------------------------------------
    def build(
        self,
        game_dir: Path,
        asset: Asset,
        replacements: dict[str, Path],
        out_path: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        by_id = {line["id"]: line for line in self.list_lines(game_dir, asset)}
        unknown = sorted(set(replacements) - set(by_id))
        if unknown:
            raise BabelDubError(
                f"{len(unknown)} replacement ids are not lines of {asset.asset_id}",
                code=EXIT_USAGE,
                hint="re-run `babeldub extract`; the folder content changed since the line list was made",
                unknown=unknown[:5],
            )
        files: list[str] = []
        codecs: set[str] = set()
        prefix = "" if asset.relpath in (".", "") else asset.relpath + "/"
        for ident in sorted(replacements):
            line = by_id[ident]
            file_rel = line["entry"]["file"]
            inner = file_rel[len(prefix) :] if prefix and file_rel.startswith(prefix) else file_rel
            original = game_dir / file_rel
            orig = media.probe(original)
            target = out_path / inner
            codec = media.encode_like(
                Path(replacements[ident]),
                target,
                int(orig["sr"]),
                int(orig["ch"]),
                original.suffix,
                orig.get("subtype"),
            )
            codecs.add(codec)
            files.append(file_rel)
        return {
            "out": str(out_path),
            "files": files,
            "replaced": len(files),
            "kept": 0,
            "codec": ",".join(sorted(codecs)) if codecs else None,
        }

    def verify(self, game_dir: Path, asset: Asset, built: Path, options: dict[str, Any]) -> dict[str, Any]:
        checks: list[str] = []
        problems: list[str] = []
        if not built.is_dir():
            return {"ok": False, "checks": ["exists"], "problems": [f"{built} is not a folder"]}
        produced = sorted(p for p in built.rglob("*") if p.is_file() and p.suffix.lower() in media.AUDIO_EXTS)
        if not produced:
            problems.append("no audio files in the build output")
        for path in produced:
            inner = path.relative_to(built).as_posix()
            ok, problem, info = media.is_decodable(path)
            if not ok:
                problems.append(f"{inner}: {problem}")
                continue
            original = game_dir / self._game_rel(asset, inner)
            if not original.is_file():
                problems.append(f"{inner}: no original file at {self._game_rel(asset, inner)}")
                continue
            orig = media.probe(original)
            if info and orig.get("ch") and info.get("ch") != orig.get("ch"):
                problems.append(f"{inner}: channels {info.get('ch')} != original {orig.get('ch')}")
            if (
                info
                and orig.get("sr")
                and info.get("sr") != orig.get("sr")
                and path.suffix.lower() != ".opus"
            ):
                problems.append(f"{inner}: sample rate {info.get('sr')} != original {orig.get('sr')}")
        checks += ["exists", "decodes", "duration>0", "same channels", "same sample rate"]
        return {"ok": not problems, "checks": checks, "problems": problems, "files": len(produced)}
