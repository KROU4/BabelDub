---
name: babeldub-voice
description: "Dub game lines in the original character's own voice: clone timbre, emotion and delivery from the original line with GPU-first local voice-cloning TTS (Qwen3-TTS by default, Chatterbox for pl/tr), choose the voice prompt (line, char, auto), run the pilot and batch synthesis, fit length and loudness to the original. Use for the synth and post stages and for voice questions. Example requests: 'dub the voices', 'voice it in the hero's voice', 'same voice in another language', 'clone the voices', 'why does the voice sound different', 'озвучь голосом героя', 'тот же голос на русском', 'сделай русскую озвучку', 'клонирование голоса', 'нейроозвучка', 'синтез реплик', 'doblar el juego', 'doblaje con la voz original'."
license: MIT
compatibility: "Local agent with shell access; babeldub CLI 0.1.0 (install via the babeldub-doctor skill); NVIDIA GPU with 8+ GB VRAM recommended, CPU works but is many times slower; engine venvs and weights are installed by babeldub setup."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: voice — dubbing in the original character's voice

This skill covers the `synth` and `post` stages. Its job is to make every translated line sound like **its own original character**: the same timbre, emotion, delivery and pace, only in the target language. This is dubbing, not "text-to-speech by a narrator". Facial animation and lip sync are not rebuilt.

The skill complements the `voice` role of the `babeldub-localize` skill (`references/roles/voice.md`). Stage order and exit codes are in that skill's `references/pipeline.md`.

Talk to the user in the user's language. Keep commands, field names and identifiers as they are.

## Core principle

