---
name: babeldub-localize
description: "Localize and dub any video game end to end in the voices of its original characters: find the voice-over and subtitles, translate, clone each hero's timbre, emotion and delivery with local TTS on the GPU, check every line (speech recognition, voice similarity, duration), repack into native engine formats and install with a backup and one-command rollback. Use when the user points at a game folder and asks to localize, translate, dub or revoice the game, e.g. 'localize this game', 'dub the voices', 'translate the game into Polish', 'revoice the characters', 'переведи игру', 'сделай русскую озвучку', 'переозвучь', 'нейроозвучка', 'doblar el juego', 'traduire le jeu', 'lokalisiere das Spiel'."
license: MIT
compatibility: "Local agent with shell access; Windows 10/11 or Linux; Python 3.10+ with uv; ffmpeg. GPU-first: an NVIDIA GPU with 8+ GB VRAM runs all neural work (voice cloning, speech recognition, voice similarity); CPU works but is slow. Web chats without file access can only translate exported batches."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: localize and dub a game in the voices of its original characters

This skill takes a game from its folder to an installed dub in the target language. You are the orchestrator: you run the stages of the `babeldub` CLI, delegate judgment calls to roles, react to exit codes and report to the user at the end. The skill works for any engine; the examples use Low-Budget Repairs (Unity + FMOD) only as an illustration.

## Up front: how you work with the user

1. **Ask the user exactly two questions**: the target language, and which characters to revoice (all / main / a list). Everything else is automatic: decide using the defaults and record each decision in the final report.
2. **Talk to the user in the user's language** (the language of their messages): questions, short progress notes and the final report. Keep commands, paths, identifiers and agent files exactly as they are.
3. **GPU-first.** All neural compute (voice cloning, speech recognition, voice similarity, voice clustering) runs on the NVIDIA GPU when one is present: `compute.device=auto`, the default. CPU is used only when there is no usable GPU or the user explicitly asks for it (`babeldub set compute.device=cpu`). A CPU fallback while a GPU is present is never silent: the CLI warns, and you pass the warning on in the report.
4. **Original-hero voice.** Every dubbed line sounds like the original hero: same timbre, same emotion, same delivery, only in the target language. See the next section.

## Core principle: dubbing, not text-to-speech

Every translated line must sound **in the voice of the original hero**: the same timbre, emotion, pace and delivery, only in the target language. Facial animation and lip sync are not rebuilt.

How this is achieved:

