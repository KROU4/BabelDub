---
name: babeldub-qa
description: "Quality control of a game dub voiced in the original characters' voices: measure intelligibility (ASR, CER), voice similarity of every dubbed line to the original hero, duration and peaks, then decide accept, regen, reref, shorten, retranslate, keep_original or skip and apply the decisions. Use after synthesis, when qa returns attention items, or when the user asks about dub quality, e.g. 'check the dub', 'the voice doesn't sound like him', 'bad synthesis', 'QA report per character', 'проверь озвучку', 'голос не похож', 'брак синтеза', 'отчёт по персонажам', 'revisa el doblaje', 'vérifie le doublage'."
license: MIT
compatibility: "Local agent with shell access; babeldub CLI 0.1.0 with the qc engine installed (babeldub setup --engines qc, faster-whisper and SpeechBrain ECAPA); NVIDIA GPU recommended, CPU works slower."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: dub quality control

This skill covers the `qa` stage. BabelDub's quality bar for a dub: every line sounds **in the voice of its original hero**, is intelligible and fits the length of the original. QA checks this with metrics, decides what to do with defective lines and brings every line to a terminal state: into the build (`accept`) or in the original voice (`keep_original`, `skip`).

You cannot hear audio. Decide **from metrics only**; never draw conclusions "by ear". This skill complements the `qa` role from the `babeldub-localize` skill (`references/roles/qa.md`). Talk to the user in the user's language.

## Commands

```text
babeldub qa                 metrics for the latest processed attempt of each line + auto-decisions
babeldub decide apply       merge qa/decisions.agent.jsonl into the canonical decisions
babeldub report             qa/report.md: per-character totals and the attention list
```

`qa` requires the `post` stage and checks each line's final take: the RVC-converted one (`vc/audio/<uid>.wav`) after `babeldub vc`, otherwise `post/audio/<uid>.wav`; a line converted after it was checked is checked again. Run `babeldub vc` before `qa` when characters have RVC models. `qa` holds the GPU (`state/locks/gpu.lock`): do not run it in parallel with `synth` or `vc`. A repeated `qa` checks only new attempts; attempts already checked are skipped.

## What is measured

