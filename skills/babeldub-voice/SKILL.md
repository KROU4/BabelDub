---
name: babeldub-voice
description: "Dub game lines in the original character's own voice: GPU-first local voice cloning (VoxCPM2 with the hero's long original reference and per-line delivery tags by default, Qwen3-TTS and Chatterbox as fallbacks), best-of-N takes, exact-length fit to the original, RVC timbre transfer per main character, the pilot and batch synthesis. Use for the synth, post and vc stages and for voice questions. Example requests: 'dub the voices', 'voice it in the hero's voice', 'same voice in another language', 'clone the voices', 'train the voice model', 'why does the voice sound different', 'озвучь голосом героя', 'тот же голос на русском', 'сделай русскую озвучку', 'клонирование голоса', 'обучи голос персонажа', 'нейроозвучка', 'doblar el juego', 'doblaje con la voz original'."
license: MIT
compatibility: "Local agent with shell access; babeldub CLI 0.1.0 (install via the babeldub-doctor skill); NVIDIA GPU with 8+ GB VRAM for the default chain (VoxCPM2 + RVC), smaller GPUs and the CPU fall back to Qwen3-TTS; engine venvs and weights are installed by babeldub setup."
metadata:
  babeldub-cli: "0.3.0"
---

# BabelDub: voice — dubbing in the original character's voice

This skill covers the `synth`, `post` and `vc` stages. Its job is to make every translated line sound like **its own original character**: the same timbre, emotion, delivery and pace, only in the target language. This is dubbing, not "text-to-speech by a narrator". Facial animation and lip sync are not rebuilt.

The skill complements the `voice` role of the `babeldub-localize` skill (`references/roles/voice.md`). Stage order and exit codes are in that skill's `references/pipeline.md`.

Talk to the user in the user's language. Keep commands, field names and identifiers as they are.

## The default chain

Chosen in blind listening rounds on a real game (Low-Budget Repairs, English → Russian) over Qwen3-TTS, Seed-VC and fine-tunes; in v0.3 a blind round on the 38-line test set preferred the delivery tags over v0.2's target-language prompt bank:

1. **TTS: VoxCPM2** (`tts.engine=auto` on an NVIDIA GPU with 8 GB+ and the `voxcpm` env; 30 languages, 48 kHz, Apache-2.0).
2. **The long original reference plus delivery tags** (`tts.ref_strategy=auto` = `tags` for VoxCPM2). Every line is voiced with the character's *long timbre reference*: 3–4 clean original lines (4–7.5 s each) joined into ~15–20 s (`tts.tags_reference=main`, the default: the calm/main one for the whole role; `bucket`: the one of the arousal bucket nearest to the line's original). The delivery of the line comes from a short English control text VoxCPM2 reads before the dub text, for example `(shouting, slightly faster)`, made of the line's **delivery tags**: measured on the original line against the same character's other lines, or corrected by the translator agent from the scene. Every line is voiced in the first run: no phases, no waiting lines. Details below and in [references/ref-strategy.md](references/ref-strategy.md), section 2.2.
3. **Best of N** (`tts.best_of=3`): N takes with different seeds. With the `qc` env installed (`tts.best_of_mode=auto` → `score`) every take is kept, and after the TTS model is unloaded `synth` scores them (prosody similarity to the original, MOS, length, voice similarity; CER and length as gates) and keeps the best; otherwise the take whose speech length is closest to the original's wins.
4. **Exact fit** (`post`): never slower (a short dub keeps its tempo, the rest is silence); a long one loses part of its pauses, then speeds up to at most `post.tempo_max` (1.15, pitch kept); cutscene (`fixed_length`) chunks start where the original's segments start; the speech starts where the original's does, and the file has exactly the original's sample count, rate and channels, at the original speech loudness.
5. **RVC timbre transfer** (`vc`): one RVC v2 model per main character, trained once on its clean original lines (`babeldub vc train`), converts every dub of that character. It works for any target language.
6. **QA** (Whisper with VAD, ECAPA with cross-language calibration) accepts or sends lines back.

Measured on Low-Budget Repairs: VoxCPM2 + RVC CER 0.045 (Qwen3-TTS + RVC 0.050), ECAPA similarity to the originals ~0.65–0.70 (originals among themselves 0.77).

## Core principles

