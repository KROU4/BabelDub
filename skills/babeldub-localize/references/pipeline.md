# BabelDub v0.1 pipeline: stages, commands, exit codes, artifacts

Read this file before stage 1 and every time a stage output is unclear. Commands and flags are as in the `babeldub` CLI 0.1.0; do not use any others. Almost every output has a `next` field — the CLI's hint for the next step. If `next` disagrees with this file, trust `next`: it sees the actual project state.

## 1. General rules for calls

- Format: `babeldub <command> [arguments] [--project <folder|slug>] [--pretty]`.
- Without `--project` the CLI uses the active project (the last `init`) or the `BABELDUB_PROJECT` variable.
- Stage flags where they make sense: `--limit N`, `--ids id1,id2`, `--force`, `--dry-run`.
- stdout is exactly one compact JSON object (usually under 2 KB). The `ok` field is `true` for exit codes 0 and 3. Long data lives in project files; the JSON points to them.
- Stages are idempotent. `--force` recomputes finished work; do not use it without a reason. For `install`, `uninstall` and `reapply`, `--force` disables the safeguards (running game, anti-cheat) — never use it there.
- Progress is kept in `state/progress.json`, the event log in `state/events.jsonl`. Read them only through `babeldub status`.
- GPU-first: every neural workload (`cast` voice embeddings, `synth`, `qa`) runs on the NVIDIA GPU when one is present (`compute.device` = `auto`, the default). `compute.device` accepts `auto`, `cuda`, `cpu`; `cpu` only on the user's explicit request.

## 2. Exit codes

| Code | Constant | Meaning | General reaction |
|---|---|---|---|
| 0 | `EXIT_OK` | stage complete, no attention needed | next stage |
| 1 | `EXIT_ERROR` | unexpected error: fields `error`, `where` | stage log in `logs/`, `troubleshooting.md`; retry once if the failure is transient; otherwise stop with a report |
| 2 | `EXIT_USAGE` | bad arguments, previous stage not done, invalid agent file | read `hint`; run the missing stage or fix the agent file; check the arguments against this file |
| 3 | `EXIT_ATTENTION` | stage complete, but items need a decision | hand the items to the stage's role; continue after it finishes |
| 4 | `EXIT_ENV` | environment missing | `babeldub-doctor` skill: `babeldub doctor` → `babeldub setup` |

A stage in the `attention` state counts as passed: the following stages run. Do not retell exit codes to the human: they get the outcome in the report.

## 3. Stages

Stage order in the CLI: `init → scan → extract → link → analyze → cast → translate → synth → post → vc → qa → build → verify → install`. A stage started before the previous one returns exit code 2 with a hint. `vc train` (RVC models of the main characters) is a one-time step any time after `cast`.

### 3.0. Environment (outside a project)

| Command | What it does | Key fields |
|---|---|---|
| `babeldub doctor` | OS, Python, tools, GPU (name, VRAM, driver), disk space, engine venvs, models; recommends a tier | `tier`, `gpu`, `missing[]`, `optional_missing[]`, `todo[]`, `warnings[]`, `install{}`, `next` |
| `babeldub setup [--engines voxcpm,applio,qc] [--tools] [--models auto] [--dry-run]` | engine venvs (on an 8 GB+ GPU: `voxcpm`, `applio`, `qc`; smaller GPUs: `qwen3tts-fast`; CPU: `qwen3tts`), packages, model weights, the Applio checkout, external tools (vgmstream) | `planned[]`, `download_gb`, `blocked[]`, `done[]`, `failed[]` |
| `babeldub models list` / `babeldub models fetch <name>` | model catalog with licenses and status | `models[]` |

`doctor`: code 4 — required tools missing (`missing[]`: uv, ffmpeg, ffprobe; install commands in `install{}`); code 3 — venvs or models missing (`todo[]`) → `setup`. Details are in the `babeldub-doctor` skill.

### 3.1. `init`: project

```text
babeldub init "<game folder>" --lang <code> [--cast all|main|list] [--chars A,B] [--mode auto] [--slug <slug>]
```