1. **The timbre comes from the original actor.** The default chain (8 GB+ NVIDIA GPU) is VoxCPM2 with `bootstrap` prompts: every line gets the character's ~15–20 s long reference of clean original lines with the same energy (calm, animated, loud or soft, from `analyze`); the first 4 lines of each character are dubbed with it alone, and once QA accepted 3 of them, the best of those dubs become target-language prompts for the rest (native pronunciation, the actor's timbre). Interjections and nonverbal lines keep the original.
2. **Best of N and an exact fit.** Three takes per line, ranked by intonation, naturalness, length and voice (with the `qc` env; else the one closest to the original's speech length wins); `post` fits it without ever slowing it down (pauses first, then at most 1.15× faster) and outputs exactly the original's length and format.
3. **RVC per main character.** One RVC model per main character, trained once on its original lines (`babeldub vc train`), converts its dubs (`babeldub vc`) in any target language.
4. **Fallback engines** (smaller GPUs, CPU, languages without VoxCPM2): Qwen3-TTS or Chatterbox with the original line as the prompt (`tts.ref_strategy=line`, carrying its intonation), or the character's best reference for short, noisy or untranscribed lines (`char`); `auto` picks.
5. **The translation keeps the emotional shape of the original**: exclamations, stammers, laughter, cut-off phrases, register and level of profanity. With an official translation the translator adapts it for speech (`adapt`).
6. **QA compares the synthesized voice with the character's original** (speaker similarity). A drifting voice, a gender flip or a foreign timbre goes back for regeneration (`regen`) or a reference change (`reref`).

Do not change `tts.ref_strategy` without a reason. If you change it, record the reason in the final report.

## Rules that always apply

1. **The game folder is read-only** until `babeldub install`. Do not copy, rename or edit anything there by hand. Only the CLI installs and rolls back (`install`, `uninstall`, `reapply`), with a backup and sha1 checks.
2. **Never run the game's executables** and never load the game's DLLs. Run a check with the game's own runtime (`babeldub verify --game-runtime`) only when the user explicitly asks for it.
3. **Only the CLI writes the canon.** Agents write `*.agent.jsonl`, `*.out.jsonl` and `translate/style_bible.md`. `cast apply-labels`, `batch submit` and `decide apply` merge them into the canon.
4. **Never read project files whole.** Look at data through `babeldub status`, `babeldub query`, `babeldub sample`; read `*.md` reports by section. The only file you may read whole is a translation batch handed to a role.
5. **Every command prints one JSON object** and returns an exit code. Decide the next step from the code and the `next` and `hint` fields.
6. **Online games and anti-cheat** (EasyAntiCheat, BattlEye, VAC in multiplayer): do not modify by default. If `scan` reports such a warning, stop and tell the user.
7. **Rights and licenses.** BabelDub is meant for personal fan localization. Do not distribute the built game containers. By default use only models under Apache-2.0 and MIT licenses; non-commercial (NC) models only after showing the license and getting an explicit "yes" from the user.

## Questions to the user: exactly two

Ask only two questions and nothing else. The wording, options and all defaults are in [references/questions-defaults.md](references/questions-defaults.md); read it before the first question. Ask in the user's language.

1. **Target language** — at the start, if the user has not named it.
2. **Characters** (all / main / a list) — after the `cast` stage, when the character list is known, if the user has not answered in advance.

Decide everything else yourself using the defaults and record it in the final report. Do not turn ambiguity into questions: put it in `human_pending` and in the report. If the host cannot ask questions (background run, CI), take the defaults.

## Roles and host

Roles make the judgment calls. Each role file describes the input, output, rules and the JSON answer format.

| Role | When | File |
|---|---|---|
| scout | `scan`, `extract`, `link`, analysis of unknown containers | [references/roles/scout.md](references/roles/scout.md) |
| caster | characters, labels, reference checks | [references/roles/caster.md](references/roles/caster.md) |
| lexicon | glossary, pronunciation, formal/informal address, style bible | [references/roles/lexicon.md](references/roles/lexicon.md) |
| translator | translating or adapting one batch | [references/roles/translator.md](references/roles/translator.md) |
| voice | pilot, synthesis, post-processing | [references/roles/voice.md](references/roles/voice.md) |
| qa | decisions on defects by metrics | [references/roles/qa.md](references/roles/qa.md) |
| packer | build, verify, install, rollback | [references/roles/packer.md](references/roles/packer.md) |

If the host can run subagents, delegate the role to the `babeldub-<role>` subagent. If it cannot, perform the role yourself: read its file, follow it, then return to this skill. If the host has the stage skills installed (`babeldub-recon`, `babeldub-cast`, `babeldub-translate`, `babeldub-voice`, `babeldub-qa`, `babeldub-pack`), their rules extend the role file of the same stage. Before you start, read the file for your host:

- Claude Code — [references/hosts/claude-code.md](references/hosts/claude-code.md);
- Codex CLI and desktop — [references/hosts/codex.md](references/hosts/codex.md);
- Gemini CLI and Antigravity — [references/hosts/gemini-antigravity.md](references/hosts/gemini-antigravity.md);
- web chats (claude.ai, ChatGPT) and other agents — [references/hosts/generic.md](references/hosts/generic.md).

## Workflow

Details for every stage (commands, output fields, artifacts, exit criteria) are in [references/pipeline.md](references/pipeline.md). Read it before stage 1. Below are the order and the orchestrator's decisions.

### 0. Environment

1. Run `babeldub --version`. If the command is missing or the version differs from this skill's `metadata.babeldub-cli` (`0.1.0`), apply the `babeldub-doctor` skill: it installs the right CLI version.
2. Run `babeldub doctor`. On exit code 4 (required tools missing) or 3 (engines or models missing, `todo[]`), apply `babeldub-doctor`. Working without a GPU is possible, but synthesis then takes hours; say so in the report.

### 1. Project

1. If the user has not named the language, ask question 1.
2. Check whether a project for this game already exists: `babeldub games`. If it does, continue it (`babeldub status --project <slug>`) instead of creating a new one.
3. Create the project: `babeldub init "<game folder>" --lang <code>`. If the user has already said which characters, add `--cast all|main` or `--cast list --chars A,B`.

### 2. Recon: what is inside the game

Delegate the **scout** role (following the `babeldub-recon` skill): `babeldub scan` → `babeldub extract` → `babeldub link`.

- Support level from `scan` (`level`): **A** — fully automatic from here; **B** — needs a tool the user installs, or a mod loader; **C** — CLI v0.1 cannot build this format. For B and C read the engine reference in `babeldub-recon` and describe honestly to the user what is possible. Do not edit anything in the game folder by hand.
- If there are no voiced lines (`lines` = 0) or all containers are unknown, stop with the scout's report.

### 3. Characters

1. `babeldub cast`. If `uncertain[]` lists characters, delegate the **caster** role: it labels them through `cast/labels.agent.jsonl` and `babeldub cast apply-labels`.
2. Ask question 2 and show the character table from `cast/casting_sheet.md` (name, gender, lines, minutes of speech, tier). Default: main characters.
3. Apply the answer: `babeldub cast select --policy main`, `--policy all` or `--chars A,B`.
4. On an 8 GB+ GPU with the `applio` env, train the RVC models of the `main` characters once: `babeldub vc train --chars <main ids>` (~15 min per character; tell the user before starting). It works for any target language; secondary characters pass through `vc` unchanged.

Characters that are not selected keep their original voice.

### 4. Text

1. `babeldub translate plan` — the CLI picks the mode: `adapt` (the default when an official translation exists: the translator turns it into spoken dub text — numbers as words, Latin names transliterated, colloquial, within the length budget; the official text stays the subtitle), `translate` (translate from the source); `official` (voice the official text as is) only when set explicitly.
2. For `official`, run `babeldub translate use-official`. If nothing is left for the agent to translate (`need_agent` = 0), go to the voice stage.
3. Delegate the **lexicon** role: glossary of names and terms, pronunciation for TTS, formal/informal address rules and the style bible; the role finishes with `babeldub glossary apply`. Do this before cutting batches: batches only receive the glossary that has already been applied.
4. `babeldub batch make` cuts batches by scene. Then loop: `babeldub batch next` → **translator** role on that batch → `babeldub batch submit <bid>`. On hosts with subagents, keep up to 4 translators in parallel, each with its own batch from `batches[]` of the `batch make` output.
5. When `batch next` returns `done: true`, run `babeldub glossary check`. For violations: `babeldub batch make --ids <id,...>` and the translator again.

### 5. Voice

Delegate the **voice** role: a pilot on a few lines of the main characters, then synthesis in chunks by character. Loop until every selected line reaches a terminal state:

1. `babeldub synth` → `babeldub post` → `babeldub vc` → `babeldub qa`. The CLI sets decisions from the thresholds on its own: a line that passes gets `accept`; an intelligibility, voice or peak defect gets `regen`; a line that is too long gets `shorten`.
2. With `bootstrap`, the first round dubs only the first lines of each character; `synth`, `qa` and `status` then say to run `synth` again (`bootstrap_wait`): the rest is dubbed with the target-language prompt built from the accepted lines.
3. Exit code 3 from `qa` (`attention[]`): the **qa** role reviews these lines, writes decisions to `qa/decisions.agent.jsonl` and runs `babeldub decide apply`. For a drifting voice it prefers `reref` — switching the prompt to the character reference.
4. `regen` and `reref` → `babeldub synth --attempt-next` → `post` → `vc` → `qa`. `shorten` and `retranslate` → `babeldub batch make --from-decisions` → translator → `batch submit` → `synth` → `post` → `vc` → `qa`. The `next` field of the `qa` output tells which branch is needed now.
5. After `tts.max_attempts` (3) failed attempts the line keeps its original voice (`keep_original`) and goes into the report.

The loop is over when `qa` suggests `babeldub report` in `next`. Do not run GPU stages (`synth`, `vc`, `vc train`, `qa`) in parallel: the CLI holds `state/locks/gpu.lock`. At the end: `babeldub report` → `qa/report.md`.

### 6. Build and install

Delegate the **packer** role: `babeldub build` → `babeldub verify` → `babeldub install`. If `verify` passes without problems, install by default; the CLI backs up the originals first. The CLI refuses to write to the game folder while the game is running or anti-cheat is detected; do not bypass these checks. If `verify` found problems or the installation is blocked, describe the reason and the next step in the report.

### 7. Final report

Write the report from the template in [references/questions-defaults.md](references/questions-defaults.md), in the user's language: what was dubbed, what kept the original voice and why, which decisions were taken by default, risks, how to roll back (`babeldub uninstall`) and how to restore the dub after a game update or a Steam file integrity check (`babeldub reapply`).

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | stage complete | next stage (check `next`) |
| 1 | unexpected error (`error`, `where`) | look at the project's `logs/` and [references/troubleshooting.md](references/troubleshooting.md); retry once if the failure looks transient (worker crash, network drop) |
| 2 | bad arguments, previous stage not done, invalid agent file | read `hint`; run the missing stage or fix the agent file (up to 3 iterations) |
| 3 | stage complete, items need a decision | hand them to the role of this stage (caster, translator, qa, packer); the human only gets the outcome in the report |
| 4 | environment missing | `babeldub-doctor` skill: `babeldub doctor` → `babeldub setup` |

The detailed reaction per stage is in [references/pipeline.md](references/pipeline.md).

## Resuming

The project lives in `BABELDUB_HOME/projects/<slug>`. `BABELDUB_HOME` defaults to `~/.babeldub` (`C:\Users\<user>\.babeldub` on Windows); the `BABELDUB_HOME` environment variable overrides it. Stages are idempotent: a rerun does not repeat finished work without `--force`. After a break, context compaction or a new session:

1. `babeldub status` — stages (`stages`), counters (`counts`) and `next`.
2. Continue from the first stage in the `pending`, `running` or `failed` state. A stage in `attention` has passed but still has items to decide (exit code 3 from `status`): review them if they matter, then move on through the `stages` table.

## Rollback

- `babeldub uninstall` restores the originals from the backup and checks sha1.
- A game update or Steam's "Verify integrity of game files" brings the original files back; to reinstall the dub, run `babeldub reapply`.

## When to read what

- Before the first question to the user, when choosing defaults and before the final report — [references/questions-defaults.md](references/questions-defaults.md).
- Before stage 1 and whenever a stage output is unclear — [references/pipeline.md](references/pipeline.md).
- On exit codes 1 and 4, GPU out of memory, odd QA metrics, installation problems — [references/troubleshooting.md](references/troubleshooting.md).
- Before delegating a role or performing it in the main session — the role file in `references/roles/`.
- Once at the start of work — the file for your host in `references/hosts/`.
- When `scan` has shown the engine or format — the `babeldub-recon` skill and its references.
