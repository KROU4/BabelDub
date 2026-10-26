# The two questions, defaults and the final report

Read this file before the first question to the user, when choosing any default and before the final report.

## 1. The two-question rule

The user is asked exactly two questions: **language** and **characters**. Everything else is decided by the defaults in this file and recorded in the report. That includes the delivery of each line: the delivery tags are measured on the originals and corrected by the translator agents, never asked from the user.

- Ask in the user's language (the language of their messages). The wording below is a template: translate it, keep its meaning. Write the final report in the user's language too.
- Only the main session asks. Roles (subagents) never address the human: they return anything ambiguous in `human_pending`.
- If the user's request already contains the answer ("dub it into Russian, main characters only"), do not ask.
- If the host cannot ask questions (background run, CI, no interactivity), take the defaults.
- "I don't know" or "whatever is best" means the default.
- Ask questions with options through the host's choice tool if it has one; otherwise as a plain message listing the options.

The "listen to the pilot" and "install into the game?" gates do not block work by default: the pilot is checked by metrics, installation goes with a backup, rollback is one command.

## 2. Question 1: target language

**When:** at the very start, before `babeldub init`.

**Wording (template):**

> Which language should the game be dubbed into? I will keep the characters' voices: timbre, emotion and delivery stay as in the original, only the language changes.

**Options:** the user's language (default); another — the user types a name or a code.

**How to apply:** `babeldub init "<game folder>" --lang <code>`. The code is two letters: `ru`, `en`, `de`, `fr`, `es`, `it`, `pt`, `ja`, `ko`, `zh` …

**Default without an answer:** the language named in the user's request; otherwise the language the user writes in; if neither is known, `ru`.

If the default engine does not support the language (Qwen3-TTS has 10 languages: zh, en, ja, ko, de, fr, ru, pt, es, it), check `babeldub models list`. If there is no suitable model with a permissive license, say so honestly. Offer a model with a non-commercial (NC) license only together with its license text; enable it only after an explicit "yes".

## 3. Question 2: characters