- Creates `project.json`, registers the game in the `games.json` registry (the guard hook protects the game folder based on it) and makes the project active.
- Output: `project`, `slug`, `game_dir`, `target_lang`, `cast`, `mode`, `engine`, `next`. Code 3 — there are warnings (for example, the synthesis engine does not support the language).
- `--mode`: `auto` (default) | `translate` | `adapt` | `official`.
- Code 2: folder not found, bad language code (ISO 639-1: `ru`, `pl`, `pt-BR` …) or `--cast list` without `--chars`.

### 3.2. `scan`: inventory

```text
babeldub scan
```

- Detects the engine, middleware, text systems and anti-cheat by signatures, queries all adapters, writes `inventory/recon.json`, `inventory/assets.jsonl`, `inventory/report.md`.
- Output: `engine{id, version, backend}`, `middleware[]`, `text_systems[]`, `voice_assets[]`, `text_assets[]`, `counts{}`, `level`, `warnings[]`, `report`, `next`.
- `level`: A — automatic; B — needs a user-installed tool or a mod loader; C — read-only and instructions.
- `inventory/report.md`, section "What to read next" — the references for what was found (the `babeldub-recon` skill works from them).
- Code 3: there are warnings (unrecognized engine, anti-cheat, adapter errors). The scout role reviews them with the `babeldub-recon` references. Anti-cheat → stop.

### 3.3. `extract`: lines and original audio

```text
babeldub extract [--kinds dialogue,cutscene] [--limit N]
```

- `list_lines()` → `inventory/lines.jsonl` (the canonical line list); `extract_audio()` → `audio/orig/<uid>.wav`; `read_texts()` → `inventory/texts.jsonl`.
- Output: `lines`, `by_kind{}`, `minutes`, `texts`, `locales[]`, `next`.
- `kind`: `dialogue | cutscene | bark | sfx | music | ambience | ui | unknown`. Only `dialogue`, `cutscene` and `bark` are voiced.
- Code 3: there are `unknown` lines or decoding errors in individual entries. The scout inspects them with `babeldub query --where kind=unknown --fields id,entry.key,audio.dur_s --limit 30`.
- Code 4: no decoder (usually `vgmstream-cli`) → `babeldub setup --tools`.

### 3.4. `link`: text ↔ audio

```text
babeldub link
```

- Links lines to texts: exact key, then normalized key, then file name. Fills `text` in `lines.jsonl` and `source_lang` in the project.
- Output: `linked`, `unlinked`, `with_official_target`, `by_match{}`, `src_lang`, `ambiguous`, `no_src_text`, `unlinked_examples[]`, `next`.
- Lines without text cannot be translated: they keep the original voice and go into the report. `analyze` transcribes their original (`analysis.asr_text`) for the report.
- Code 3: there are unlinked lines. The scout checks a sample and writes a hypothesis into the report; the canon is never edited by hand.

### 3.4a. `analyze`: what the originals really contain

```text
babeldub analyze [--ids a,b] [--limit N] [--force] [--device cuda|cpu] [--wait-lock S]
```

- The `qc` worker (on the GPU when present) measures every voiced original with audio (alt takes that follow a leader are skipped): Silero VAD speech segments (a 250 ms pause splits them), the ASR transcript with word times and confidences, pitch (F0 median and range in semitones), loudness and voiced share; an emotion label and embedding only when the user accepted the opt-in emotion2vec+ license. Rows go to `inventory/analysis.jsonl`, vectors to `inventory/analysis.npz`.
- Each line gets a class: `speech`, `short_word` (real words in under 1.2 s of speech: "Freeze!", "Yes, Chief!"), `interjection` (the subtitle is only "Hehe…", "Hmm", "Um…", "Ugh", "Oh!", "Ах"…), `nonverbal` (screams, laughs, grunts, a `[laughs]` subtitle, or no subtitle and nothing the ASR is sure of) or `mixed` (words plus a laugh or groan). `interjection` and `nonverbal` lines keep the original audio: `translate` leaves them out (`translate plan` → `keep_original`), `synth` skips them (`skipped.keep_original`) with an automatic `keep_original` decision. `short_word` lines are voiced from the line itself with 6 candidates and one regen at most; `mixed` lines are voiced and QA flags them for listening. The prosody of each line also sorts it into an arousal bucket (calm, animated, loud, soft) for the voice prompts.
- A line whose original says something else than its subtitle (CER of the ASR against `text.src` > 0.35) gets the flag `subtitle_mismatch` in `lines.jsonl`; no other line field changes. Tell the translator: such a subtitle may be an alt take, a shortened caption or the wrong line.
- Idempotent: an unchanged wav is not measured again (`--force` does); the class and the flag are recomputed on every run, so a corrected subtitle is picked up.
- Output: `analyzed`, `up_to_date`, `failed`, `lines`, `classes{}`, `subtitle_mismatch`, `mismatch_ids[]`, `flags_changed`, `skipped{}`, `emotion`, `device`, `elapsed_s`, `next`. Code 3: some lines failed or are left by `--limit` (run again); code 4: no `qc` env (`babeldub setup --engines qc`).
- `status` shows `counts.classes` and `counts.subtitle_mismatch`; `query` filters with `class=interjection` and reads `analysis.*` fields.