1. **The timbre comes from the original actor.** The voice prompt is always made of original recordings (the long reference, or for other engines the original line itself with its text), never of a generic voice.
2. **Delivery follows the original.** With VoxCPM2 the delivery tags measured on the original line (or the translator's correction) steer every take. For Qwen3-TTS and Chatterbox the default `auto` strategy uses the original line as an ICL prompt (`line`), or the character's best clean reference (`char`) when the line is too short, untranscribed, noisy or nonverbal; ICL needs the exact transcript, so an implausible one switches to the character reference or timbre only (`line-xvec`). Details: [references/ref-strategy.md](references/ref-strategy.md).
3. **Plain text.** VoxCPM2 and Qwen3-TTS Base turn U+0301 stress marks into extra sounds, and VoxCPM2 reads `+` as "plus": the CLI strips marks for every model except an explicit stress model (`qwen3-tts-…-ru-stress-cf`). The glossary `pronounce` is applied to the dub text. `tts.stress=on` (off by default) marks Russian homographs with RUAccent and sends them in the form the engine reads (VoxCPM2: a CAPITAL stressed vowel).
4. **What is not words is not dubbed.** `analyze` classes: interjections and nonverbal lines (laughs, groans, "Hmm…") keep the original (`skipped.keep_original`, an automatic `keep_original` decision); short words ("Freeze!") are voiced from the line itself with 6 candidates and one regen at most; `mixed` lines are voiced and flagged for listening.
5. **Alternative takes and duplicates** (`alt_take`, `duplicate`) are not voiced: they follow their leader, and the build gives them the leader's accepted dub.
6. **The translation must fit the intonation and length of the original.** With official texts the translator adapts them for speech (`translation.mode=adapt`, the `babeldub-translate` skill).
7. **QA compares the synthesized voice with the original character** (voice, words, naturalness, intonation range) and sends drifted, unnatural or monotone takes back.

## Delivery tags (`tts.ref_strategy=tags`)

- **Vocabulary**, at most 3 tags per line: `shouting`, `whispered`, `urgent`, `energetic`, `calm`, `sad`, `soft`, `loud`, `quick`, `slow`, `expressive`, `flat`. Each becomes an English phrase of the control text ("shouting", "whispering", "fast pace", "flat intonation"…).
- **Measured** from the `analyze` prosody: pitch, loudness, voiced share, pitch range and speaking rate of the line as robust z-scores against the same character's other lines. Only strong deviations become tags, so most lines have none: their normal delivery is in the reference already. No tags without `analyze` or for a character with fewer than 5 other comparable lines.
- **Corrected by the translator.** Every batch line carries the measured tags as `delivery_hint`; the translator returns `delivery` only where the scene calls for another delivery (skill `babeldub-translate`). Delivery is decided by agents and the measurement, never asked from the user.
- **Precedence:** the translator's `delivery` (even `[]`, explicitly neutral) > the measured tags > none; a `delivery` that repeats the measured tags is the measured delivery. `tts.delivery_tags`: `on` (default) | `measured` (ignore the translator's `delivery`) | `off` (no control text).
- **Regens:** a `regen` after a `cer` failure, and any `regen` from the 3rd take of the same text on (a new translation starts again), drops the control text (`tags_source: dropped`); after `flat` or `mos` the tags stay with a new seed.
- **Reported** per take in `synth/results.jsonl` (`control`, `tags`, `tags_source`: `agent | measured | none | dropped`), in the `synth` output (`delivery{by_source, tags}`, also in `--dry-run`) and in `babeldub status` (`counts.delivery`).
- **When to change them** (record the reason in the report): `tts.delivery_tags=measured` when the translators' `delivery` contradicts the hints and the scenes on many lines; `off` when a character's tagged takes fail `cer`/`mos` clearly more often than its untagged ones or the tags over-act a deliberately flat voice; `tts.tags_reference=bucket` when a character's shouts or whispers sound like another person from the calm reference. Change them before the batch: afterwards every line whose control text changes is voiced again (`reason: delivery`, counted toward `tts.max_attempts`).
- **`bootstrap` stays an option** (`babeldub set tts.ref_strategy=bootstrap`): the v0.2 chain with a target-language prompt bank from the character's first accepted dubs and no tags; use it when the pilot shows a target-language accent the text does not explain ([references/ref-strategy.md](references/ref-strategy.md), sections 2.3 and 6).

## Preconditions

1. `babeldub status`: `cast` and `translate` are finished (`done` or `attention`), with selected characters and translations. Otherwise `synth` returns exit code 2 with a hint.
2. The engine environments are installed. On exit code 4 apply the `babeldub-doctor` skill: `babeldub doctor` → `babeldub setup` (on an 8 GB+ GPU it installs `voxcpm`, `applio` and `qc`).
3. Lines without original text (`no_text`) are neither translated nor voiced: in v0.1 they keep the original voice.
4. GPU-first: every stage runs on the NVIDIA GPU when one is present (`compute.device=auto`); `--device cuda|cpu` overrides it for one run. `synth` reports the engine it chose and why in `notes`.