| Metric | How | Default threshold |
|---|---|---|
| `cer` — intelligibility | speech recognition of the dub (faster-whisper large-v3-turbo) against `dub_text`, both texts normalized, digits spelled out on both sides | `qa.max_cer` = 0.15 |
| `sim` — is it the same voice | mean of `sim_line` (ECAPA similarity to the original line) and `sim_char` (to the character's reference) | `sim ≥ sim_threshold = max(ratio × sim_ref, qa.min_sim_abs = 0.25)`; ratio `qa.min_sim_ratio_xlang` = 0.45 for a dub in another language than the original audio (the normal case), `qa.min_sim_ratio` = 0.75 for a same-language dub |
| `sim_ref` — the character's baseline | mean pairwise similarity of the character's own clean originals | — |
| `margin` — whose voice is it | similarity to the own character's voice centroid minus the best other character's (`closest_char`); needs two characters with centroids | `margin ≥ qa.min_identity_margin` = −0.05, else reason `identity` |
| `ratio` — length | dub speech / original speech (from `post`) | `qa.max_duration_ratio` = 1.35; for `fixed_length` 1 + `post.fixed_length_tolerance` = 1.03 |
| `mos` — naturalness | UTMOS22 of the dub against `orig_mos`, the median of the character's own originals (`qa/mos_baseline.json`); only when the UTMOS22 checkpoint is downloaded | reason `mos` when `mos < orig_mos − qa.mos_drop` (1.0): only clear outliers |
| `prosody_sim` — intonation | 0.6 × F0 range ratio (dub vs original) + 0.4 × F0 contour correlation | reason `flat` when the original moves ≥ 3 semitones and the dub's range is under `qa.min_f0_range_ratio` (0.5) of it |
| onset/offset (`fixed_length`) | `onset_deltas`/`offset_deltas` of `post` | reason `timing` when a segment starts more than ±80 ms off or ends more than 80 ms late |
| `peak_db`, clipping | peak and share of clipped samples | peak no higher than `post.true_peak_db` + 0.5 dB |
| silence | speech loudness | not quieter than −50 dB |

Lines flagged `nonverbal` (screams, laughs) are measured but never fail on `sim`, `identity` or `cer` (`sim_skipped`, `cer_skipped`) and skip MOS and prosody. Interjections and nonverbal lines classed by `analyze` never reach QA: `synth` keeps their original. `class` in `qa.jsonl` is the analyze class of the original. Each metric in detail, its weak spots, and why similarity is measured against a baseline — [references/metrics.md](references/metrics.md).

## Workflow

1. `babeldub qa`. Output: `checked`, `pass`, `fail`, `regen`, `shorten`, `keep_original`, `accept` (auto-decisions of this run), `attention[]`, `pending`, `cer_median`, `sim_median`, `mos_median`, `prosody_median`, `errors[]`, `next`.
2. **Exit code 0** — everything was decided automatically. If `regen` > 0, run the voice loop (`synth --attempt-next` → `post` → `vc` → `qa`, skill `babeldub-voice`); if `next` mentions waiting lines (bootstrap phase 2), run `synth` → `post` → `vc` → `qa`; if `shorten` > 0, go back to translation (skill `babeldub-translate`, `batch make --from-decisions`).
3. **Exit code 3** — `attention[]`: lines where the auto-decision was not enough or was flagged for review. Work through them (steps 4–6).
4. **Look at the metrics** of the relevant lines only:
   `babeldub query --where verdict=fail --fields id,char,kind,fixed_length,qa.attempt,qa.cer,qa.sim,qa.sim_line,qa.sim_char,qa.sim_threshold,qa.margin,qa.closest_char,qa.mos,qa.orig_mos,qa.prosody_sim,qa.ratio,qa.peak_db,qa.reasons --limit 50`.
   In `query`, QA metrics live under the `qa.` prefix; to compare the text with what was recognized, add `translation.dub_text` and `qa.asr_text`.
5. **Systemic defects.** If more than 20 % of one character's lines in a chunk fail, do not decide line by line: this is a prompt, reference or attribution problem. Return the character in `failing_chars` (to the orchestrator and to the voice and caster roles).
6. **Decisions:** write them to `qa/decisions.agent.jsonl` and run `babeldub decide apply`. The decision table is below; details — [references/decisions.md](references/decisions.md).
7. **Report:** `babeldub report` → `qa/report.md`. From it, the per-character table and no more than 30 attention lines go into the final report for the user, written in the user's language.

## Decisions

| Decision | When | What happens next |
|---|---|---|
| `accept` | metrics within thresholds, or the deviation is acceptable and explained in `note` | the line goes into the build |
| `regen` | a random defect (`cer`, peaks, silence, looping, `mos`, `flat`, `timing`) with a sound prompt, attempts remain | a new attempt with a different seed (after `mos`/`flat` also the next nearest arousal bucket) |
| `reref` | the voice "drifted": low `sim`, especially `sim_line`, on shouting, whispering, noise, a radio effect | a new attempt with a different prompt (`line` → `char`) |
| `shorten` | `ratio` above the limit, `fixed_length` does not fit | the translator shortens the text, then a new synthesis |
| `retranslate` | `cer` high again while the voice is fine: numbers, Latin script, hard words | the translator fixes the text, then a new synthesis |
| `keep_original` | no speech, attempts exhausted, nothing helps | the original plays; reason in `note` |
| `skip` | the line does not need dubbing (a duplicate, a service entry) | the original plays; reason in `note` |

CLI auto-decisions: `pass` → `accept` (a `mixed` line, words with a laugh or groan, with a flag: listen to it); a quality failure (`cer`, `sim`, `identity`, `mos`, `flat`, `timing`, `peak`, `empty`, `silent`) → `regen` while the attempt number is below `tts.max_attempts` (3; 2 for a `short_word` line), then `keep_original` with a flag; length → `shorten`, at most twice, then `keep_original`. The CLI never assigns `reref` or `retranslate` itself — those are agent decisions.

## Thresholds

The default thresholds are a starting point, not the truth: the voice thresholds come from one measured run (Low-Budget Repairs, Russian dub of English originals), the rest are not calibrated on the voices of a particular game. When and how to change them (`babeldub set qa.max_cer=…`), why an absolute similarity threshold does not work, and what to do after changing a threshold — [references/thresholds.md](references/thresholds.md).

## Rules

1. The game folder is read-only.
2. The canon (`qa/qa.jsonl`, `qa/decisions.jsonl`, `qa/baseline.json`, `qa/report.md`) is written by the CLI. The agent writes only `qa/decisions.agent.jsonl`.
3. Do not read project files whole: use `query` with `--fields` and `--limit`, and read the report section by section.
4. Do not address the human directly: put anything unclear into `human_pending` of the role's response, with a reason; this does not block the build (the line gets `keep_original`).
5. Bring the human no more than 30 lines per round, and only with a concrete question.
6. Every line must reach a terminal state or a queue (`regen`, `reref`, `shorten`, `retranslate`).

## References

- What each metric means, where it goes wrong, how to read `sim_line` and `sim_char` — [references/metrics.md](references/metrics.md). Read it the first time you review defects.
- Default thresholds, calibration on the pilot, when to change them — [references/thresholds.md](references/thresholds.md). Read it when there are suspiciously many or few defects.
- The decision tree, the `qa/decisions.agent.jsonl` format, typical cases with examples — [references/decisions.md](references/decisions.md). Read it before writing decisions.