### 3.5. `cast`: characters and references

```text
babeldub cast [--refs 1..3] [--min-ref-s 3] [--max-ref-s 12]
babeldub cast apply-labels
babeldub cast select --chars A,B | --policy main|all
```

- Characters come from metadata (`speaker_hint`); lines without it are grouped by voice (embeddings through the `qc` worker, on the GPU when present). A voice cluster that sounds like a named character (centroid cosine ≥ `cast.merge_threshold` = 0.75) is merged into him: typically his cutscene lines without a speaker hint.
- A character's references are the cleanest and most typical original lines (`cast/refs.jsonl`, `cast/refs/<char_id>/*.wav`); the casting sheet is `cast/casting_sheet.md`.
- `cast` output: `characters`, `selected`, `main[]`, `uncertain[]`, `no_ref[]`, `clusters` (left after merging), `merged`, `merged_into{char_id: [cluster_id]}`, `refs`, `sheet`, `next`.
- Code 3: there are voice clusters or `uncertain[]`. The caster role labels them in `cast/labels.agent.jsonl` → `babeldub cast apply-labels` (output `updated`, `labeled`, `uncertain[]`; code 2 — invalid file, fix it).
- `no_ref[]` — the character has no separate reference: their lines use the original line as the prompt (`line`); short lines of such a character may stay without synthesis.
- After question 2 to the user: `babeldub cast select ...` (output `policy`, `selected[]`).
- Then, on an 8 GB+ GPU, train the RVC models of the `main` characters once: `babeldub vc train --chars <ids> [--epochs 20] [--minutes 30] [--dry-run]` (dataset: clean original lines of 2–20 s; ~15 min per character on an 8 GB GPU; models in `vc/<char_id>/`). Output `trained[]`, `skipped[]`, `no_data[]`, `failed[]`; code 4 — no `applio` env; code 2 — unknown character or no GPU.

### 3.6. `translate`: text for the dub

```text
babeldub translate plan
babeldub translate use-official
babeldub glossary apply
babeldub batch make [--size 60] [--ids id1,id2] [--from-decisions]
babeldub batch next
babeldub batch submit <bid>
babeldub glossary check
```

| Command | What it does | Output |
|---|---|---|
| `translate plan` | decides the mode; with `auto` — `adapt` when official target-language texts exist (the translator turns them into spoken dub texts; lines without one are translated in the same batches), otherwise `translate`; `official` (voice the official text as is) only when set explicitly | `mode`, `need_agent`, `translated`, `glossary`, `style_bible`, `next` |
| `translate use-official` | mode `official`: copies the official translation into `translate/translations.jsonl` (`by=official`) | `copied`, `missing`, `next` |
| `glossary apply` | validates `translate/glossary.agent.jsonl` and merges it into `translate/glossary.jsonl` | `added`, `updated`, `total` |
| `batch make` | cuts batches by scene for the lines that need an agent; `--ids` and `--from-decisions` make batches for retranslation (QA decisions `shorten`, `retranslate`) | `mode`, `batches[]`, `lines`, `open`, `dir`, `next` |
| `batch next` | the first open batch | `batch`, `state`, `in`, `out`, `remaining`, `next` |
| `batch submit <bid>` | validates `.out.jsonl` (every `id`, non-empty `dub_text`, placeholders as in `src`) and merges it into `translations.jsonl`; picks a variant that fits the budget | `merged`, `errors[]`, `over_budget[]`, `over_fixed[]`, `picked_variants`, `open_batches`, `next` |
| `glossary check` | checks glossary terms in the translations | `violations[]`, `next` |

