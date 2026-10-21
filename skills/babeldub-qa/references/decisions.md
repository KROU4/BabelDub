# Decisions on defects: tree, format, examples

Read this before writing `qa/decisions.agent.jsonl`.

## 1. Format of `qa/decisions.agent.jsonl`

UTF-8 without BOM, one object per line:

```json
{"id": "fmod:StreamingAssets/Dialogue_en-US.bank#fsb1:0042", "decision": "reref", "attempt": 2, "note": "sim 0.41 / sim_ref 0.62, sim_line low: shout with radio effect"}
```

| Field | Required | Value |
|---|---|---|
| `id` | yes | an existing line `id` |
| `decision` | yes | `accept` \| `regen` \| `reref` \| `shorten` \| `retranslate` \| `keep_original` \| `skip` |
| `attempt` | no | integer ≥ 1; defaults to the line's latest processed attempt |
| `note` | no | string: why this was decided (metrics, reason); for `shorten` — the actual `ratio` and how much to cut |

Do not write the `by` and `ts` fields: the CLI sets them. `babeldub decide apply` validates the whole file; if even one line is invalid (unknown `id`, a decision not on the list, a non-integer `attempt`), nothing is applied (exit code 2, list in `errors`). Identical decisions are not duplicated on repeat. An agent decision for an attempt takes priority over the CLI auto-decision for the same attempt.

**Important:** `accept` applies to the latest processed attempt. In v0.1 `post` keeps the file of the latest attempt only, so an earlier attempt cannot be accepted: the build marks such a line `stale_attempt` and does not take it. If an early attempt was the best and the latest is worse, use `regen` (a new attempt) or `keep_original`.

## 2. Decision tree

Go from top to bottom; the first matching rule decides:

1. **No speech in substance** (the line is laughter, a cough, a sigh, music; `dub_text` is an interjection and the model produced garbage) → `keep_original`.
2. **The line does not need dubbing** (a duplicate of another line that `link` did not flag, a service entry, a test string) → `skip`. Lines flagged `alt_take`/`duplicate` never reach QA: they follow their leader, and the build gives them the leader's accepted dub (`babeldub query --where follows!=null`); decide the leader only.
3. **Service reason** (`missing_file`, `qc_error`, `no_dub_text`) → do not decide: fix the cause (`post`, rerun `qa`, translation).
4. **Attempts exhausted** (`attempt` ≥ `tts.max_attempts`) and the defect is not about length → `keep_original`; in `note`, say what did not help.
5. **The voice "drifted"** (`sim` < `min_sim_ratio × sim_ref`):
   - `sim_line` low, `sim_char` normal, the prompt was `line` → `reref`;
   - both low, first attempt → `regen`;
   - both low again → `reref`;
   - widespread for a character (more than 20 % in a chunk) → do not decide line by line; return the character in `failing_chars`.
6. **Intelligibility** (`cer` > `max_cer`), the voice is fine:
   - compare `dub_text` and `asr_text`; if the difference is only in a name, a number or an interjection → `accept` with a `note`, or `retranslate` (numbers as words, `pronounce`);
   - skipped words, repetitions, foreign words → `regen`;
   - high `cer` a second time in a row on the same text → `retranslate`, with a `note` on what to simplify.
7. **Length** (`ratio` above the limit, or `fixed_length` does not fit):
   - `ratio` above 2 → `regen` (model failure);
   - otherwise → `shorten`; in `note`, give the `ratio` and how many syllables to cut;
   - two shortenings already done → `keep_original`, or `accept` with a `note` if audio in this game is not tied to timing (only for ordinary dialogue, not for `fixed_length`).
8. **Peaks, clipping, silence** → `regen`.
9. **Everything within thresholds** → `accept`.

## 3. Examples

| Metrics | Decision | `note` |
|---|---|---|
| `cer` 0.05, `sim` 0.52, `sim_ref` 0.60 (0.87 × baseline), `ratio` 1.1 | `accept` | — |
| `cer` 0.40; `dub_text` «Збышек, иди сюда»; `asr_text` «збышик иди сюда» | `accept` | "ASR misspells the name; the rest matches" |
| `cer` 0.60; `asr_text` contains half the phrase and a repetition | `regen` | "skipped words and repetition, attempt 1" |
| `sim` 0.30, `sim_line` 0.22, `sim_char` 0.38, `sim_ref` 0.58; the line is a shout over the radio | `reref` | "line prompt carries the radio effect; use char" |
| `ratio` 1.22, `fixed_length` true | `shorten` | "ratio 1.22 on a cutscene: cut ~3 syllables of 14" |
| `ratio` 3.4, `cer` 0.7 | `regen` | "runaway generation" |
| `synth` keeps failing with `error: "runaway"` (every prompt looped), attempts exhausted, the line never reached `post` | `keep_original` (or `retranslate` with a simpler text) | "loops in every prompt mode" |
| attempt 3, `sim` still low | `keep_original` | "3 attempts, voice drifts on whisper" |
| `dub_text` «Ха-ха», the model produced noise | `keep_original` | "laughter, original kept" |

## 4. After `decide apply`

- `regen`, `reref` → `babeldub synth --attempt-next` → `babeldub post` → `babeldub qa` (skill `babeldub-voice`).
- `shorten`, `retranslate` → `babeldub batch make --from-decisions` → translator → `batch submit` → `babeldub synth` → `post` → `qa` (skill `babeldub-translate`).
- `accept`, `keep_original`, `skip` → terminal; `babeldub report` and the build (skill `babeldub-pack`).

## 5. What to escalate to the human

No more than 30 lines per round, and only when no decision fits without a judgment of meaning or taste: disputed voice attribution, a plot-critical line that cannot be shortened, profanity or sensitive content. Such lines get `keep_original` (they do not block the build) and go into the "Needs a human decision" section of the final report.
