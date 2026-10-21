"""Characters: grouping lines by speaker, tiers, selection policy, agent labels, casting sheet."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import soundfile as sf

VOICED_KINDS = frozenset({"dialogue", "cutscene", "bark"})
NO_HINT = frozenset({"", "unknown", "none", "null", "n/a", "na", "-", "?", "narrator?"})
UNKNOWN_ID = "unknown"
GENDERS = ("m", "f", "u")
AGES = ("child", "adult", "old", "u")
POLICIES = ("all", "main", "list")
SECONDARY_MIN_SHARE = 0.02
LABEL_FIELDS = ("name", "name_tgt", "gender", "age", "notes")


def voiced_lines(lines: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lines that get a new voice: dialogue/cutscene/bark that have original audio."""
    out = []
    for line in lines:
        if line.get("kind") not in VOICED_KINDS:
            continue
        if "no_audio" in (line.get("flags") or []):
            continue
        out.append(line)
    return out


def wav_path(root: Path, line: dict[str, Any]) -> Path | None:
    rel = (line.get("audio") or {}).get("wav")
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else root / p


def line_duration(root: Path, line: dict[str, Any]) -> float:
    dur = (line.get("audio") or {}).get("dur_s")
    if isinstance(dur, (int, float)) and dur > 0:
        return float(dur)
    wav = wav_path(root, line)
    if wav is not None and wav.exists():
        try:
            return float(sf.info(str(wav)).duration)
        except RuntimeError:
            return 0.0
    return 0.0


def line_text(line: dict[str, Any]) -> str | None:
    text = (line.get("text") or {}).get("src")
    return text.strip() if isinstance(text, str) and text.strip() else None


def hint_name(line: dict[str, Any]) -> str | None:
    hint = line.get("speaker_hint")
    if not isinstance(hint, str):
        return None
    name = " ".join(hint.split())
    return None if name.lower() in NO_HINT else name


_NON_WORD_RE = re.compile(r"[\W_]+")
_RESERVED = frozenset({UNKNOWN_ID, "con", "prn", "aux", "nul"} | {f"com{i}" for i in range(10)} | {f"lpt{i}" for i in range(10)})


def name_key(text: str) -> str:
    """Case/punctuation-insensitive key of a name in any script ("Тадеуш!" -> "тадеуш")."""
    folded = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return _NON_WORD_RE.sub("-", folded).strip("-")


def char_id_for(name: str) -> str:
    """Stable, filesystem-safe id of a metadata character (any script; never cluster_NN/unknown)."""
    slug = name_key(name)[:48].strip("-")
    if not slug:
        return "char-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"char-{slug}" if slug in _RESERVED else slug


def new_character(char_id: str, name: str, source: str, n_lines: int, speech_s: float, conf: float | None) -> dict[str, Any]:
    return {
        "char_id": char_id,
        "name": name,
        "name_tgt": None,
        "gender": "u",
        "age": "u",
        "n_lines": n_lines,
        "speech_s": round(speech_s, 2),
        "share": 0.0,
        "tier": "minor",
        "selected": False,
        "source": source,
        "conf": None if conf is None else round(conf, 3),
        "notes": "",
    }


def assign_tiers(chars: list[dict[str, Any]], main_share: float, secondary_min_share: float = SECONDARY_MIN_SHARE) -> None:
    """main: smallest set (by speech, descending) covering main_share of all speech;
    secondary: the next ones with >= 2 % of speech; minor: the rest. The mixed `unknown`
    group never ranks (it is not one voice)."""
    ranked = [c for c in chars if c["char_id"] != UNKNOWN_ID]
    use_lines = sum(c["speech_s"] for c in ranked) <= 0
    weight = (lambda c: float(c["n_lines"])) if use_lines else (lambda c: float(c["speech_s"]))
    total = sum(weight(c) for c in ranked)
    ranked.sort(key=lambda c: (-weight(c), c["char_id"]))
    covered = 0.0
    main_done = False
    for c in chars:
        c["tier"] = "minor"
        c["share"] = 0.0
    for i, c in enumerate(ranked):
        share = weight(c) / total if total > 0 else 0.0
        c["share"] = round(share, 4)
        if not main_done and (i == 0 or covered < main_share - 1e-9):
            c["tier"] = "main"
            covered += share
            if covered >= main_share - 1e-9:
                main_done = True
        elif share >= secondary_min_share:
            c["tier"] = "secondary"
        else:
            c["tier"] = "minor"
        if c["tier"] != "main":
            main_done = True