Order:

1. `translate plan`. Mode `adapt` (the default with official texts) or `translate` → batches below; batch rows with `task: "adapt"` carry the official text in `tgt_official`, and an adapted dub keeps it as the subtitle. Mode `official` → `translate use-official`; if `need_agent` = 0 after that, go to the voice stage.
2. Lexicon role: `translate/glossary.agent.jsonl` and `translate/style_bible.md`, then `babeldub glossary apply`. This must happen **before** `batch make`: the batch header only receives terms from `translate/glossary.jsonl` and the style bible text.
3. `batch make`, then loop: `batch next` → translator → `batch submit <bid>`. `batch next` always returns the first open batch; for parallel work take different `bid` values from `batches[]` of the `batch make` output (or from the file names `translate/batches/<bid>.in.jsonl` that have no `.out.jsonl` yet).
4. `batch submit`: code 2 — errors in `errors[]` (full list in `errors_file`). The translator fixes their `.out.jsonl` and submits again, up to 3 iterations. Code 3 — `over_budget[]`: lines longer than 115 % of the budget; `over_fixed[]` (fixed length) needs shorter `variants`, `post` fits the rest.
5. End of the loop: `batch next` returns `batch: null`, `done: true`.
6. `glossary check`: code 3 — `violations[]` (file `translate/glossary_violations.jsonl`). Fixes: `babeldub batch make --ids <id,...>` → translator → `batch submit`.

### 3.7. `synth`: synthesis in the hero's voice

```text
babeldub synth [--chars A,B] [--limit N] [--attempt-next] [--engine <name>] [--best-of N]
```

- Engine: `tts.engine=auto` — `voxcpm` (VoxCPM2) on CUDA when its env exists, else `qwen3tts-fast`/`qwen3tts`/`chatterbox`; the choice is in `notes`.
- Voice prompt by `tts.ref_strategy`: with VoxCPM2 `auto` = `bootstrap` — the character's ~15–20 s long original timbre reference of the line's arousal bucket (calm, animated, loud, soft; calm when a bucket has too little material); the first 4 lines of each character with it alone (`bootstrap-src`), the rest wait (`skipped.bootstrap_wait`) until QA accepted 3 of them, then a target-language prompt bank (per bucket 1–2 accepted dubs, ranked by MOS and intonation) joins the reference with the prompt of the nearest bucket (`bootstrap-tgt`). Other engines: `line` — the original of this line with its source text; `char` — the character's best reference; `auto` — `line` if the original is ≥ `tts.line_ref_min_s` and has text, otherwise `char`.
- Best of N: `tts.best_of` (3) takes per line with different seeds. `tts.best_of_mode=score` (the default when the `qc` env exists): every take is kept, and after the TTS worker exits `synth` ranks them with the qc models (prosody similarity, MOS, length, voice similarity; CER and length as gates) and keeps the best; `babeldub synth score` does the same later for lines left unscored. Otherwise the take closest to the original's speech length is kept (`best_of` in the results row).
- Stress marks are stripped from the text for every model except an explicit stress model; the glossary `pronounce` goes into the dub text; `tts.stress=on` marks homographs (RUAccent, qc env) in the form the engine reads.
- Interjections and nonverbal lines are skipped (`skipped.keep_original`, an automatic `keep_original` decision).
- The model loads once per run, on the GPU when present; lines go in groups by character. Results: `synth/raw/<uid>/a<attempt>.wav` and `synth/results.jsonl`. Holds `state/locks/gpu.lock`.
- Output: `done`, `failed`, `rtf`, `best_of`, `best_of_mode`, `score{}`, `remaining`, `next`; skipped lines are counted by reason (character not selected, no translation, attempts exhausted, `bootstrap_wait`, `keep_original`). With waiting lines `next` says to run `post`, `vc`, `qa` and `synth` again; `status` and `qa` repeat it.
- `--attempt-next`: a new attempt for lines with the decision `regen` (new seed; after a `flat`/`mos` failure also the next nearest arousal bucket) or `reref` (prompt switch: original → character reference or the other way round).
- Lines with a changed translation (after `shorten`/`retranslate`) are picked up by a plain `synth`.
- Code 3: `failed` > 0 — rerun for them; if the error repeats, see `troubleshooting.md`.
- Code 4: engine venv or model weights missing → `babeldub-doctor`.
- Run long jobs in chunks (`--chars`, `--limit`) and check `remaining`.