**When:** after `babeldub cast` (and the caster's labeling, if there was any), before translation.

**What to show:** a short table from `cast/casting_sheet.md`: name, gender, number of lines, minutes of speech, tier (`main`/`secondary`/`minor`). At most 15 rows; fold the remaining characters into one row: "and N more secondary characters, M minutes in total".

**Wording (template):**

> Whom should I dub? The main characters carry ~70 % of all speech. Everyone else keeps their original voice.

**Options:**

| Answer | Command |
|---|---|
| main characters (default) | `babeldub cast select --policy main` |
| all | `babeldub cast select --policy all` |
| a list (names or numbers from the table) | `babeldub cast select --chars A,B,C` |

**Default without an answer:** main characters. This is the smallest set of characters that covers `cast.main_share` = 0.7 of all speech.

## 4. Defaults (no questions)

Values from `project.json`, created by `babeldub init`. Change them with `babeldub set <section.key>=<value>` only with a reason, and record the reason in the report.

| Setting | Default | Meaning |
|---|---|---|
| `compute.device` | `auto` | GPU-first: every neural workload runs on the NVIDIA GPU when present; `cuda` requires a GPU; `cpu` only on the user's explicit request |
| `translation.mode` | `auto` | `adapt` when official target-language texts exist (the translator turns them into spoken dub texts; the official text stays the subtitle), otherwise `translate`; `official` (voice the official text as is) only explicitly; the CLI decides in `translate plan` |
| `cast.policy` | `main` | main characters (`cast.main_share` = 0.7) |
| `cast.merge_threshold` | 0.75 | a voice cluster at least this similar to a named character is merged into him |
| `tts.engine` | `auto` | VoxCPM2 (`voxcpm`, Apache-2.0) on an NVIDIA GPU with its env; otherwise `qwen3tts-fast` (CUDA) / `qwen3tts` (CPU) / `chatterbox` by language |
| `tts.model` | `null` | the model is picked by the engine, tier and language |
| `tts.ref_strategy` | `auto` | VoxCPM2: `tags` (the long original timbre reference plus the line's delivery tags as a short control text); other engines: `line` (the original line with its text) when it lasts ≥ `tts.line_ref_min_s` and has text, otherwise `char`; `bootstrap` (long reference, then a target-language prompt from the character's accepted dubs) only explicitly |
| `tts.tags_reference` | `main` | `tags`: the calm/main long reference for every line; `bucket` takes the reference of the line's arousal bucket |
| `tts.delivery_tags` | `on` | `tags`: the translator's `delivery` > the measured tags > none; `measured` ignores the translator's tags; `off` sends no control text |
| `tts.line_ref_min_s` | 1.6 | minimum duration of the original for `line` |
| `tts.best_of` | 3 | takes per line; scored with the `qc` env (delivery, naturalness, length, voice), else the one closest to the original's speech length is kept |
| `tts.bootstrap_lines` | 4 | `bootstrap` only: first lines of a character dubbed before its target-language prompt exists |
| `tts.bootstrap_min_accepted` | 3 | `bootstrap` only: accepted first lines needed to build the prompt |
| `tts.max_attempts` | 3 | attempts before `keep_original` |
| `post.tempo_min` / `post.tempo_max` | 1.0 / 1.15 | `post` never slows a dub down (slow speech sounds unnatural) and speeds it up at most 15% after shortening its pauses; the rest is silence, the file keeps the original's exact length |
| `post.tolerance` | 0.03 | no tempo change when the dub's speech is within ±3 % of the original's |
| `post.fixed_length_tolerance` | 0.03 | tolerance for fixed-length lines (cutscenes) |
| `vc.epochs` / `vc.minutes` | 20 / 30 | RVC training per character (`babeldub vc train`) |
| `vc.index_rate` / `vc.protect` | 0.5 / 0.33 | RVC conversion settings (`babeldub vc`) |
| `post.true_peak_db` | −1.0 | peak ceiling |
| `qa.max_cer` | 0.15 | maximum character error rate of speech recognition on the dub |
| `qa.min_sim_ratio_xlang` | 0.45 | minimum similarity of the dub's voice to the character's original, relative to the character's baseline, when the dub is in another language than the original audio (the normal case) |
| `qa.min_sim_ratio` | 0.75 | the same for a same-language dub |
| `qa.min_sim_abs` | 0.25 | absolute floor of the similarity threshold |
| `qa.min_identity_margin` | −0.05 | how much closer the dub may sit to another character's voice than to its own |
| `qa.max_duration_ratio` | 1.35 | maximum ratio of the dub's duration to the original's |
| `build.codec` | `auto` | the adapter picks the build codec |
| `install.backup` | `true` | install only with a backup |

Decisions the orchestrator takes on its own:

- **Installation:** after a successful `verify`, install into the game with a backup. Do not install if the game is running or `verify` found problems.
- **Subtitles:** not changed in v0.1. The speech is translated and voiced; in `translate` mode the player hears the new language while the subtitles stay as they were (writing text tables comes in later versions).
- **Lines without text, interjections without a transcript, music and SFX** are not voiced: the original stays.
- **Online games and anti-cheat:** do not modify; tell the user.
- **Non-commercial models:** do not enable.
- **Downloading models and environments** with permissive licenses happens without asking, but with the plan printed (`babeldub setup --dry-run`): size and licenses are visible in the report.
- **Compute device:** keep `compute.device=auto` (GPU when present). If a stage warns that a worker runs on CPU although a GPU is present, fix it (see `troubleshooting.md`) instead of accepting hours of CPU synthesis.
- **`human_pending`:** does not block the build. Anything unresolved keeps the original voice and is listed in the report (at most 30 lines, the rest as a file).

## 5. Final report

Write it in the user's language, in plain words, briefly, without retelling JSON. Template (shown in English; translate the headings and text into the user's language):

```markdown
## BabelDub: <game> → <language>

**Result:** N of M selected lines dubbed (X min of speech). Installed into the game with a backup.

### What was done
- Engine and audio: <engine>, <middleware>; support level <A/B/C>.
- Text: mode <official/adapt/translate>; source — <official translation / translation from <language>>.
- Characters: <list of dubbed characters with minutes>; everyone else keeps the original voice.
- Voice: <model>, dubbed in the original hero's voice (VoxCPM2: the hero's long original reference plus delivery tags — N lines measured, N set by the translators, N neutral, from `counts.delivery` of `babeldub status`; other engines: the original line, for short lines the character reference); device <GPU name / CPU>.

### Quality
- Passed QA on the first attempt: N; after retries: N; kept original: N (reasons: …).
- Characters with problems: <name — what is wrong (voice similarity, intelligibility, length)>.
- Full table: `qa/report.md` in the project folder <path>.

### Default decisions
- <every deviation from the defaults and its reason>

### What to check in the game
- Voices match the characters, no gender flips.
- Loudness is even relative to music and effects.
- Long lines are not cut off; cutscenes do not overlap.
- Subtitles stay in sync with the speech.

### Rollback and updates
- Restore the original: `babeldub uninstall`.
- After a game update or "Verify integrity of game files" in Steam: `babeldub reapply`.

### Limitations and rights
- <not checked in the running game / codec / accent from cross-lingual cloning / subtitles not changed>.
- Models: <names and licenses from build/build_manifest.json>.
- This is a personal fan localization. Do not distribute the built game files: they contain other people's assets.

### Needs a human decision (not blocking)
- <up to 30 lines: character, key, reason>; full list — <file>.
```