## Step 1. Engine

- `tts.engine=auto` (default): `voxcpm` on CUDA when its env exists and it speaks the language; otherwise `qwen3tts-fast` (CUDA) / `qwen3tts` (CPU) for zh, en, ja, ko, de, fr, ru, pt, es, it, and `chatterbox` for pl, tr and its other languages. `synth` writes the choice into `notes` (`tts.engine=auto -> voxcpm`, or why not).
- `babeldub set tts.engine=<engine>` or `babeldub synth --engine <engine>` forces one; a CUDA-only engine falls back along its chain on the CPU (`voxcpm` → `qwen3tts-fast` → `qwen3tts`).
- If no v0.1 engine speaks the language (for example `uk`), say so honestly: models with non-commercial licenses are not part of v0.1.

Engines, VRAM, licenses and measurements: [references/engines.md](references/engines.md).

## Step 2. RVC models of the main characters (once per project)

Train one model per **main** character (tier `main` in `cast/casting_sheet.md`) as soon as `cast` is done; it does not depend on the translation:

```text
babeldub vc train --chars <id1>,<id2> --dry-run   # dataset size per character
babeldub vc train --chars <id1>,<id2>            # ~15 min per character on an 8 GB GPU (20 epochs, 30 min of speech)
```

- The dataset is the character's clean original lines (2–20 s, no flags), up to `vc.minutes` (30). Fewer than ~5 minutes gives a weak model: tell the user.
- Training needs an NVIDIA GPU and the `applio` env (exit code 4 → `babeldub setup --engines applio`). It is a long job: tell the user before starting and run it in the background when the host allows.
- Secondary characters usually go without a model: `vc` passes their lines through unchanged.

## Step 3. Pilot

Run the pilot on the main characters before the batch:

```text
babeldub synth --chars <id1>,<id2> --dry-run   # plan: by_ref, delivery, best_of, best_of_mode, skipped
babeldub synth --chars <id1> --limit 8         # pilot: the first lines of each main character, one by one
babeldub post
babeldub vc
babeldub qa
babeldub query --where char=<id>,verdict=fail --fields id,qa.attempt,qa.cer,qa.sim,qa.sim_ref,qa.ratio,qa.reasons --limit 30
```