### 3.8. `post`: fitting to the original

```text
babeldub post
```

- Never slower (`post.tempo_min` 1.0): a short dub keeps its tempo and the rest is silence; a dub that does not fit loses part of its pauses (each down to max(80 ms, half)), then speeds up to `post.tempo_max` (1.15, pitch kept); `fixed_length` chunks start at the original's speech segments; the speech starts where the original's does; exactly the original's sample count, sample rate and channels; loudness matched to the original speech (BS.1770 LUFS; RMS for clips shorter than 0.4 s); peak ceiling `post.true_peak_db` = −1 dB.
- For `fixed_length: true` (cutscenes, timelines, video) the speech must fit the original within `post.fixed_length_tolerance` (0.03).
- Output: `done`, `exact`, `not_exact[]`, `over_length[]`, `next`.
- Code 3: `over_length[]` — lines that did not fit even at the fastest allowed tempo (speech is never cut; such files run longer, `not_exact[]`). After `qa` they get `shorten`.

### 3.8a. `vc`: timbre transfer

```text
babeldub vc [--chars A,B] [--ids ...] [--force] [--dry-run]
```

- For every line of a character with an RVC model (`vc train`): Applio converts the post audio, the CLI re-applies the exact length and loudness, writes `vc/audio/<uid>.wav` and a new `post/post.jsonl` row (`stage: "vc"`), so QA checks and the build packs the converted take. Characters without a model pass through (`passthrough{}` and a note).
- Output: `converted`, `up_to_date`, `passthrough{}`, `failed`, `next`. Idempotent; holds `state/locks/gpu.lock`. Code 4 — no `applio` env (only when there is something to convert).

### 3.9. `qa`: quality and voice similarity

```text
babeldub qa
babeldub decide apply
babeldub report
```

- For the latest attempt of every line: speech recognition of the dub → CER against `dub_text`; similarity of the dub's voice to the hero's original line and to their reference (`sim`) against the character's baseline (`sim_ref`); naturalness (MOS) against the character's originals; intonation range and contour against the original (`prosody_sim`, monotony check); onset/offset timing of cutscene lines; duration ratio; peaks and silence. Runs on the GPU when present. Writes `qa/qa.jsonl`; auto-decisions go to `qa/decisions.jsonl`.
- Thresholds: `qa.max_cer` = 0.15; `sim` ≥ max(ratio × `sim_ref`, `qa.min_sim_abs` = 0.25) with ratio `qa.min_sim_ratio_xlang` = 0.45 for a dub in another language than the original audio, `qa.min_sim_ratio` = 0.75 for a same-language dub; identity `margin` ≥ `qa.min_identity_margin` = −0.05; `mos` ≥ the originals' median − `qa.mos_drop` (1.0); the dub's F0 range ≥ `qa.min_f0_range_ratio` (0.5) × the original's; cutscene chunks within ±80 ms; `qa.max_duration_ratio` = 1.35. Lines flagged `nonverbal` skip the voice, CER, MOS and prosody checks.
- Auto-decisions: passed → `accept` (a `mixed` line with attention); intelligibility, voice, naturalness, monotony, timing, peaks → `regen` while attempts remain (2 for a `short_word` line), then `keep_original`; too long → `shorten` (up to 2 times), then `keep_original`; missing file, no translation, worker failure → no decision (attention).
- `qa` output: `checked`, `pass`, `fail`, `regen`, `shorten`, `keep_original`, `accept`, `attention[]`, `pending`, `cer_median`, `sim_median`, `mos_median`, `prosody_median`, `next`.
- Code 3: `attention[]` is not empty. The qa role writes `qa/decisions.agent.jsonl` → `babeldub decide apply` (output `applied`, `skipped`, `by_decision`; code 2 — invalid file, fix it).
- Decisions: `accept | regen | reref | shorten | retranslate | keep_original | skip`.
- `report` writes `qa/report.md` with totals per character (output `file`).

