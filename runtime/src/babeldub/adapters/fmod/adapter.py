"""FMOD Studio adapter: .bank (RIFF 'FEV ' + FSB5) and raw .fsb sound banks.

Level A: lines are listed from FSB5 headers, decoded natively (PCM, FADPCM) or with vgmstream-cli
(Vorbis and other codecs), and rebuilt natively: every FSB that receives replacements is
re-encoded in PCM16 or FADPCM with the same subsound count, order and names, then the bank's
SNDH/SND/RIFF fields are patched. Event paths come from *.strings.bank (STDT) and programmer
instruments (PRIB); speakers and scenes are name-based hints.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import soundfile as sf

from babeldub.adapters.base import Adapter, Asset
from babeldub.adapters.fmod import fsb5 as F
from babeldub.adapters.fmod import heuristics as H
from babeldub.adapters.fmod import pcm, vgm
from babeldub.adapters.fmod.bank import BankError, Container, open_container
from babeldub.adapters.fmod.bankinfo import (
    ADAPTER,
    BankInfo,
    event_index,
    load_bank,
    parse_line_id,
    strings_banks_near,
)
from babeldub.adapters.fmod.meta import audio_table_fsb, programmer_keys
from babeldub.adapters.fmod.rebuild import build_bank
from babeldub.adapters.fmod.verify import verify_build
from babeldub.core.ids import asset_id, line_id, uid
from babeldub.core.output import EXIT_USAGE, BabelDubError

_EXTENSIONS = {".bank", ".fsb"}


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _stem(path: Path) -> str:
    return path.name[: -len(path.suffix)] if path.suffix else path.name


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


class FmodAdapter(Adapter):
    name = ADAPTER
    level = "A"
    description = "FMOD Studio banks (.bank) and FSB5 sound banks (.fsb): extract, PCM16/FADPCM rebuild"

    # ------------------------------------------------------------------ discover
    def discover(self, game_dir: Path) -> list[Asset]:
        game_dir = Path(game_dir)
        found: list[tuple[Path, Container]] = []
        for root, _dirs, files in os.walk(game_dir):
            for fname in sorted(files):
                path = Path(root) / fname
                if path.suffix.lower() not in _EXTENSIONS:
                    continue
                try:
                    found.append((path, open_container(path)))
                except (BankError, OSError):
                    continue
        strings_rel = [
            p.relative_to(game_dir).as_posix()
            for p, c in found
            if c.kind == "bank" and c.find(b"STDT") and any(ch.size for ch in c.find(b"STDT"))
        ]
        assets: list[Asset] = []
        guid_groups: dict[str, list[str]] = defaultdict(list)
        for path, cont in found:
            if cont.bank_guid:
                guid_groups[cont.bank_guid].append(_stem(path))
        for path, cont in found:
            rel = path.relative_to(game_dir).as_posix()
            stem = _stem(path)
            fsb_rows: list[dict[str, Any]] = []
            counts: list[int] = []
            if not cont.encrypted:
                with path.open("rb") as fh:
                    for loc in cont.fsbs:
                        try:
                            fsb = F.read_fsb5(fh, loc.offset)
                        except F.Fsb5Error:
                            cont.encrypted = True
                            break
                        counts.append(len(fsb.subsounds))
                        fsb_rows.append(
                            {
                                "sub": f"fsb{loc.index}",
                                "codec": fsb.codec_name,
                                "n": len(fsb.subsounds),
                                "dur_s": round(sum(s.duration_s for s in fsb.subsounds), 1),
                            }
                        )
            has_audio = bool(cont.fsbs)
            is_strings = rel in strings_rel
            role = H.bank_role(stem, has_audio)
            locale = H.locale_from_name(stem)
            if locale is None and cont.bank_guid:
                locale = H.locale_from_siblings(stem, guid_groups[cont.bank_guid])
            info: dict[str, Any] = {
                "kind": cont.kind,
                "fsbs": fsb_rows,
                "subsounds": sum(counts),
                "strings": is_strings,
                "encrypted": cont.encrypted,
            }
            if cont.kind == "bank":
                info["fmt_version"] = cont.fmt_version
                info["bank_guid"] = cont.bank_guid
                if not cont.encrypted:
                    at = audio_table_fsb(cont, counts)
                    info["audio_table"] = f"fsb{at}" if at is not None else None
                    info["programmer_keys"] = len(programmer_keys(cont))
                if len(guid_groups.get(cont.bank_guid or "", [])) > 1:
                    info["localized_group"] = sorted(guid_groups[cont.bank_guid or ""])
            if strings_rel:
                info["strings_banks"] = strings_rel
            assets.append(
                Asset(
                    asset_id=asset_id(ADAPTER, rel),
                    adapter=ADAPTER,
                    relpath=rel,
                    role=role,
                    size=path.stat().st_size,
                    sha1=_sha1(path),
                    locale=locale,
                    level="C" if cont.encrypted else self.level,
                    info=info,
                )
            )
        return assets

    # ------------------------------------------------------------------ list_lines
    def list_lines(self, game_dir: Path, asset: Asset) -> list[dict[str, Any]]:
        game_dir = Path(game_dir)
        src = game_dir / asset.relpath
        bank = load_bank(src)
        if not bank.fsbs:
            return []
        events = event_index(game_dir, asset.relpath, bank, asset.info.get("strings_banks"))
        stem = _stem(src)
        role = asset.role if asset.role in ("voice", "audio") else H.bank_role(stem, True)
        seen: Counter[str] = Counter()
        lines: list[dict[str, Any]] = []
        order = 0
        for k, fsb in enumerate(bank.fsbs):
            sub = f"fsb{k}"
            base = bank.subsong_base(k)
            for s in fsb.subsounds:
                key = s.name or f"{sub}_{s.index:04d}"
                event_path, table_key, ev_src = events.resolve(key)
                kind, fixed = H.classify(key, event_path, stem, role)
                flags = H.line_flags(key, kind)
                if seen[key]:
                    flags.append("duplicate")
                if s.nsamples == 0:
                    flags.append("no_audio")
                seen[key] += 1
                lid = line_id(ADAPTER, asset.relpath, sub, s.index)
                entry: dict[str, Any] = {
                    "key": key,
                    "sub": sub,
                    "index": s.index,
                    "event_path": event_path,
                    "fsb_codec": fsb.codec_name,
                    "subsong": base + s.index + 1,
                }
                if ev_src:
                    entry["event_src"] = ev_src
                if table_key:
                    entry["table_key"] = table_key
                if bank.audio_table == k:
                    entry["audio_table"] = True
                lines.append(
                    {
                        "id": lid,
                        "uid": uid(lid),
                        "adapter": ADAPTER,
                        "asset_id": asset.asset_id,
                        "entry": entry,
                        "kind": kind,
                        "locale": asset.locale,
                        "audio": {
                            "sr": s.rate,
                            "ch": s.channels,
                            "dur_s": round(s.duration_s, 3),
                            "codec": fsb.codec_name,
                            "samples": s.nsamples,
                        },
                        "speaker_hint": H.speaker_hint(event_path),
                        "scene": H.scene_hint(key, event_path, kind),
                        "order": order,
                        "fixed_length": fixed,
                        "flags": flags,
                    }
                )
                order += 1
        return lines

    # ------------------------------------------------------------------ extract_audio
    def extract_audio(
        self, game_dir: Path, asset: Asset, lines: list[dict[str, Any]], out_dir: Path
    ) -> list[dict[str, Any]]:
        game_dir = Path(game_dir)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        src = game_dir / asset.relpath
        bank = load_bank(src)
        wanted: list[tuple[dict[str, Any], int, int]] = []
        for line in lines:
            k, i = parse_line_id(line["id"], asset.relpath)
            if k >= len(bank.fsbs) or i >= len(bank.fsbs[k].subsounds):
                raise BabelDubError(f"line {line['id']} is not in {asset.relpath}", code=EXIT_USAGE)
            wanted.append((line, k, i))
        results: list[dict[str, Any]] = []
        via_vgm: list[tuple[dict[str, Any], int, int]] = []
        for line, k, i in wanted:
            fsb = bank.fsbs[k]
            if fsb.codec not in F.NATIVE_DECODE:
                via_vgm.append((line, k, i))
                continue
            sub = fsb.subsounds[i]
            data = F.decode_native(fsb, sub, bank.read_data(k, sub))
            wav = out_dir / f"{line.get('uid') or uid(line['id'])}.wav"
            pcm.write_int16(wav, data, sub.rate)
            results.append(self._row(line, wav))
        if via_vgm:
            with tempfile.TemporaryDirectory(prefix=".vgm-", dir=out_dir) as tmp_name:
                files = vgm.decode_subsongs(src, [bank.subsong(k, i) for _, k, i in via_vgm], Path(tmp_name))
                for line, k, i in via_vgm:
                    wav = out_dir / f"{line.get('uid') or uid(line['id'])}.wav"
                    os.replace(files[bank.subsong(k, i)], wav)
                    results.append(self._row(line, wav))
        return results

    @staticmethod
    def _row(line: dict[str, Any], wav: Path) -> dict[str, Any]:
        info = sf.info(str(wav))
        return {
            "id": line["id"],
            "wav": str(wav),
            "sr": int(info.samplerate),
            "ch": int(info.channels),
            "dur_s": round(info.frames / info.samplerate, 3) if info.samplerate else 0.0,
        }

    # ------------------------------------------------------------------ build
    def build(
        self,
        game_dir: Path,
        asset: Asset,
        replacements: dict[str, Path],
        out_path: Path,
        options: dict[str, Any],
    ) -> dict[str, Any]:
        game_dir = Path(game_dir)
        out_path = Path(out_path)
        if _inside(out_path, game_dir):
            raise BabelDubError(
                "refusing to write into the game folder",
                code=EXIT_USAGE,
                hint="build into the project (build/out/<relpath>); `babeldub install` puts files in place",
            )
        src = game_dir / asset.relpath
        bank = load_bank(src)
        targets = {parse_line_id(lid, asset.relpath): Path(wav) for lid, wav in replacements.items()}
        if not targets:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out_path)
            return {
                "out": str(out_path),
                "replaced": 0,
                "kept": sum(len(f.subsounds) for f in bank.fsbs),
                "codec": "copy",
                "size": out_path.stat().st_size,
                "sha1": _sha1(out_path),
            }
        summary = build_bank(bank, targets, out_path, options)
        summary["replaced_ids"] = sorted(replacements)
        return summary

    # ------------------------------------------------------------------ verify
    def verify(self, game_dir: Path, asset: Asset, built: Path, options: dict[str, Any]) -> dict[str, Any]:
        game_dir = Path(game_dir)
        src = game_dir / asset.relpath
        bank: BankInfo = load_bank(src)
        listed = asset.info.get("strings_banks")
        events = event_index(game_dir, asset.relpath, bank, listed)
        strings = strings_banks_near(game_dir, asset.relpath, listed)
        return verify_build(game_dir, src, Path(built), asset.relpath, options, events, strings)