1. **The voice prompt is the original line itself.** The cloning engine receives the original audio of this line and its original text (ICL cloning: `ref_audio` + `ref_text`). This carries over not only the timbre but the delivery of this exact phrase: a shout stays a shout, a whisper stays a whisper, fatigue stays fatigue. This is `tts.ref_strategy=line`.
2. **The character's best reference** is used when the original line is unfit as a prompt: the line is shorter than `tts.line_ref_min_s`, has no original text, or has the flags `has_bed` (music or noise under the voice) or `nonverbal`. This is `char`.
3. **The default is `auto`**: `line` for a usable line, otherwise `char`; if the character has no reference, the short original line is used anyway, so the line is not lost.
4. **The translation must fit the intonation of the original.** A text with the same emotional shape (exclamation, question, hesitation, cut-off) sounds natural; a text "calmer" than the original fights the prompt.
5. **QA compares the synthesized voice with the original character** (ECAPA similarity to the original line and to the character reference, relative to the character's own baseline). A line whose voice has drifted is regenerated or gets a different prompt.

Details on why a line prompt carries the emotion, when `char` is needed, and how to reduce the accent of the original language: [references/ref-strategy.md](references/ref-strategy.md). Read it before the pilot.

## When to use

- The `synth` and `post` stages, the pilot before batch synthesis, reruns after QA decisions.
- Questions like "why doesn't the voice sound like the character", "why is there an accent", "why is the line longer than the original", "which engine to use for language X".

## Preconditions

1. `babeldub status`: the `cast` and `translate` stages are finished (`done` or `attention`), and there are selected characters and translations. Otherwise `synth` returns exit code 2 with a hint.
2. The engine environment is installed. If `synth` returned exit code 4 (no venv or weights), apply the `babeldub-doctor` skill: `babeldub doctor` → `babeldub setup --engines <engine>`.
3. Lines without original text (`no_text`) are neither translated nor voiced: in v0.1 they keep the original voice.
4. GPU-first: synthesis runs on the NVIDIA GPU when one is present (project `compute.device=auto`); `babeldub synth --device cuda|cpu` overrides it for one run. If a GPU is present but the worker ended up on the CPU, `synth` says so in `notes`: reinstall the engine with CUDA wheels (`babeldub setup --engines <engine> --force`).

## Step 1. Engine and model

- The default is `tts.engine=qwen3tts` (Qwen3-TTS-12Hz-1.7B-Base, Apache-2.0). Languages: zh, en, ja, ko, de, fr, ru, pt, es, it. The Russian-stress fine-tune `qwen3-tts-12hz-1.7b-ru-stress-cf` keeps the hero's voice less faithfully: it is not installed by default and is enabled only explicitly (`references/engines.md`).
- For `pl`, `tr` and the other languages of Chatterbox Multilingual V3 (MIT, 23 languages, an inaudible PerTh watermark in every file): `babeldub set tts.engine=chatterbox`, or for one run `babeldub synth --engine chatterbox`.
- If the engine does not speak the target language, `synth` returns exit code 2 with a hint about which engine to use. If no v0.1 engine fits (for example, `uk`), say so honestly in the report: models with non-commercial licenses (OmniVoice and others) are not part of v0.1.
- The engine's model is chosen by hardware tier (`tts.tier=auto`). To pick one explicitly, run `babeldub set tts.model=<name from babeldub models list>`, for example `Qwen3-TTS-12Hz-0.6B-Base` when video memory is short.

The full picture of engines, VRAM tiers, licenses and what is not in v0.1: [references/engines.md](references/engines.md).

## Step 2. Prompt strategy

Keep `tts.ref_strategy=auto` unless there is a reason to change it. The duration threshold `tts.line_ref_min_s` is 1.6 s. Change settings only based on pilot metrics, and record the reason in the report:

| Situation | Action |
|---|---|
| similarity is fine, emotions are intact | change nothing |
| the character has many short lines, `line` gives a "wrong" voice | raise the threshold: `babeldub set tts.line_ref_min_s=2.5` |
| the character's originals are dirty (echo, radio, music without the `has_bed` flag) | use `char` for this character: `babeldub set tts.ref_strategy=char` → `babeldub synth --chars <id>` → set `auto` back |
| strong accent of the original language in the target language | see the accent section in `references/ref-strategy.md` |

To regenerate individual lines with a different prompt, use the QA decision `reref` (see step 6), not the global setting.

## Step 3. Pilot

Before batch synthesis, run a pilot on 10–20 lines of every main character:

```text
babeldub synth --chars <id1>,<id2> --limit 30
babeldub post
babeldub qa
babeldub query --where char=<id>,verdict=fail --fields id,qa.attempt,qa.cer,qa.sim,qa.sim_ref,qa.ratio,qa.reasons --limit 30
```

- `synth --dry-run` shows the plan in advance: how many lines, how many with a `line` prompt and how many with `char` (`by_ref`), what is skipped and why (`skipped`).
- Pilot criteria per character: voice similarity `sim ≥ qa.min_sim_ratio × sim_ref` (0.75), intelligibility `cer ≤ qa.max_cer` (0.15), duration `ratio ≤ qa.max_duration_ratio` (1.35; for `fixed_length`, 1 + `post.fixed_length_tolerance`).
- You cannot hear audio. The pilot does not block the work, but in the report (in the user's language) list 3–5 `post/audio/<uid>.wav` files of the main characters for the user to listen to, and explain how to compare them with `audio/orig/<uid>.wav`.
- Analyze a character's pilot failure using the step 2 table and `references/ref-strategy.md`. A problem with a character's references as a whole (every line in the "wrong" voice, a gender switch) is a question for the caster role, not for synthesis.

## Step 4. Batch synthesis

1. In chunks by character, main characters first: `babeldub synth --chars <id>`; split large volumes with `--limit 200`. Watch the `remaining` field.
2. Estimate run time from `rtf` and `audio_s` in the output: minutes of speech × `rtf`.
3. `synth` holds `state/locks/gpu.lock`. Do not run GPU stages (`synth`, `qa`, and `cast` with embeddings) in parallel.
4. `tts.best_of` is not used in v0.1: the QA loop regenerates weak lines (`regen` → `synth --attempt-next`).
5. After `tts.max_attempts` (3) attempts for the same text, the CLI stops synthesizing the line and reports it in `attention`; QA decides what happens next (usually `keep_original`).

Reacting to the `synth` output:

| Symptom | What to do |
|---|---|
| exit code 3, `failed` > 0, various errors | rerun `babeldub synth` (failed lines get a new attempt) |
| `aborted`: 5 identical errors in a row, or the worker crashes | read the log from the `log` field; on `CUDA out of memory`, use a smaller model (`tts.model`) or close programs that use video memory; see `troubleshooting.md` of the `babeldub-localize` skill |
| `runaway[]`: generation hit the length limit | usually the model got stuck in a loop; such lines almost always fail QA and go to `regen` |
| `skipped.no_ref` | the character has neither a reference nor a usable original: a question for the caster (`babeldub cast`) |
| `skipped.not_selected` | the character is not selected for dubbing: this is intended, and the original voice plays |

## Step 5. Post-processing

`babeldub post` fits every line to the original: it trims silence from the synthesized audio, compresses speech without changing pitch within `post.max_stretch` (up to 12%), restores the leading and trailing pauses of the original, matches loudness to the original line, caps true peak at `post.true_peak_db` (−1 dB), and converts sample rate and channels to the original. `post` cannot slow speech down: a short dubbed line is padded with pauses.

- Exit code 3 and `over_length[]`: lines that did not fit even after compression. QA decides their fate (usually `shorten`).
- Length rules by line type and the fitting cascade: [references/length-policy.md](references/length-policy.md).
- Order of operations, loudness, format, effects: [references/post.md](references/post.md).

## Step 6. The loop after QA

```text
babeldub qa → (qa role) qa/decisions.agent.jsonl → babeldub decide apply
babeldub synth --attempt-next → babeldub post → babeldub qa
```

- `regen`: a new attempt with a different seed and the same prompt.
- `reref`: a new attempt with a different prompt: `line` → `char`; `char` → another character reference or the line's original.
- `shorten` and `retranslate` go through translation (the `babeldub-translate` skill); then a regular `synth` picks up the new text as a new attempt.
- If more than 20% of a character's lines in a chunk are defective, stop synthesis for that character and find the cause (step 2, caster role) instead of running retries.

## synth and post exit codes

| Code | synth | post |
|---|---|---|
| 0 | everything planned was synthesized | all lines processed |
| 2 | no lines or translations, the engine does not speak the target language, invalid `--engine` | no synthesis (`synth` was not run) |
| 3 | there are `failed`, `attention` or `aborted` | there are `over_length[]` or errors in individual files |
| 4 | no engine venv or weights → `babeldub-doctor` | no stretching tool (rubberband or ffmpeg) → `babeldub-doctor` |

## What not to do

- Do not synthesize outside the CLI (your own scripts, third-party TTS): that loses idempotency, attempt accounting and the model licenses in the build manifest.
- Do not enable models with non-commercial licenses without showing the license and getting an explicit "yes" from the user.
- Do not "improve" the original lines before cloning (denoising, EQ): the prompt must sound like the character in the game.
- Do not edit the canon (`synth/results.jsonl`, `post/post.jsonl`) or the game folder.

## References

- Before the pilot and on any "the voice does not match", "the emotion is gone", "accent": [references/ref-strategy.md](references/ref-strategy.md).
- When choosing an engine for a language, on low VRAM, on license questions: [references/engines.md](references/engines.md).
- On `over_length[]`, cutscenes and videos (`fixed_length`), the `shorten` decision: [references/length-policy.md](references/length-policy.md).
- On questions about loudness, pauses, format, radio and echo effects: [references/post.md](references/post.md).