### 3.10. Quality loop

```text
synth → post → vc → qa ─┬─ accept ──────────────────────────────────────────→ ready to build
                        ├─ bootstrap_wait ──→ synth → post → vc → qa
                        ├─ regen / reref ──→ synth --attempt-next → post → vc → qa
                        ├─ shorten / retranslate ──→ batch make --from-decisions → batch next → translator
                        │                             → batch submit → synth → post → vc → qa
                        └─ keep_original / skip ──────────────────────────────→ original plays
```

- The `next` hint of `qa` tells which branch is needed now. The loop is over when `next` suggests `babeldub report`.
- After `tts.max_attempts` (3) attempts on the same text the line gets `keep_original`.
- If more than 20 % of one character's lines in a chunk fail, the voice role stops that character's synthesis and investigates (references, strategy) instead of burning GPU time on retries.

### 3.11. `build`: native containers

```text
babeldub build [--codec auto|pcm16|fadpcm]
```

- For every changed container the adapter writes `build/out/<relpath>` and the manifest `build/build_manifest.json` (containers, sha1, codec, models and their licenses).
- Output: `containers[]`, `replaced`. Preview: `--dry-run`.
- Code 2 with a hint about level B/C: the adapter cannot build this format. Describe the manual path from the `babeldub-recon` reference in the report; write nothing to the game folder.
- The default codec is the project's `build.codec` (`auto`). If the game did not accept the audio, roll back and rebuild with `--codec pcm16`.

### 3.12. `verify`: checks before installing

```text
babeldub verify [--game-runtime]
```

- Checks every built container: structure, number and order of entries, decoding.
- Output: `ok`, `problems[]`.
- Code 3 or `ok` = false: do not install. Put the reasons in the report; rebuild if needed.
- `--game-runtime` loads the game's own sound library. Only on the user's explicit request.

### 3.13. `install`, `uninstall`, `reapply`

| Command | What it does | Output |
|---|---|---|
| `babeldub install` | backs up the originals to `build/backup`, atomic replacement, `build/install.json` | `installed[]`, `files`, `backup`, `rollback` |
| `babeldub uninstall` | restores the originals from the backup with a sha1 check | `restored[]` |
| `babeldub reapply` | reinstalls after a game update (checks the sha1 of the source files) | `reapplied[]`, `changed[]` |

- The CLI refuses to write to the game folder while the game is running or anti-cheat is detected, and if the game files are not the originals the build was made from. All three commands support `--dry-run` (plan without writing).
- "the game is running" → ask the user (in the report) to close the game and its launcher; do not bypass with flags.
- "not the originals this build was made from", or `reapply` with a non-empty `changed[]`: the game was updated. Rebuild from the new originals: `build` → `verify` → `install`.

## 4. Utility commands

| Command | Purpose |
|---|---|
| `babeldub status` | stages, counters (`counts`), `next_stage`, `next` — the first stage in the `pending`, `running` or `failed` state (stages in `attention` count as passed); code 3 if any stage is in `attention` or `failed` (per-stage details in `attention{stage: …}`). The first step after any break |
| `babeldub set <section.key>=<value> ...` | change `project.json` settings (output `changed{}`), e.g. `babeldub set qa.max_cer=0.2 compute.device=cuda` |
| `babeldub query --where <conditions> --fields <fields> --limit N` | select lines (output `rows[]`, `total`) |
| `babeldub sample --scene X --n 30 --out file.jsonl` | lines of a scene into a file for an agent (output `file`, `n`) |
| `babeldub games` | registry of games and their projects (output `games[]`, `active`) |

