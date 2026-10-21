# BabelDub developer contract (v0.1)

This document is the single source of truth for the code in `runtime/` and for the skills that call that code. If the code and this document disagree, fix whichever is wrong and update the document. Checked against the v0.1 implementation (every command, flag and field below exists in `runtime/src/babeldub`); the end-to-end run of all stages is `runtime/tests/e2e/test_pipeline_synthetic.py`.

## 0. Language policy

Binding, from the project owner. The shipped product is English-first, the world standard for software:

- Everything meant for an AI model (SKILL.md files and their references, agent roles, commands, `AGENTS.md`, `GEMINI.md`, hook messages, CLI hints and generated reports) and all repository documentation (`docs/`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, GitHub templates) is written in English.
- `README.ru.md` is the only Russian document; the `assets/*-ru.*` illustrations that go with it are the only other Russian content.
- Skill descriptions may list example user phrases in several languages, including Russian, so the skills trigger for non-English users.
- The agent always talks to the user in the user's language.

## 1. Principles

1. **Skills define the procedure, the CLI does the bulk work, agents make judgements.** Everything deterministic, bulk or dangerous (parsing binary formats, decoding, synthesis, metrics, build, install) is a `babeldub` command. Translation, character labelling and triage of bad takes belong to agents.
2. **One writer of the canon.** Only the CLI writes the canonical manifests (`inventory/*.jsonl`, `translate/translations.jsonl`, `translate/glossary.jsonl`, `cast/characters.jsonl`, `qa/decisions.jsonl` …). Agents write `*.agent.jsonl` / `*.out.jsonl`; the CLI validates them and merges them (`submit`, `apply`) on an all-or-nothing basis.
3. **The game folder is read-only** until `babeldub install`. Installation makes a backup and checks sha1; rollback is `babeldub uninstall`. The CLI never runs the game's executables (the FMOD runtime check in `verify --game-runtime` loads only the game's audio library, in a child process).
4. **Heavy things stay outside the repository and outside the CLI.** The CLI depends only on `numpy`, `soundfile`, `PyYAML`, `UnityPy`. Everything that needs `torch` runs in separate venvs (`BABELDUB_HOME/envs/<engine>`) as a subprocess worker with a JSONL protocol (§6).
5. **One JSON on output.** Every command prints exactly one compact JSON object to stdout (target: under 2 KB) and returns an exit code (§2). Details go to project files; the summary points to them. Even if a command's module fails to import, `cli.main` prints a JSON error, not a traceback.
6. **GPU-first (a customer requirement).** When the machine has an NVIDIA GPU, ALL neural compute (TTS, ASR, speaker embeddings, clustering on embeddings, separation) runs on the GPU. Project setting `compute.device`: `auto` (default: GPU if present) | `cuda` | `cpu`. The worker's device is resolved by `core.compute.resolve_device(project.compute.device)` and passed in `{"op":"load","device":...}` (`synth` takes `--device`, else `tts.device`, else `compute.device`; `cast`/`qa` take `--device`, else `compute.device`). In its reply to `load` the worker reports the actual device (`"device": "cuda:0"`); if a GPU exists but the worker ended up on the CPU, the command adds the warning `core.compute.device_warning(...)` to its summary. `setup` installs CUDA torch wheels whenever `core.compute.detect_gpu()` sees a GPU, and CPU wheels only when there is no GPU. Heavy numpy loops (for example, FADPCM encoding) are vectorised; light DSP (resampling, loudness) stays on the CPU.
7. **Idempotence.** Re-running a stage does not break the result and does not repeat finished work without `--force`.
8. **Dubbing in the original character's voice.** The default voice prompt is the original line itself with its source text (`tts.ref_strategy`, §4.1); QA compares the voice of the synthesis with the character's original.

## 2. Exit codes

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | stage finished, no attention needed |
| 1 | `EXIT_ERROR` | unexpected error (`error`, `where` in the JSON) |
| 2 | `EXIT_USAGE` | bad arguments, previous stage not passed, invalid agent file |
| 3 | `EXIT_ATTENTION` | stage finished, but some items need a decision (by the agent or a human) |
| 4 | `EXIT_ENV` | environment missing: `babeldub doctor` → `babeldub setup` |

Errors are raised as `BabelDubError(message, code=..., hint=..., **extra)`; `cli.main` turns them into JSON. The `ok` field in the JSON is `true` for codes 0 and 3.

Stage statuses in `state/progress.json` (`project.mark_stage`): `running | done | attention | failed`. A stage in `attention` counts as passed (the pipeline moves on); its items wait for a decision. `uninstall` records the `install` stage with status `attention` and `state: "uninstalled"`.

## 3. Data layout

```text
BABELDUB_HOME (default ~/.babeldub, i.e. C:\Users\<user>\.babeldub on Windows; env BABELDUB_HOME)
├── active.json        active project {"project": "<dir>"}
├── games.json         registry of game folders (for the guard hook)
├── envs/<engine>/     engine venvs: qwen3tts, chatterbox, qc (marker babeldub-env.json)
├── models/<name>/     weights (hf download --local-dir; marker .babeldub-model.json)
├── hf-cache/          HF_HOME for workers (speechbrain/<model> is the working copy of ECAPA)
├── tools/<tool>/      external utilities (vgmstream/INSTALLED.json etc.)
├── cache/downloads/   setup downloads
├── logs/              doctor.json, setup-plan.json, setup-<name>.log
└── projects/<slug>/   project working folders
```