- With `tags`, every line of a character is voiced in one run (`by_ref: tags`), so limit the pilot with `--limit`. Check `delivery{by_source, tags}` in the output: a large share of `agent` means the translators overrode many hints (look at a few with `query --fields id,text.src,translation.delivery`); no tags at all usually means `analyze` did not run or the characters have too few lines.
- With an explicit `bootstrap`, the first `synth` dubs only the first lines of each character (`by_ref: bootstrap-src`) and reports the rest in `skipped.bootstrap_wait`; the next `synth` after QA builds the target-language prompt bank (`synth/prompts/<char_id>/tgt.json`) and dubs the rest (`bootstrap-tgt`, `bucket` in `synth/results.jsonl`). If fewer than 3 first lines were accepted, the rest goes on with the original reference only (`ref_reason` says so) — look at those lines first.
- Pilot criteria per character: `sim ≥ sim_threshold` (0.45 × `sim_ref` for a dub in another language, never below 0.25), identity `margin ≥ −0.05`, `cer ≤ qa.max_cer` (0.15), `ratio ≤ qa.max_duration_ratio` (1.35).
- You cannot hear audio. In the report (in the user's language) list 3–5 final files of the main characters (`vc/audio/<uid>.wav`, or `post/audio/<uid>.wav` without a model) to compare with `audio/orig/<uid>.wav`.
- A problem with a character's references as a whole (every line in the "wrong" voice, a gender switch) is a question for the caster role.

## Step 4. Batch synthesis

1. In chunks by character, main characters first: `babeldub synth --chars <id>`; split large volumes with `--limit 200`. Watch `remaining` (and `skipped.bootstrap_wait` with `bootstrap`).
2. Time: minutes of speech × `rtf` × `best_of`. `--best-of 1` (or `tts.best_of=1`) is a faster draft.
3. `synth`, `vc`, `qa` and `cast` with embeddings hold `state/locks/gpu.lock`: never run them in parallel.
4. After `tts.max_attempts` (3) attempts for the same text the line goes to `attention`; QA decides (usually `keep_original`).

| Symptom | What to do |
|---|---|
| exit code 3, `failed` > 0 | rerun `babeldub synth` (failed lines get a new attempt) |
| `aborted` or a crashing worker | read the `log`; on `CUDA out of memory` close programs that use video memory or use a smaller engine; see `troubleshooting.md` of `babeldub-localize` |
| `runaway[]` | the take looped and was discarded; the line was retried alone. A line that loops every time is `error: "runaway"`: rerun, then `keep_original` or `retranslate` |
| `skipped.bootstrap_wait` | only with `bootstrap`, expected: run `post`, `vc`, `qa`, then `synth` again |
| `control_ignored` in `notes` | the engine is not VoxCPM2 and reads no control text: the delivery tags are not used; expected on smaller GPUs |
| `skipped.follows` / `not_selected` | intended: alt takes reuse their leader; unselected characters keep the original voice |
| `skipped.keep_original` | intended: interjections and nonverbal lines keep the original (`tts.keep_original_classes`); a human decision other than `keep_original` dubs one anyway |
| `score.pending` with a note | the qc env is missing or busy: the provisional (length) picks stand; run `babeldub synth score` later, then `post` |
| `skipped.no_ref` | no reference and no usable original: a question for the caster |

## Step 5. Post-processing and timbre transfer

`babeldub post` fits every take to its original (exact length, never slower, pauses then tempo up to 1.15, loudness, true peak ≤ −1 dB, original format). Then `babeldub vc` converts the lines of characters with an RVC model and re-applies the exact length and loudness; characters without a model pass through (`passthrough`). Both are idempotent.

- `post` exit code 3, `over_length[]`: the dub does not fit even at the fastest allowed tempo; `not_exact[]`: such files run longer than the original (speech is never cut). QA decides (usually `shorten`).
- Length rules and the fitting cascade: [references/length-policy.md](references/length-policy.md). Order of operations, loudness, format, RVC: [references/post.md](references/post.md).

## Step 6. The loop after QA

```text
babeldub qa → (qa role) qa/decisions.agent.jsonl → babeldub decide apply
babeldub synth --attempt-next → babeldub post → babeldub vc → babeldub qa
```

- `regen`: a new attempt with new seeds and the same reference. With `tags`, a regen after a `cer` failure and any regen from the 3rd take of the same text on is voiced without the control text (`tags_source: dropped`); after `flat` or `mos` the tags stay (with `tts.tags_reference=bucket` also the next nearest arousal bucket). With `bootstrap`, the line gets the target-language prompt when it exists, and after `flat` or `mos` the next nearest bucket.
- `reref`: a new attempt with a different prompt (`tags` → `char`, or the line itself when the character has one reference; `line` → `char`; `char` → another reference or the line); the delivery tags stay.
- `shorten` and `retranslate` go through translation; then a regular `synth` picks up the new text. A new `delivery` from the translator on the same text gives a new take too (`reason: delivery`).
- If more than 20% of a character's lines in a chunk are defective, stop and find the cause (step 1–2, caster role) instead of retrying.

## Exit codes

| Code | synth | post | vc / vc train |
|---|---|---|---|
| 0 | everything planned was synthesized | all lines processed | converted / trained |
| 2 | no lines or translations, the engine does not speak the language | `synth` was not run | `post` not done; unknown character; training without a GPU |
| 3 | `failed`, `attention` or `aborted` | `over_length[]` or file errors | some lines failed; a character without clean lines |
| 4 | no engine venv or weights | no stretching tool (rubberband or ffmpeg) | no `applio` env |

## What not to do

- Do not synthesize or convert outside the CLI (own scripts, third-party TTS or RVC): that loses idempotency, attempt accounting and the model licenses in the build manifest.
- Do not enable models with non-commercial licenses without showing the license and getting an explicit "yes".
- Do not "improve" the original lines before cloning (denoising, EQ): the prompt must sound like the character in the game.
- Do not put stress marks into dub texts by hand (use the glossary `pronounce` or `tts.stress=on`); do not edit the canon (`synth/results.jsonl`, `post/post.jsonl`) or the game folder.

## References

- Before the pilot, for the delivery tags and on any "the voice does not match", "the emotion is gone", "over-acted", "accent": [references/ref-strategy.md](references/ref-strategy.md).
- Engines, languages, VRAM, licenses, measurements: [references/engines.md](references/engines.md).
- `over_length[]`, cutscenes (`fixed_length`), the `shorten` decision: [references/length-policy.md](references/length-policy.md).
- Loudness, pauses, format, RVC conversion, effects: [references/post.md](references/post.md).