`query` is the main way to look at data. Always pass `--fields` and `--limit` (up to 50).

- `--where` conditions: `field=value`, `field!=value`, `field~substring`, comma-separated.
- Line fields: `id`, `kind`, `scene`, `fixed_length`, `flags`, `speaker_hint`, `entry.key`, `audio.dur_s`, `text.src`, `text.tgt_official`; casting — `char` (id or name), `char_name`; translation — `translation.dub_text`, `translation.fit`; QA — `qa.attempt`, `qa.cer`, `qa.sim`, `qa.sim_ref`, `qa.sim_threshold`, `qa.margin`, `qa.closest_char`, `qa.ratio`, `qa.reasons`, `qa.asr_text`; analysis — `analysis.speech_s`, `analysis.asr_text`, `analysis.asr_vs_sub_cer`, `analysis.prosody.f0_range_st`, `analysis.class_reason`; computed — `has_text`, `translated`, `verdict`, `decision`, `voiced`, `class`.
- Example: `babeldub query --where char=Zbyszek,verdict=fail --fields id,qa.reasons,qa.cer,qa.sim,qa.sim_ref --limit 30`.

## 5. Project artifacts

`BABELDUB_HOME` defaults to `~/.babeldub` (`C:\Users\<user>\.babeldub` on Windows); the `BABELDUB_HOME` environment variable overrides it.

```text
BABELDUB_HOME/projects/<slug>/
├─ project.json                         settings and the user's answers
├─ state/ progress.json events.jsonl locks/gpu.lock
├─ inventory/ recon.json assets.jsonl lines.jsonl texts.jsonl report.md analysis.jsonl analysis.npz
├─ audio/orig/<uid>.wav                 decoded originals
├─ cast/ characters.jsonl assign.jsonl refs.jsonl refs/<char_id>/<uid>.wav casting_sheet.md labels.agent.jsonl
├─ translate/ glossary.jsonl glossary.agent.jsonl style_bible.md batches/<bid>.in.jsonl batches/<bid>.out.jsonl translations.jsonl
├─ synth/ results.jsonl raw/<uid>/a<attempt>.wav prompts/<char_id>/ (bootstrap: src-*.wav, tgt.json, tgt-*.wav)
├─ post/ post.jsonl audio/<uid>.wav
├─ vc/ <char_id>/model.pth model.index model.json dataset/  audio/<uid>.wav
├─ qa/ qa.jsonl decisions.jsonl decisions.agent.jsonl report.md
├─ build/ out/<relpath> build_manifest.json backup/<relpath> install.json
└─ logs/
```

| Who writes | Files |
|---|---|
| CLI only (canon) | `project.json`, `state/*`, `inventory/*`, `cast/characters.jsonl`, `cast/assign.jsonl`, `cast/refs.jsonl`, `cast/casting_sheet.md`, `translate/glossary.jsonl`, `translate/batches/*.in.jsonl`, `translate/translations.jsonl`, `synth/*`, `post/*`, `vc/*`, `qa/qa.jsonl`, `qa/decisions.jsonl`, `qa/report.md`, `build/*` |
| roles (agents) | `cast/labels.agent.jsonl` (caster), `translate/glossary.agent.jsonl` and `translate/style_bible.md` (lexicon), `translate/batches/<bid>.out.jsonl` (translator), `qa/decisions.agent.jsonl` (qa) |

All JSONL files are UTF-8 without BOM, one object per line. A repeated `id` is a new revision; the last one wins.

## 6. Line lifecycle

```text
inventoried (extract) → linked (link; no text → original plays) → analyzed (analyze: line class, `subtitle_mismatch` flag)
→ cast (character not selected → original plays)
→ translated (translations.jsonl)
→ synth (attempt k; bootstrap lines may wait for their character's target-language prompt) → post → vc → qa
   ├─ accept → build → install
   ├─ regen / reref → synth (attempt k+1, at most tts.max_attempts per text)
   ├─ shorten / retranslate → new translation → synth
   └─ keep_original / skip → not built, original plays
```