The default is not `%LOCALAPPDATA%`: packaged (MSIX) hosts such as the Claude desktop app virtualise it, so data written there from an agent session is invisible to other terminals. `core.paths.legacy_homes()` lists the pre-release defaults (`%LOCALAPPDATA%\BabelDub`, `$XDG_DATA_HOME/babeldub` or `~/.local/share/babeldub`); `doctor` warns when it finds data there.

Project folder (`core/layout.py` holds the path constants):

```text
projects/<slug>/
├─ project.json                         settings and the user's answers
├─ state/ progress.json events.jsonl locks/gpu.lock
├─ inventory/ recon.json assets.jsonl lines.jsonl texts.jsonl report.md
├─ audio/orig/<uid>.wav                 decoded originals
├─ cast/ characters.jsonl assign.jsonl refs.jsonl refs/<char_id>/<uid>.wav casting_sheet.md labels.agent.jsonl
│        embeddings.npz                 cache of ECAPA embeddings of the originals (keyed by wav size+mtime)
├─ translate/ glossary.jsonl glossary.agent.jsonl style_bible.md translations.jsonl
│             batches/index.json batches/<bid>.in.jsonl batches/<bid>.out.jsonl batches/<bid>.errors.json
│             missing_official.txt glossary_violations.jsonl
├─ synth/ results.jsonl raw/<uid>/a<attempt>.wav refcache/<sha>.wav (trimmed long references)
├─ post/ post.jsonl audio/<uid>.wav
├─ qa/ qa.jsonl decisions.jsonl decisions.agent.jsonl baseline.json report.md
├─ build/ out/<relpath> build_manifest.json backup/<relpath> install.json (staging/ exists only during build)
└─ logs/ synth-<stamp>.log qc_worker.log samples/<label>.jsonl
```

## 4. Manifests

All JSONL files are UTF-8 without BOM, one object per line; the first line of canonical files is a header `{"_schema": "...", "_v": 1, "_created": "..."}` (readers skip it, see `core/jsonl.py`). A repeated `id` is a new revision; the last one wins. Fields beyond those listed are allowed; readers ignore them.

### 4.1 `project.json`
```json
{
  "_schema": "babeldub.project/1", "cli_version": "0.1.0", "created": "...",
  "slug": "low-budget-repairs", "game_name": "Low-Budget Repairs", "game_dir": "G:/Low-Budget Repairs",
  "target_lang": "ru", "source_lang": null,
  "compute": {"device": "auto"},
  "cast": {"policy": "main", "list": [], "main_share": 0.7},
  "translation": {"mode": "auto"},
  "tts": {"engine": "qwen3tts", "model": null, "tier": "auto", "ref_strategy": "auto", "line_ref_min_s": 1.6, "best_of": 1, "max_attempts": 3},
  "post": {"max_stretch": 0.12, "fixed_length_tolerance": 0.03, "true_peak_db": -1.0},
  "qa": {"max_cer": 0.15, "min_sim_ratio": 0.75, "max_duration_ratio": 1.35},
  "build": {"codec": "auto"},
  "install": {"backup": true}
}
```
- `cast.policy`: `all` | `main` (the smallest set of characters that covers `main_share` of all speech) | `list` (names/IDs in `cast.list`).
- `tts.ref_strategy` is **BabelDub's core principle: dubbing in the original character's voice.** `line`: the voice prompt is the original line itself with its source text, so the model carries over the timbre *and* the emotion, pace and delivery of that exact phrase (a scream stays a scream). `char`: the character's best clean reference (for short lines, interjections, lines without a transcript or with music). `auto`: `line` if the original has source text, `line_ref_min_s ≤ dur ≤ 15 s` and no `has_bed`/`nonverbal` flags, otherwise `char`; if the character has no reference, the last resort is the short original. A reference longer than 15 s is cut at its quietest point and fed in `x_vector_only` mode (timbre only). QA compares the voice of the synthesis with the character's original (speaker similarity) and sends drifted voices back for regeneration.
- `translation.mode`: `auto` (official if an official translation exists for ≥ 95 % of the linked voiced lines; otherwise translate; `adapt` only when set explicitly) | `translate` | `adapt` (the official text exists; the agent adapts it to the duration) | `official` (voice the official text as is). `translate plan` writes the resolved mode to `translation.resolved_mode`.
- Optional keys the CLI reads: `target_locale` (`pt-BR` etc., written by `init`), `tts.device` (`cuda|cpu`), `tts.dtype` (`bf16`), `tts.batch_size` (lines per generation call, default 4), `tts.params` (engine generation kwargs), `tts.options` (model load options, for example `attn_implementation`), `tts.yo` (`keep|strip|restore` for `prepare_for_tts`), `post.tolerance` (0.05: no stretching if the dub is at most 5 % longer), `qa.silence_db` (−50), `qa.asr_model`, `qa.sv_model` (qc model folders).
- `tts.best_of` is not used in v0.1 (`synth` says so in `notes`): weak lines are re-voiced by the QA loop (`regen`/`reref` → `synth --attempt-next`).
- `set` validates the enumerations `compute.device`, `cast.policy`, `translation.mode`, `tts.ref_strategy`, `tts.tier`, `tts.engine` (`qwen3tts|chatterbox|dummy`); `_schema`, `cli_version`, `created`, `slug`, `game_dir`, `target_lang` are protected (another language means a new project).

