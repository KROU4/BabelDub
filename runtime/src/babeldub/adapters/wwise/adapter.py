"""Audiokinetic Wwise (support level B): list and decode voice media, no native rebuild.

Sources of lines:
  .bnk  - media embedded in DATA (indexed by DIDX); sub "bnk"
  .pck  - streamed media table (sub "snd"), external sources (sub "ext") and media embedded in the
          banks stored inside the package (sub "bnk<k>", k = bank position in the bank table)
  .wem  - loose media files, grouped per folder; sub "wem"
Names come from SoundbanksInfo.xml/.json when the game ships it (media id -> ShortName/Path),
otherwise the media id is the key. Locale comes from the package language map or from the
language folder ("English(US)" -> en-US); localized media are voice by definition.

Decoding: PCM media are written directly; every other codec goes through vgmstream-cli on the
media bytes copied to a temporary .wem. Rebuilding needs WwiseConsole of the game's Wwise version
(Wwise Authoring) - build() refuses with a pointer to the packing reference.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from babeldub.adapters.base import Adapter, Asset
from babeldub.adapters.loose import media
from babeldub.adapters.wwise import formats
from babeldub.core.ids import asset_id, line_id, uid
from babeldub.core.output import EXIT_ERROR, EXIT_USAGE, BabelDubError
from babeldub.recon.kinds import VOICED_KINDS, guess_kind
from babeldub.recon.langs import locale_from_token, locale_in_path
from babeldub.recon.walk import walk

ADAPTER = "wwise"
HEAD_BYTES = 4096
NON_LANGUAGES = frozenset({"sfx", "", "none", "default"})


def _is_akpk(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(4) == b"AKPK"
    except OSError:
        return False


def _iter_named_media(node: Any) -> Iterator[dict[str, Any]]:
    """Walk SoundbanksInfo JSON and yield every object that has an Id and a ShortName/Path."""
    if isinstance(node, dict):
        if "Id" in node and ("ShortName" in node or "Path" in node):
            yield node
        for value in node.values():
            yield from _iter_named_media(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_named_media(value)


def parse_soundbanks_info(path: Path) -> dict[int, dict[str, str]]:
    """media id -> {"name", "path", "language"} from SoundbanksInfo.xml or SoundbanksInfo.json."""
    names: dict[int, dict[str, str]] = {}
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            items = [
                {
                    "id": n.get("Id"),
                    "short": n.get("ShortName"),
                    "path": n.get("Path"),
                    "lang": n.get("Language"),
                }
                for n in _iter_named_media(data)
            ]
        else:
            root = ET.parse(path).getroot()
            items = []
            for el in root.iter("File"):
                items.append(
                    {
                        "id": el.get("Id"),
                        "short": (el.findtext("ShortName") or "").strip(),
                        "path": (el.findtext("Path") or "").strip(),
                        "lang": el.get("Language"),
                    }
                )
    except (OSError, ValueError, ET.ParseError):
        return names
    for item in items:
        try:
            media_id = int(str(item["id"]))
        except (TypeError, ValueError):
            continue
        short = str(item.get("short") or item.get("path") or "")
        if not short:
            continue
        names[media_id] = {
            "name": short,
            "path": str(item.get("path") or ""),
            "language": str(item.get("lang") or ""),
        }
    return names


class WwiseAdapter(Adapter):
    name = ADAPTER
    level = "B"
    description = "Wwise .bnk/.pck/.wem: list and decode (vgmstream); rebuild needs WwiseConsole"

    def __init__(self) -> None:
        self._names: dict[str, dict[int, dict[str, str]]] = {}

    # ---- helpers -------------------------------------------------------
    def _media_names(self, game_dir: Path) -> dict[int, dict[str, str]]:
        key = str(game_dir.resolve())
        if key not in self._names:
            names: dict[int, dict[str, str]] = {}
            index = walk(game_dir)
            for fname in ("soundbanksinfo.json", "soundbanksinfo.xml"):
                for entry in index.files_named(fname):
                    names.update(parse_soundbanks_info(game_dir / entry.rel))
            self._names[key] = names
        return self._names[key]

    @staticmethod
    def _locale(lang_name: str | None) -> str | None:
        if lang_name is None or lang_name.strip().lower() in NON_LANGUAGES:
            return None
        return locale_from_token(lang_name)

    @staticmethod
    def _kind(localized: bool, *texts: str | None) -> str:
        guessed = guess_kind(*texts)
        if localized:
            return guessed if guessed in VOICED_KINDS else "dialogue"
        return guessed or "unknown"

    # ---- discovery -----------------------------------------------------
    def discover(self, game_dir: Path) -> list[Asset]:
        index = walk(game_dir)
        assets: list[Asset] = []
        for entry in index.files_with_ext(".bnk"):
            path = game_dir / entry.rel
            try:
                with path.open("rb") as fh:
                    bank = formats.read_bank(fh)
            except (OSError, formats.WwiseFormatError):
                continue
            if not bank.media:
                continue  # metadata-only bank (events/hierarchy, no embedded audio)
            locale = locale_in_path(entry.rel)
            voiced = locale is not None or guess_kind(entry.rel) in VOICED_KINDS
            assets.append(
                Asset(
                    asset_id=asset_id(self.name, entry.rel),
                    adapter=self.name,
                    relpath=entry.rel,
                    role="voice" if voiced else "audio",
                    size=entry.size,
                    locale=locale,
                    level="B",
                    info={
                        "container": "bnk",
                        "bank_id": bank.bank_id,
                        "bank_version": bank.version,
                        "media": len(bank.media),
                        "mtime_ns": entry.mtime_ns,
                    },
                )
            )
        for entry in index.files_with_ext(".pck"):
            path = game_dir / entry.rel
            if not _is_akpk(path):
                continue
            try:
                with path.open("rb") as fh:
                    pack = formats.read_pack(fh)
            except (OSError, formats.WwiseFormatError):
                continue
            lang_ids = {e.language_id for e in pack.sounds + pack.externals + pack.banks}
            locales = {self._locale(pack.languages.get(i)) for i in lang_ids}
            locales.discard(None)
            voiced = bool(locales) or guess_kind(entry.rel) in VOICED_KINDS
            info: dict[str, Any] = {
                "container": "pck",
                "sounds": len(pack.sounds),
                "banks": len(pack.banks),
                "externals": len(pack.externals),
                "languages": sorted(n for n in pack.languages.values() if n),
                "mtime_ns": entry.mtime_ns,
            }
            assets.append(
                Asset(
                    asset_id=asset_id(self.name, entry.rel),
                    adapter=self.name,
                    relpath=entry.rel,
                    role="voice" if voiced else "audio",
                    size=entry.size,
                    locale=next(iter(locales)) if len(locales) == 1 else locale_in_path(entry.rel),
                    level="B",
                    info=info,
                )
            )
        groups: dict[str, list[Any]] = {}
        for entry in index.files_with_ext(".wem"):
            groups.setdefault(entry.parent or ".", []).append(entry)
        for rel, entries in sorted(groups.items()):
            locale = locale_in_path(rel, include_file=False) if rel != "." else None
            voiced = locale is not None or guess_kind(rel) in VOICED_KINDS
            assets.append(
                Asset(
                    asset_id=asset_id(self.name, rel),
                    adapter=self.name,
                    relpath=rel,
                    role="voice" if voiced else "audio",
                    size=sum(e.size for e in entries),
                    locale=locale,
                    level="B",
                    info={
                        "container": "wem-dir",
                        "files": len(entries),
                        "mtime_ns": max(e.mtime_ns for e in entries),
                    },
                )
            )
        return assets

    # ---- lines ---------------------------------------------------------
    def _line(
        self,
        asset: Asset,
        sub: str,
        index: int,
        media_id: int,
        head: bytes,
        size: int,
        locale: str | None,
        names: dict[int, dict[str, str]],
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        ident = line_id(self.name, asset.relpath, sub, index)
        flags: list[str] = []
        try:
            wem = formats.wem_info(head, size)
            audio: dict[str, Any] = {"sr": wem.sr, "ch": wem.ch, "dur_s": wem.dur_s, "codec": wem.codec}
        except formats.WwiseFormatError:
            audio = {"sr": None, "ch": None, "dur_s": None, "codec": None}
            flags.append("no_probe")
        named = names.get(media_id)
        short = named["name"] if named else None
        if locale is None and named and named.get("language"):
            locale = self._locale(named["language"])
        key = re.split(r"[\\/]", short)[-1].rsplit(".", 1)[0] if short else str(media_id)
        entry: dict[str, Any] = {"key": key, "sub": sub, "index": index, "media_id": media_id, **extra}
        if short:
            entry["name"] = short
        # a package mixes languages per entry, so only the entry's own language counts there
        voiced_asset = asset.role == "voice" and asset.info.get("container") != "pck"
        kind = self._kind(locale is not None or voiced_asset, short, asset.relpath)
        return {
            "id": ident,
            "uid": uid(ident),
            "adapter": self.name,
            "asset_id": asset.asset_id,
            "entry": entry,
            "kind": kind,
            "locale": locale,
            "audio": audio,
            "speaker_hint": None,
            "scene": None,
            "order": index,
            "fixed_length": kind == "cutscene",
            "flags": flags,
        }

    def list_lines(self, game_dir: Path, asset: Asset) -> list[dict[str, Any]]:
        names = self._media_names(game_dir)
        container = asset.info.get("container")
        path = game_dir / asset.relpath
        lines: list[dict[str, Any]] = []
        if container == "wem-dir":
            files = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".wem")
            for i, wem_path in enumerate(files):
                with wem_path.open("rb") as fh:
                    head = fh.read(HEAD_BYTES)
                stem = wem_path.stem
                media_id = int(stem) if stem.isdigit() else 0
                file_rel = wem_path.relative_to(game_dir).as_posix()
                line = self._line(
                    asset,
                    "wem",
                    i,
                    media_id,
                    head,
                    wem_path.stat().st_size,
                    asset.locale,
                    names,
                    {"file": file_rel},
                )
                if not stem.isdigit():
                    line["entry"]["key"] = stem
                lines.append(line)
            return lines
        with path.open("rb") as fh:
            if container == "bnk":
                bank = formats.read_bank(fh)
                for i, m in enumerate(bank.media):
                    fh.seek(m.offset)
                    head = fh.read(min(m.size, HEAD_BYTES))
                    lines.append(
                        self._line(
                            asset,
                            "bnk",
                            i,
                            m.media_id,
                            head,
                            m.size,
                            asset.locale,
                            names,
                            {"offset": m.offset, "size": m.size},
                        )
                    )
                return lines
            if container != "pck":
                raise BabelDubError(f"wwise: unknown container type for {asset.relpath}", code=EXIT_USAGE)
            pack = formats.read_pack(fh)
            for sub, table in (("snd", pack.sounds), ("ext", pack.externals)):
                for i, e in enumerate(table):
                    fh.seek(e.offset)
                    head = fh.read(min(e.size, HEAD_BYTES))
                    locale = self._locale(pack.languages.get(e.language_id))
                    lines.append(
                        self._line(
                            asset,
                            sub,
                            i,
                            e.file_id,
                            head,
                            e.size,
                            locale,
                            names,
                            {"offset": e.offset, "size": e.size},
                        )
                    )
            for k, bank_entry in enumerate(pack.banks):
                locale = self._locale(pack.languages.get(bank_entry.language_id))
                try:
                    bank = formats.read_bank(fh, base=bank_entry.offset, size=bank_entry.size)
                except formats.WwiseFormatError:
                    continue
                for i, m in enumerate(bank.media):
                    fh.seek(m.offset)
                    head = fh.read(min(m.size, HEAD_BYTES))
                    lines.append(
                        self._line(
                            asset,
                            f"bnk{k}",
                            i,
                            m.media_id,
                            head,
                            m.size,
                            locale,
                            names,
                            {"offset": m.offset, "size": m.size, "bank_id": bank.bank_id},
                        )
                    )
        return lines

    # ---- decoding ------------------------------------------------------
    @staticmethod
    def _media_bytes(game_dir: Path, asset: Asset, entry: dict[str, Any]) -> bytes:
        if "file" in entry:
            return (game_dir / entry["file"]).read_bytes()
        with (game_dir / asset.relpath).open("rb") as fh:
            fh.seek(int(entry["offset"]))
            data = fh.read(int(entry["size"]))
        if len(data) != int(entry["size"]):
            raise BabelDubError(f"media {entry.get('media_id')} is truncated in {asset.relpath}")
        return data

    @staticmethod
    def _write_pcm(data: bytes, dst: Path) -> dict[str, Any]:
        raw, sr, ch, bits = formats.pcm_samples(data)
        if bits not in (8, 16, 24, 32) or ch <= 0:
            raise formats.WwiseFormatError(f"unsupported PCM layout ({bits} bit, {ch} ch)")
        if bits == 16:
            samples = np.frombuffer(raw[: len(raw) // (2 * ch) * 2 * ch], dtype="<i2").reshape(-1, ch)
            subtype = "PCM_16"
        elif bits == 8:
            samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128).reshape(-1, ch) * 256
            samples = samples.astype("<i2")
            subtype = "PCM_16"
        elif bits == 24:
            usable = len(raw) // (3 * ch) * 3 * ch
            b = np.frombuffer(raw[:usable], dtype=np.uint8).reshape(-1, 3)
            vals = (
                b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)
            )
            vals = np.where(vals & 0x800000, vals - 0x1000000, vals)
            samples = (vals.reshape(-1, ch) << 8).astype("<i4")
            subtype = "PCM_24"
        else:
            samples = np.frombuffer(raw[: len(raw) // (4 * ch) * 4 * ch], dtype="<i4").reshape(-1, ch)
            subtype = "PCM_32"
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part.wav")
        sf.write(str(tmp), samples, sr, subtype=subtype)
        tmp.replace(dst)
        return {"sr": sr, "ch": ch, "dur_s": round(samples.shape[0] / sr, 3) if sr else 0.0}

    def extract_audio(
        self, game_dir: Path, asset: Asset, lines: list[dict[str, Any]], out_dir: Path
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        tmp_dir = out_dir / ".wwise-tmp"
        for line in lines:
            dst = out_dir / f"{line['uid']}.wav"
            try:
                data = self._media_bytes(game_dir, asset, line["entry"])
                try:
                    info = self._write_pcm(data, dst)
                except formats.WwiseFormatError:
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    tmp = tmp_dir / f"{line['uid']}.wem"
                    tmp.write_bytes(data)
                    try:
                        info = media.vgmstream_decode(tmp, dst)
                    finally:
                        tmp.unlink(missing_ok=True)
                results.append({"id": line["id"], "wav": str(dst), **info})
            except BabelDubError as err:
                if err.code != EXIT_ERROR:  # environment problems (no vgmstream) stop the whole asset
                    raise
                results.append({"id": line["id"], "error": str(err)})
            except OSError as exc:
                results.append({"id": line["id"], "error": f"{type(exc).__name__}: {exc}"})
        if tmp_dir.exists() and not any(tmp_dir.iterdir()):
            tmp_dir.rmdir()
        return results

    def build(
        self,
        game_dir: Path,
        asset: Asset,
        replacements: dict[str, Path],
        out_path: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        raise BabelDubError(
            "wwise: native rebuild is not automated (support level B)",
            code=EXIT_USAGE,
            hint=(
                "WAV -> .wem needs WwiseConsole of the same Wwise version as the game (Wwise Authoring, "
                "Audiokinetic account), then the media are swapped into .bnk/.pck; follow "
                "skills/babeldub-pack/references/formats/wwise.md"
            ),
        )