def match_char(chars: list[dict[str, Any]], token: str) -> dict[str, Any] | None:
    """Find a character by id, name or target-language name (case and punctuation insensitive)."""
    want = name_key(token)
    if not want:
        return None
    for field_name in ("char_id", "name", "name_tgt"):
        for c in chars:
            value = c.get(field_name)
            if isinstance(value, str) and (value == token or name_key(value) == want):
                return c
    return None


def apply_policy(chars: list[dict[str, Any]], policy: str, names: Iterable[str]) -> list[str]:
    """Set `selected` on every character; returns tokens of `names` that matched nothing."""
    unmatched: list[str] = []
    if policy == "all":
        for c in chars:
            c["selected"] = True
        return unmatched
    if policy == "main":
        for c in chars:
            c["selected"] = c["tier"] == "main"
        return unmatched
    if policy != "list":
        raise ValueError(f"unknown cast policy '{policy}'")
    for c in chars:
        c["selected"] = False
    for token in names:
        hit = match_char(chars, str(token))
        if hit is None:
            unmatched.append(str(token))
        else:
            hit["selected"] = True
    return unmatched


def carry_labels(chars: list[dict[str, Any]], previous: dict[str, dict[str, Any]]) -> None:
    """Keep agent/human labels of characters that survive a re-run (same char_id)."""
    for c in chars:
        old = previous.get(c["char_id"])
        if not old or not old.get("labeled"):
            continue
        for key in LABEL_FIELDS:
            if key in old:
                c[key] = old[key]
        c["labeled"] = True


def validate_label(row: dict[str, Any], known: set[str]) -> list[str]:
    problems: list[str] = []
    cid = row.get("char_id")
    if not isinstance(cid, str) or not cid:
        return ["missing 'char_id'"]
    if cid not in known:
        problems.append(f"unknown char_id '{cid}'")
    extra = sorted(set(row) - {"char_id", *LABEL_FIELDS})
    if extra:
        problems.append(f"unknown fields {extra} (allowed: char_id, {', '.join(LABEL_FIELDS)})")
    for key in ("name_tgt", "notes"):
        if key in row and row[key] is not None and not isinstance(row[key], str):
            problems.append(f"'{key}' must be a string or null")
    if "name" in row and (not isinstance(row["name"], str) or not row["name"].strip()):
        problems.append("'name' must be a non-empty string")
    if "gender" in row and row["gender"] not in GENDERS:
        problems.append(f"gender must be one of {'|'.join(GENDERS)}")
    if "age" in row and row["age"] not in AGES:
        problems.append(f"age must be one of {'|'.join(AGES)}")
    return problems


def apply_label(char: dict[str, Any], row: dict[str, Any]) -> bool:
    changed = False
    for key in LABEL_FIELDS:
        if key in row and char.get(key) != row[key]:
            char[key] = row[key]
            changed = True
    if changed or not char.get("labeled"):
        char["labeled"] = True
    return changed


# ---- casting sheet -------------------------------------------------------------------------
_TIER_TEXT = {"main": "main", "secondary": "secondary", "minor": "minor"}
_SOURCE_TEXT = {"metadata": "metadata", "folder": "folder", "cluster": "voice cluster", "unknown": "unknown"}
_GENDER_TEXT = {"m": "m", "f": "f", "u": "?"}
_AGE_TEXT = {"child": "child", "adult": "adult", "old": "old", "u": "?"}
_POLICY_TEXT = {
    "all": "all characters",
    "main": "main characters (the smallest set that covers {share:.0%} of all speech)",
    "list": "list: {names}",
}