### 4.2 `inventory/assets.jsonl`: containers (`adapters.base.Asset`)
`asset_id`, `adapter`, `relpath`, `role` (`voice|text|audio|other`), `size`, `sha1`, `locale`, `level` (`A|B|C`), `info{}`.
- `scan` adds `info.mtime_ns` (sha1 is reused if size and mtime did not change) and `info.sha1_of: "listing"` for a folder asset (sha1 over the file list).
- FMOD: `info.kind`, `fsbs[{sub, codec, n, dur_s}]`, `audio_table`, `programmer_keys`, `localized_group`, `strings_banks` (relpaths of `*.strings.bank`; the adapter takes event paths from them, and the field survives re-creating an `Asset` from a row), `encrypted`, `fmt_version`, `bank_guid`.
- Unity Localization: `info.kind` (`string-tables|shared-table-data|mixed`), `tables`, `typetree`, `unity_version`, `addressables_crc`, `warning`.

### 4.3 `inventory/lines.jsonl`: the CANON of lines
```json
{
  "id": "fmod:LowBudgetRepairs_Data/StreamingAssets/Dialogue_en-US.bank#fsb1:0042",
  "uid": "1f0c3a9e7b21",
  "adapter": "fmod",
  "asset_id": "fmod:LowBudgetRepairs_Data/StreamingAssets/Dialogue_en-US.bank",
  "entry": {"key": "dlg_a1_m01_d03_Zbyszek_01", "sub": "fsb1", "index": 42, "event_path": "event:/Dialogue/Act1/Zbyszek/dlg_..."},
  "kind": "dialogue",
  "locale": "en-US",
  "audio": {"sr": 48000, "ch": 1, "dur_s": 2.35, "codec": "vorbis", "wav": "audio/orig/1f0c3a9e7b21.wav"},
  "speaker_hint": "Zbyszek",
  "scene": "a1_m01",
  "order": 42,
  "fixed_length": false,
  "text": {"key": "dlg_...", "src": "Okay, let's see.", "src_lang": "en", "tgt_official": "Так, посмотрим.", "source": "unity-localization", "match": "exact", "table": "Dialogues_Act_1"},
  "flags": []
}
```
- `id` is built by `core.ids.line_id(adapter, relpath, sub, index)`; `uid = core.ids.uid(id)`.
- `kind`: `dialogue | cutscene | bark | sfx | music | ambience | ui | unknown`. Only `dialogue`, `cutscene` and `bark` are voiced.
- `fixed_length: true`: the line must fit the original duration (cutscenes, timelines, video).
- `audio.wav` appears after `extract` (a path relative to the project); `text` appears after `link`.
- FMOD `entry` also has: `subsong` (vgmstream number), `table_key` (audio table key for `getSoundInfo`), `audio_table`, `event_src` (`strings|programmer`), `fsb_codec`; and `audio.samples`. `loose` has `entry.file` (the file's path in the game).
- `text`: `src` is the text actually spoken, without subtitle markup (the speaker label `{ColorStart}{CharacterName}:{ColorEnd}`, `<i>` and `<color>` tags, Ren'Py tags); the raw string is in `src_raw`/`tgt_raw` when it differs. `match`: `exact | normalized | filename | null`. A repeated `link` does not overwrite the sources `asr|agent|human|manual`.
- `flags`: `duplicate`, `alt_take` (set by `link` for several records with one key: the main record is the one with `table_key`, then the one with the exact key, then `_best`, then the highest `_vN`), `nonverbal`, `has_bed` (speech mixed with music/noise), `no_text` (no text), `no_audio` (failed to decode). A repeated `extract` keeps `text` and the stage flags.

### 4.4 `inventory/texts.jsonl`
`{key, table, locale, lang, text, source}`: every string in every language from the text assets (`lang` is a two-letter code). Optional: `entry_id`, `key_missing` (key not found in the shared table), `audio`/`file`/`voice` (audio file name; `link` uses it in the file-name pass).

### 4.5 `cast/*`
- `characters.jsonl`: `{char_id, name, name_tgt, gender (m|f|u), age (child|adult|old|u), n_lines, speech_s, share, tier (main|secondary|minor), selected, source (metadata|folder|cluster|unknown), conf, labeled, notes}`. `char_id` is the Unicode slug of the name (`cast.characters.char_id_for`); clusters are `cluster_NN`; lines without a speaker when there is no qc worker go to `unknown`.
- `assign.jsonl`: `{id, char_id, conf}` (for a cluster, `conf` is the cosine to the centroid).
- `refs.jsonl`: `{ref_id, char_id, line_id, wav, text, lang, dur_s, score, chosen, centroid_cos?, active_rms_db?}`. The `unknown` group has no references: its lines are voiced with the `line` strategy.
- `labels.agent.jsonl` (written by the caster agent): `{char_id, name, name_tgt, gender, age, notes}`; extra fields or an unknown `char_id` reject the whole file (code 2).
- `embeddings.npz`: cache of embeddings by `line id`.

### 4.6 `translate/*`
- `batches/<bid>.in.jsonl` (CLI): first line `{"_batch": bid, "scene", "scenes", "target_lang", "source_lang", "mode", "n", "glossary": [...], "style": "...", "out": "<bid>.out.jsonl", "rules"}`, then `{id, key, speaker, src, tgt_official, dur_s, budget_syll, fixed_length, placeholders, context_prev, context_next, task?, current?}` (`task`: `adapt | retranslate | shorten`; `current` is the current `dub_text`).
- `batches/<bid>.out.jsonl` (agent): `{id, dub_text, sub_text?, variants?: [shorter...], notes?}`. The source's placeholders and tags must be present in `dub_text`, `sub_text` and every variant (`check_placeholders`; stage directions like `[snoring]` and line breaks are soft).
- `batches/index.json`: batch state (`open | error | merged`, ids, sha1 of the merged `.out`); `<bid>.errors.json`: errors of the last submit.
- `translations.jsonl` (CLI): `{id, dub_text, sub_text, lang, by (official|agent|human), rev, ts, est_syll, budget_syll, fit (ok|over|under|null), batch?, variants?, picked_variant?, agent_text?, notes?}`. `fit = null` when the duration is unknown; on `over` the CLI itself picks the first fitting variant from `variants` (the agent's original text goes to `agent_text`). `dub_text` keeps the string with tags as in `src`; `prepare_for_tts` cleans it before synthesis.
- `glossary.jsonl` (CLI, canonical): `{term, translation, pronounce?, note?}`; the agent writes `glossary.agent.jsonl` and the CLI merges it (`glossary apply`).

### 4.7 `synth/results.jsonl`
`{id, attempt, engine, model, wav, sr, dur_s, seed, ref_id, text, status (ok|error), error?, rtf?, ts}` plus `ref_mode (line|char)`, `ref_wav`, `ref_trimmed`, `lang`, `char_id`, `reason (new|retry|retranslated|regen|reref|force)`, `key` (hash of the inputs: engine, model, language, text, reference), `batch?` (batch size; a batch shares one seed), `hit_token_limit?`, `vram_peak_gb?`. Failed rows have `status=error`, `wav=null`.

### 4.8 `post/post.jsonl`
`{id, attempt, wav, dur_s, orig_dur_s, target_s, ratio, ratio_raw, stretch, stretch_method, lufs_in, lufs_out, level_ref, measure (lufs|rms), peak_db, lead_s, trail_s, sr, ch, fixed_length, over_length, src_wav, src_key, ts}`.
- `dur_s` is the duration of the whole file; `target_s` is the speech span of the original without edge silence; `ratio` = speech span of the dub / `target_s` after fitting, `ratio_raw` before it.
- Stretching happens if `ratio_raw > 1 + post.tolerance` (for `fixed_length`, `1 + fixed_length_tolerance`), and never more than `post.max_stretch`. `over_length` is set if after fitting `ratio > 1 + fixed_length_tolerance` (fixed) or `> qa.max_duration_ratio`.
- Loudness follows the original's LUFS (RMS if the speech is shorter than 0.4 s), true peak ≤ `post.true_peak_db`, sample rate and channels as in the original.

### 4.9 `qa/*`
- `qa.jsonl`: `{id, attempt, char_id, wav, asr_text, cer, sim, sim_line, sim_char, sim_ref, ratio, dur_s, peak_db, verdict (pass|fail), reasons[], ts}`; `reasons ∈ missing_file | qc_error | no_dub_text | empty | silent | cer | sim | duration | peak`. `sim` is the mean of the similarity to the original of this line and to the chosen character reference; `sim_ref` is the character's baseline (the mean pairwise cosine of their originals, `qa/baseline.json`); the check is `sim ≥ qa.min_sim_ratio · sim_ref`.
- `decisions.jsonl` (CLI): `{id, decision, attempt, by (auto|agent|human), note, attention, ts}`; `decision ∈ accept | regen | reref | shorten | retranslate | keep_original | skip`. Automatic decisions: pass → `accept`; `cer|sim|peak|empty|silent` → `regen` while `attempt < tts.max_attempts`, then `keep_original` + attention; `duration` → `shorten` + attention (up to 2 times, then `keep_original`); `missing_file|no_dub_text|qc_error` → no decision, attention. Automatic QA does not overwrite agent/human decisions for the current attempt.
- `decisions.agent.jsonl` (agent): `{id, decision, attempt?, note?}` (no `by`/`ts`); `attempt` defaults to the latest attempt in `post`. `accept` applies to the latest processed attempt: `post` keeps only that one, and the build drops a decision for an old attempt as `stale_attempt`.
- `synth --attempt-next` makes a new attempt for `regen`/`reref` decisions whose `attempt` is ≥ the latest successful attempt; `reref` changes the voice prompt (`line` → `char`, `char` → another reference or `line`).

### 4.10 `build/build_manifest.json`, `build/install.json`
- manifest: `{_schema: "babeldub.build/1", created, build_id, game_fingerprint, target_lang, options{codec, sample_rate?}, containers: [...], models: [{name, revision, license, engine, lines}]}`.
  - `containers[]`: `{asset_id, adapter, relpath, src_sha1, out, out_sha1, replaced, kept, codec, lines, inputs_sha1, built, files: [{relpath, src_sha1 (null = new file), out, out_sha1}], replacements: {line_id: {wav, sha1}}}`. `relpath = ""` is an asset at the game root. `inputs_sha1` (source + wav sha1s + options) makes the build idempotent (`status: up_to_date`).
  - `build_id` is the sha1 of the content; `verify` records it in `progress.verify.build_id`, and `install`/`reapply` require a match.
- install: `{installed: [{relpath, asset_id, orig_sha1, new_sha1, backup, created}], build_id, ts, state (installing|installed|uninstalled), restored?}`. `installing` is the write-ahead journal (a failure is rolled back); `created: true` marks a new file, which `uninstall` deletes.

## 5. CLI commands

Common flags: `--project <dir|slug>` (otherwise `BABELDUB_PROJECT`, then the active project, then the current folder), `--pretty`. Subcommands (`cast apply-labels`, `cast select`, `decide apply`) accept `--project`/`--pretty` both before and after the action.

| Module | Command | What it does | Output (key fields) |
|---|---|---|---|
| `commands/env.py` | `doctor [--no-import-check]` | OS, Python, uv, ffmpeg/ffprobe, vgmstream-cli, rubberband, git, GPU (nvidia-smi: name, VRAM, driver), free space, which envs (with an import check) and models are present; recommends a tier; the full report is `BABELDUB_HOME/logs/doctor.json`. Code 4: no uv/ffmpeg/ffprobe; 3: envs/models needed | `tier`, `gpu`, `tools{}`, `envs{}`, `models[]`, `missing[]`, `optional_missing[]`, `to_install[]`, `install{}`, `next` |
| | `setup [--engines qwen3tts,qc] [--models auto\|none\|a,b] [--tools] [--tier] [--lang] [--accept-license M] [--force] [--dry-run] [--no-import-check]` | Without flags: the project's TTS engine + qc, `auto` models for the tier and language, vgmstream. Creates a venv (`uv venv`, torch cu128/cu124/cpu), installs packages per the catalog, downloads weights (`hf download`) and tools (vgmstream from the GitHub release, sha256); "adopts" an existing working venv; `opt_in` models only with `--accept-license`. Plan: `logs/setup-plan.json` | `planned[]`, `done[]`, `skipped[]`, `failed[]`, `blocked[]`, `download_gb`, `free_gb` |
| | `models list [--engine E]` / `models fetch a,b [--accept-license M] [--force] [--dry-run]` | The `data/models.yaml` catalog with licenses and status; downloading weights | `models[]` / `done[]`, `skipped[]` |
| `commands/project.py` | `init <game_dir> --lang ru [--cast all\|main\|list] [--chars A,B] [--mode auto\|translate\|adapt\|official] [--slug] [--name] [--project-dir]` | Creates the project (TTS engine by language), registers the game in `games.json`, makes it active; a repeat is idempotent; another game/language in the same slug is code 2; a language without an engine is code 3 | `project`, `slug`, `engine`, `next` |
| | `status` | Stages, counters, next step: `next_stage` is the first stage in `pending`, `running` or `failed`; stages in `attention`/`failed` go to `attention{stage: summary}` with code 3 | `stages{}`, `counts{}`, `next_stage`, `next`, `attention{}`, `done` |
| | `set <section.key>=<value> ...` | Changes `project.json` settings; the type follows the current value; enumerations are validated (§4.1) | `changed{}`, `warnings[]` |
| | `query [--where k=v,k!=v,k~sub ...] [--fields a,b.c] [--limit 20] [--out file]` | Selects lines (merges lines + assign + characters + translations + qa + decisions). Computed fields: `char` (id or name), `has_text`, `translated`, `verdict`, `decision`, `voiced`; QA metrics as `qa.*` | `rows[]`, `total`, `file?` |
| | `sample --scene X \| --char X \| --ids a,b [--n 30] [--out file.jsonl]` | Context for the agent: lines written to a file (default `logs/samples/<label>.jsonl`) | `file`, `n` |
| | `games` | Game registry | `games[]`, `active` |
| `commands/recon.py` | `scan [--force]` | Detects the engine and middleware (`data/engines.yaml`), calls `discover()` of every adapter (an adapter failure is a warning), writes `recon.json` (with `refs.recon`/`refs.pack`: paths of the skill references), `assets.jsonl`, `report.md` | `engine`, `middleware[]`, `text_systems[]`, `voice_assets[]`, `text_assets[]`, `counts{}`, `level`, `warnings[]`, `anticheat?` |
| | `extract [--kinds dialogue,cutscene,bark\|all] [--limit N] [--ids a,b] [--locales auto\|all\|en-US,...] [--force] [--dry-run]` | `list_lines()` + `extract_audio()` → `lines.jsonl`, `audio/orig/*.wav`; `read_texts()` → `texts.jsonl`. `lines.jsonl` holds only the selected lines (a run with `--limit N` makes a trial project; a run without a limit extends it); `auto` picks one voice locale (source_lang, then en, then the largest); existing WAVs are reused | `lines`, `by_kind{}`, `minutes`, `decoded`, `reused`, `failed`, `texts`, `locales[]`, `voice_locales[]` |
| | `link [--dry-run]` | Links lines to texts (key → normalized key → file name), fills `text`, `source_lang`, the `no_text`/`alt_take`/`duplicate` flags; code 3 when `unlinked > 0` | `linked`, `unlinked`, `with_official_target`, `by_match{}`, `src_lang`, `alt_takes` |
| `commands/cast.py` | `cast [--refs 1..3] [--min-ref-s 3] [--max-ref-s 12] [--threshold 0.35] [--device] [--wait-lock S] [--no-embed] [--force]` | Characters from `speaker_hint` (metadata), the rest as clusters of embeddings from the `qc` worker (if present; otherwise an `unknown` group with a warning), tiers, `cast.policy`, choice of references, `casting_sheet.md`. Code 3: unidentified voices exist, or a selected character has no reference | `characters`, `selected`, `main[]`, `uncertain[]`, `no_ref[]`, `clusters`, `refs` |
| | `cast apply-labels` | Validates and merges `labels.agent.jsonl` (all or nothing) | `updated`, `labeled`, `uncertain[]` |
| | `cast select --chars A,B \| --policy all\|main [--device] [--wait-lock] [--no-embed]` | Changes the character selection, picks references for the new ones | `policy`, `selected[]`, `count`, `new_refs`, `no_ref[]` |
| `commands/translate.py` | `translate plan` | Resolves the mode (`official/adapt/translate`), computes coverage | `mode`, `with_official`, `official_share`, `need_agent`, `no_text`, `next` |
| | `translate use-official [--force]` | Copies the official translation into `translations.jsonl` (`by=official`); does not touch agent/human rows without `--force`; lines without official text go to `missing_official.txt`, code 3 | `copied`, `unchanged`, `missing`, `over_budget` |
| | `batch make [--size 60] [--ids a,b] [--from-decisions] [--force]` | Cuts batches by scene for untranslated lines (or for `adapt`); `--ids` retranslates, `--from-decisions` takes lines with a `retranslate`/`shorten` decision; `--force` recreates open batches | `batches[]`, `lines`, `open` |
| | `batch next` | The first open batch | `batch`, `state (new\|ready\|error)`, `in`, `out`, `errors_file?`; `batch: null, done: true` |
| | `batch submit <bid> [--force]` | Validates `.out.jsonl` (all ids, extra ids, empty `dub_text`, types, placeholders/tags) and merges it into `translations.jsonl`; errors give code 2 and `<bid>.errors.json`; exceeding the syllable budget gives code 3 | `merged`, `errors[]`, `over_budget[]`, `over_fixed[]`, `picked_variants` |
| | `glossary check` | A glossary term in the source without the agreed translation in `dub_text` is a violation (code 3, `glossary_violations.jsonl`) | `terms`, `checked`, `violations[]`, `total` |
| | `glossary apply` | Validates and merges `glossary.agent.jsonl` into `glossary.jsonl` | `added`, `updated`, `total` |
| `commands/voice.py` | `synth [--chars A,B] [--ids a,b] [--limit N] [--attempt-next] [--engine qwen3tts\|chatterbox\|dummy] [--device cuda\|cpu] [--wait-lock S] [--batch N] [--force] [--dry-run]` | Prepares the voice prompts (§4.1), runs the engine worker in its venv (one model load per run, batches per character), writes `synth/raw` and `results.jsonl` after every line; holds `gpu.lock`. Idempotent: the same text with the same engine/model is not repeated | `done`, `failed`, `remaining`, `rtf`, `batch`, `vram_peak_gb`, `by_ref{}`, `skipped{}`, `attention[]` |
| | `post [--ids] [--limit] [--force] [--dry-run] [--stretch-method rubberband\|ffmpeg-rubberband\|atempo]` | Trims silence, fits the duration (within `max_stretch`), edge silence and loudness as in the original, format as in the original (§4.8) | `done`, `up_to_date`, `over_length[]`, `stretch{}` |
| `commands/qa.py` | `qa [--ids] [--limit] [--force] [--rebaseline] [--device] [--wait-lock S]` | The `qc` worker: ASR (CER against `dub_text`), voice similarity to the original and to the character reference relative to the character's baseline, duration, peaks → `qa.jsonl`; automatic decisions → `decisions.jsonl` (§4.9). Without the `qc` env: code 4 | `checked`, `pass`, `fail`, `accept`, `regen`, `shorten`, `keep_original`, `attention[]`, `pending`, `cer_median`, `sim_median`, `device` |
| | `decide apply [--by agent\|human]` | Validates and merges `decisions.agent.jsonl` (all or nothing; repeats are skipped) | `applied`, `skipped`, `by_decision{}` |
| | `report` | `qa/report.md` with per-character totals and a "needs a decision" list | `file`, `lines`, `checked`, `pass`, `attention` |
| `commands/pack.py` | `build [--codec auto\|pcm16\|fadpcm] [--only ASSET_ID,...] [--sample-rate N] [--force] [--dry-run]` | Lines whose latest decision is `accept` are grouped by container; `adapter.build()` runs into staging and, on success, the result moves to `build/out/<relpath>`; manifest (§4.10). Unchanged inputs are `up_to_date`; a failure of one container does not affect the others | `containers[{asset_id, status, replaced, kept, codec}]`, `replaced`, `total_containers`, `build_id`, `failed?`, `attention?` |
| | `verify [--game-runtime]` | The output exists and matches `out_sha1`; then `adapter.verify()` for every container (§5.1) | `ok`, `verified`, `checks`, `problems[]`, `build_id` |
| | `install [--force] [--dry-run]` | Requires `verify` of the current build. Refuses if the game is running or an anti-cheat is found (`--force` bypasses only these two guards), or if a game file does not match the build's source. Backs up the originals into `build/backup`, replaces atomically, journal `install.json` | `installed[]`, `unchanged`, `files`, `backup`, `backed_up` |
| | `uninstall [--force] [--dry-run]` | Restores the originals from the backup with a sha1 check; a file changed both by the dub and by the game is left alone without `--force` | `restored[]`, `already_original`, `state`, `left_changed?` |
| | `reapply [--force] [--dry-run]` | Reinstalls after a game update: files that are original again are replaced again; containers changed by the game go to `changed[]` with a `build --only` hint | `reapplied[]`, `current`, `changed[]` |

### 5.1 Adapters and stages (`adapters/base.py`)

- `list_lines(game_dir, asset)`: §4.3 rows without decoding. `extract_audio(...)` → `[{id, wav, sr, ch, dur_s}]`, where `wav` is an absolute path or a path relative to the project/`out_dir`; it may return `{id, error}` for a single line; `BabelDubError(EXIT_ENV)` turns the whole command into code 4.
- `build(game_dir, asset, replacements{line_id: wav}, out_path, options)`: `out_path = <staging>/<asset.relpath>`; `options = {codec: auto|pcm16|fadpcm, sample_rate?}`. Returns `{out, replaced, kept, codec, files?}`: without `files`, a file maps to `asset.relpath` and a folder to `asset.relpath/<subpath>`; additional files are written to `<staging>/<their relpath>` and listed in `files`. Writing inside the game folder is `EXIT_USAGE`. A format v0.1 does not rebuild raises `BabelDubError(EXIT_USAGE, hint=...)` pointing to `skills/babeldub-pack/references/formats/*.md`.
- `verify(game_dir, asset, built, options)`: `options = {game_runtime, codec, files: [{relpath, path}], replaced_ids: [...], replacements: {line_id: wav}, sample_rate?}`; `replacements` contains only wavs whose sha1 did not change after the build. Returns `{ok, checks[], problems[], details?}`.
- v0.1 adapters: `fmod` (A: `.bank` and `.fsb` banks, PCM16/FADPCM rebuild that keeps the number, order and names of subsounds), `loose` (A: loose audio files, re-encoding into the original's format), `unity-localization` (reading texts only), `wwise` (B: reading `.bnk/.pck/.wem`; the build is manual, per the reference). The per-engine matrix is in `docs/ENGINES.md`.

## 6. Worker protocol (JSONL over stdin/stdout)

Workers are self-contained scripts in `runtime/src/babeldub/workers/*.py`. They **do not import** `babeldub` (it is not in the engine venv) and run as `<env python> -u <worker.py>`. Logs go only to stderr (the worker redirects fd 1 to stderr and speaks the protocol over a duplicate of the original stdout, so output from native libraries cannot corrupt it). Each stdin line is a command; each command gets exactly one reply line on stdout.

```text
→ {"op": "load", "model": "<path or repo>", "device": "cuda", "dtype": "bf16", "options": {...}}
← {"ok": true, "op": "load", "sr": 24000, "device": "cuda:0", "info": {"batch": true, ...}}
→ {"op": "synth", "id": "...", "text": "...", "lang": "ru", "ref_wav": "...", "ref_text": "...", "ref_lang": "en", "out": "...wav", "seed": 1234, "params": {"x_vector_only": false, "max_new_tokens": 400}}
← {"ok": true, "op": "synth", "id": "...", "wav": "...", "sr": 24000, "dur_s": 2.1, "rtf": 0.4, "gen_s": 0.8, "mode": "icl", "hit_token_limit": false, "vram_peak_gb": 4.3}
← {"ok": false, "op": "synth", "id": "...", "error": "CUDA out of memory"}
→ {"op": "synth_batch", "seed": 1234, "items": [<synth>, ...]}   ← {"ok": true, "results": [<synth reply>, ...]}
→ {"op": "stats"} / {"op": "ping"} / {"op": "quit"}
```

v0.1 engines: `qwen3tts` (`tts_qwen3.py`, ICL prompt ref_audio + ref_text), `chatterbox` (`tts_chatterbox.py`), `dummy` (`tts_dummy.py`: a TEST engine without a neural network for CI and end-to-end tests; runs in the CLI interpreter; supports `synth_batch`).

QC worker (`workers/qc_worker.py`, env `qc`):
```text
→ {"op": "load", "asr_model": "<dir>", "sv_model": "<dir>", "sv_savedir": "<dir>", "device": "cuda", "compute_type": null}
→ {"op": "asr", "id": "...", "wav": "...", "lang": "ru"}            ← {"ok": true, "id": "...", "text": "...", "lang": "ru", "dur_s": 2.1}
→ {"op": "embed", "id": "...", "wav": "..."}                        ← {"ok": true, "id": "...", "emb": [192 floats], "dur_s": 2.1}
→ {"op": "sim", "id": "...", "wav": "...", "refs": ["..."]}         ← {"ok": true, "id": "...", "sim": 0.71, "sims": [...]}
```

The CLI-side client is `babeldub/workers/client.py`: `WorkerClient(env|python_path, script, timeout, load_timeout, log_path).load()/request()/close()` (timeouts kill the process; after a crash it restarts, repeats `load` and retries the request once; errors `WorkerError`, `WorkerTimeout`, `WorkerCrashed`; a missing env is `EXIT_ENV`). `GpuLock(project/state/locks/gpu.lock, owner, wait_s)` is an exclusive PID lock on the GPU; the lock of a dead process is released, a busy lock is `EXIT_USAGE` with a `--wait-lock` hint. The qc session for `cast`/`qa` is `qa/qc.py:QcSession`.

## 7. Audio API (`babeldub/audio`)

`io.py`: `read_audio(path) -> (np.ndarray float32 [frames, channels], sr)`, `write_wav(path, data, sr, subtype="PCM_16")` (atomic), `probe(path) -> {sr, ch, dur_s, frames, subtype, format}`.
`dsp.py`: `to_mono` (1-D), `to_channels(data, ch)`, `resample(data, sr_in, sr_out, quality=16)` (polyphase sinc with a Kaiser window, no external dependencies), `trim_silence(data, sr, threshold_db=-45, pad_s=0.04) -> (data, lead_s, trail_s)`, `voiced_bounds(data, sr) -> (start, end) | None`, `speech_seconds(data, sr)`, `loudness_lufs(data, sr)` (BS.1770-4, K-weighting, gating), `rms_db`, `level_db(data, sr, measure="auto")` (LUFS from 0.4 s, RMS for shorter), `peak_db(data)`, `true_peak_db(data, sr)`, `match_loudness(data, sr, target, ceiling_db=-1.0, measure=...)`, `fade(data, sr, ms)`, `pad_silence(data, sr, lead_s, trail_s)`.
`stretch.py`: `time_stretch(in_wav, out_wav, ratio, method=None) -> "rubberband" | "ffmpeg-rubberband" | "atempo" | "copy"` (ratio = output duration / input duration; the rubberband CLI with `-3 -D`, then ffmpeg's rubberband filter, then an `atempo` chain; nothing available is `EXIT_ENV`), `available_methods()`, `atempo_chain(tempo)`.

## 8. Text (`babeldub/text`)

`placeholders.py`: `extract_placeholders(text) -> list[str]` (`{0}`, `{name}`, `%s`, `%d`, `<tag>`, `[tag]`, `\n`, Unity Smart Strings `{Var:plural:...}`), `check_placeholders(src, tgt) -> [{severity: error|warn, kind: missing|extra, token, expected, found}]`, `has_errors(problems)`.
`syllables.py`: `count_syllables(text, lang)` (vowels for ru/uk/pl…; vowel groups for de/es/fr…; a heuristic for en; CJK by characters), `budget_syllables(dur_s, lang) -> int | None` (syllables per second by language), `fit_status(est, budget, over=1.15, under=0.5) -> ok|over|under|None`.
`normalize.py`: `prepare_for_tts(text, lang, numbers=True, yo="keep"|"strip"|"restore")`: removes markup (`strip_markup`, including speaker labels), spells numbers out (`numbers_to_words`, if `num2words` is available), handles `ё` (`apply_yo`).

## 9. Model catalog (`runtime/src/babeldub/data/models.yaml`)

```yaml
engines:
  qwen3tts:
    env: qwen3tts
    torch: {version: "2.8.0", index: "https://download.pytorch.org/whl/cu128"}
    packages: ["qwen-tts", "soundfile", "numpy"]
    worker: tts_qwen3.py
    languages: [zh, en, ja, ko, de, fr, ru, pt, es, it]
models:
  - name: Qwen3-TTS-12Hz-1.7B-Base
    repo: Qwen/Qwen3-TTS-12Hz-1.7B-Base
    engine: qwen3tts
    license: Apache-2.0
    commercial: true
    size_gb: 4.5
    vram_gb: 6
    tiers: [8gb, 12gb, 24gb]
```
- Engines: `qwen3tts`, `chatterbox`, `qc` (faster-whisper + SpeechBrain ECAPA), `dummy` (`env: null`, for tests). API: `babeldub.envsetup.catalog` (`load_catalog`, `default_model`, `model_dir`, `is_model_downloaded`, `engine_for_language`, `tier_for_vram`).
- Models with `commercial: false` get `opt_in` and are installed only with `--accept-license`.
- By default `synth` takes the downloaded general (not language-specific) model of the engine, the larger one first; language fine-tunes (`qwen3-tts-12hz-1.7b-ru-stress-cf`) are used only when set explicitly in `tts.model`: on the test lines they preserved the original actor's voice worse.
- Human-readable overview of models, tiers, licenses and measured VRAM: `docs/MODELS.md`.

## 10. Tests and code quality

- `pytest` in `runtime/tests/<area>/`; synthetic data only (generated FSB5/banks/texts). No game assets in the repository.
- `runtime/tests/e2e/test_pipeline_synthetic.py` runs every stage through `cli.main` on a synthetic Unity game with an FMOD bank (the `dummy` engine, a fake qc worker, installation into a copy of the game and rollback); it also checks the honest `EXIT_ENV` of `qa` without the `qc` env.
- Python ≥ 3.10, type annotations, `ruff` (config in `pyproject.toml`).
- WAV/FLAC through `soundfile`; paths through `pathlib`; UTF-8 everywhere; atomic writes through `core.jsonl`.
- Every command: a handler `def cmd_x(args) -> int` that ends with `return emit({...}, code)`; stage progress through `project.mark_stage(stage, status, **summary)`.