def _cell(text: Any, limit: int = 90) -> str:
    if text is None:
        return "—"
    s = " ".join(str(text).split()).replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def render_casting_sheet(
    *,
    game_name: str,
    target_lang: str,
    policy: str,
    policy_list: list[str],
    main_share: float,
    chars: list[dict[str, Any]],
    refs_by_char: dict[str, list[dict[str, Any]]],
    samples: dict[str, list[dict[str, Any]]],
    uncertain: list[str],
    warnings: list[str],
) -> str:
    total_s = sum(c["speech_s"] for c in chars)
    selected = [c for c in chars if c.get("selected")]
    policy_text = _POLICY_TEXT.get(policy, policy).format(share=main_share, names=", ".join(policy_list) or "—")
    out: list[str] = [
        f"# Casting sheet — {game_name}",
        "",
        f"Target language: **{target_lang}**. Selection policy: **{policy}** — {policy_text}.",
        f"Characters: **{len(chars)}**, selected: **{len(selected)}**, total speech: **{total_s / 60:.1f} min**.",
        "",
        "BabelDub principle: every hero speaks the target language **in their own voice** — the same timbre,",
        "the same emotion and delivery. The voice prompt is the original line itself (`ref_strategy=line`);",
        "for short and noisy lines it is the character reference from this table.",
        "",
        "| # | Character | ID | Gender | Age | Name (target) | Lines | Speech, min | Share | Tier | Selected | Source | Reference | Sample text |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(chars, 1):
        refs = refs_by_char.get(c["char_id"]) or []
        chosen = next((r for r in refs if r.get("chosen")), refs[0] if refs else None)
        ref_cell = f"`{chosen['wav']}` ({chosen['dur_s']:.1f} s)" if chosen else "none"
        sample = (samples.get(c["char_id"]) or [{}])[0].get("text")
        out.append(
            "| {i} | {name} | `{cid}` | {g} | {a} | {nt} | {n} | {m:.1f} | {sh:.0%} | {tier} | {sel} | {src} | {ref} | {ex} |".format(
                i=i,
                name=_cell(c.get("name"), 40),
                cid=c["char_id"],
                g=_GENDER_TEXT.get(c.get("gender", "u"), "?"),
                a=_AGE_TEXT.get(c.get("age", "u"), "?"),
                nt=_cell(c.get("name_tgt"), 40),
                n=c["n_lines"],
                m=c["speech_s"] / 60,
                sh=c.get("share", 0.0),
                tier=_TIER_TEXT.get(c["tier"], c["tier"]),
                sel="yes" if c.get("selected") else "no",
                src=_SOURCE_TEXT.get(c["source"], c["source"]),
                ref=ref_cell,
                ex=_cell(sample, 80),
            )
        )
    if uncertain:
        out += ["", "## Needs review", ""]
        by_id = {c["char_id"]: c for c in chars}
        for cid in uncertain:
            c = by_id.get(cid)
            if c is None:
                continue
            conf = "—" if c.get("conf") is None else f"{c['conf']:.2f}"
            why = "mix of unidentified voices" if cid == UNKNOWN_ID else f"low cluster confidence ({conf})"
            out.append(f"- `{cid}` — {why}, {c['n_lines']} lines, {c['speech_s'] / 60:.1f} min.")
    cluster_ids = [c["char_id"] for c in chars if c["source"] in ("cluster", "unknown")]
    if cluster_ids:
        out += [
            "",
            "## Cluster line samples",
            "",
            "A cluster is a voice that has no name in the metadata. Use its lines to work out who it is.",
        ]
        for cid in cluster_ids[:40]:
            c = next(x for x in chars if x["char_id"] == cid)
            out += ["", f"### `{cid}` — {c['n_lines']} lines, {c['speech_s'] / 60:.1f} min", ""]
            for s in (samples.get(cid) or [])[:8]:
                out.append(f"- `{s['id']}` ({s['dur_s']:.1f} s): “{_cell(s.get('text'), 140)}”")
        if len(cluster_ids) > 40:
            out.append(f"\n… {len(cluster_ids) - 40} more clusters: `babeldub query --where char=<id>`.")
    if warnings:
        out += ["", "## Warnings", ""] + [f"- {_cell(w, 300)}" for w in warnings]
    out += [
        "",
        "## What the casting agent should do",
        "",
        "1. For every cluster and every character without a gender or age, add a row to `cast/labels.agent.jsonl`:",
        '   `{"char_id": "cluster_01", "name": "Maciek", "name_tgt": "Мачек", "gender": "m", "age": "adult", "notes": "..."}`',
        "   (`gender`: m|f|u, `age`: child|adult|old|u; fields that do not change can be omitted).",
        "2. Merge the labels: `babeldub cast apply-labels`.",
        "3. Change the selection if needed: `babeldub cast select --policy main|all` or `babeldub cast select --chars A,B`.",
        "",
    ]
    return "\n".join(out)
