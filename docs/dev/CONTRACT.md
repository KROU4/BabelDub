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
8. **Dubbing in the original character's voice.** The voice prompt is always made of the character's original recordings (`tts.ref_strategy`, §4.1): the default chain is VoxCPM2 with `bootstrap` prompts (the character's long original timbre reference, then a target-language prompt from its first accepted dubs), best of N takes by natural length, an exact fit to the original in `post`, and RVC timbre transfer per main character (`vc`); QA compares the voice of the synthesis with the character's original.

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
├── envs/<engine>/     engine venvs: voxcpm, applio, qwen3tts-fast, qwen3tts, chatterbox, qc (marker babeldub-env.json)
├── engines/<engine>/  source checkouts of git engines (applio at the catalog's pinned commit; logs/<model>/ = RVC training)
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
│             analysis.jsonl analysis.npz      analyze: measurements of the originals (§4.11)
├─ audio/orig/<uid>.wav                 decoded originals
├─ cast/ characters.jsonl assign.jsonl refs.jsonl refs/<char_id>/<uid>.wav casting_sheet.md labels.agent.jsonl
│        embeddings.npz                 cache of ECAPA embeddings of the originals (keyed by wav size+mtime)
├─ translate/ glossary.jsonl glossary.agent.jsonl style_bible.md translations.jsonl
│             batches/index.json batches/<bid>.in.jsonl batches/<bid>.out.jsonl batches/<bid>.errors.json
│             missing_official.txt glossary_violations.jsonl
├─ synth/ results.jsonl raw/<uid>/a<attempt>.wav refcache/<sha>.wav (trimmed long references)
│        prompts/<char_id>/ src-<hash>.wav (long timbre reference) tgt.json tgt-<hash>.wav (target-language prompt)
├─ post/ post.jsonl audio/<uid>.wav
├─ vc/ <char_id>/ model.pth model.index model.json dataset/<uid>.wav   audio/<uid>.wav (converted takes)
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
  "cast": {"policy": "main", "list": [], "main_share": 0.7, "merge_threshold": 0.75},
  "translation": {"mode": "auto"},
  "tts": {"engine": "auto", "model": null, "tier": "auto", "ref_strategy": "auto", "line_ref_min_s": 1.6, "best_of": 3, "max_attempts": 3,
          "bootstrap_lines": 4, "bootstrap_min_accepted": 3},
  "post": {"tempo_min": 1.0, "tempo_max": 1.15, "tolerance": 0.03, "fixed_length_tolerance": 0.03, "true_peak_db": -1.0},
  "vc": {"epochs": 20, "minutes": 30.0, "index_rate": 0.5, "protect": 0.33, "f0_method": "rmvpe"},
  "qa": {"max_cer": 0.15, "min_sim_ratio": 0.75, "min_sim_ratio_xlang": 0.45, "min_sim_abs": 0.25, "min_identity_margin": -0.05, "max_duration_ratio": 1.35},
  "build": {"codec": "auto"},
  "install": {"backup": true}
}
```
- `cast.policy`: `all` | `main` (the smallest set of characters that covers `main_share` of all speech) | `list` (names/IDs in `cast.list`).
- `cast.merge_threshold` (0 < x ≤ 1, default 0.75): a voice cluster whose centroid cosine to a metadata character's voice centroid reaches it is merged into that character (§4.5). QA uses the same value to treat two characters with that similar voices as one voice (§4.9).
- `qa.min_sim_ratio` (0.75) applies to a same-language dub; `qa.min_sim_ratio_xlang` (0.45) applies when `source_lang` differs from `target_lang` (the normal case; an unknown `source_lang` counts as different; languages compare by their primary subtag). `qa.min_sim_abs` (0.25) is the absolute floor of the similarity threshold; `qa.min_identity_margin` (−0.05) is the lowest allowed speaker identity margin (§4.9).
- `tts.engine`: `auto` (written by `init`) = `voxcpm` when the device is CUDA, its env exists and it speaks the target language; otherwise the first of `qwen3tts-fast`, `qwen3tts`, `chatterbox` that speaks it (`synth` puts the choice and the reason into `notes`). A CUDA-only engine (`voxcpm`, `qwen3tts-fast`) falls back along its catalog `fallback` chain off CUDA, and to the first installed engine of the chain when its own env is missing (§5 `synth`).
- `tts.ref_strategy` `bootstrap` (what `auto` means for `voxcpm`; with an engine without a continuation prompt it works as `auto`, with a note): per character a **long timbre reference** of 3–4 clean original lines (no flags, 4–7.5 s each, relaxed to 2–12 s; the character's `cast/refs.jsonl` lines first, then closest to 6 s) joined with 0.35 s gaps and trimmed to speech ± 50 ms, ~15–20 s (`synth/prompts/<char_id>/src-<hash>.wav`), is the `ref_wav` of every line (no transcript, never cut). **Phase 1:** the first `tts.bootstrap_lines` lines of the character (plain lines with text of 2.5–8 s first, by scene/order) are synthesized with it alone (`ref_mode: bootstrap-src`); the character's other lines are skipped as `bootstrap_wait`. **Phase 2:** once at least `tts.bootstrap_min_accepted` lines of the character have a latest `accept` decision for their latest post attempt, a current QA row with `cer ≤ qa.max_cer` and are not `nonverbal`, up to 4 of them (lowest CER first, ≤ 26 s) are joined from their latest post row's wav (the `vc` output when there is one) into a **target-language prompt** with the exact `text` of their synth rows (`synth/prompts/<char_id>/tgt.json`, frozen once written); the remaining and regenerated lines get it as `prompt_wav`/`prompt_text` (`ref_mode: bootstrap-tgt`). When every phase-1 line is settled (current QA row, `keep_original`/`skip`, or no ok take after `tts.max_attempts`) and too few were accepted, the rest goes on as `bootstrap-src` with `ref_reason`. A `reref` switches the line to `line`/`char`. The `unknown` group has no long reference and uses `auto`.
- `tts.best_of` (N): every line is generated N times with seeds `seed + k`; candidates that failed or ran away are dropped and the one whose speech span (`audio.fit.speech_span`: 20 ms frames within 42 dB of the loudest) is closest in |log ratio| to the original's speech span becomes `a<attempt>.wav` (§4.7 `best_of`). Batched engines run one batch per candidate round.
- U+0301 stress marks are stripped from the dub text (and prompt transcripts) for every model whose name does not contain `stress` (`voice.keeps_stress`): VoxCPM2 and Qwen3-TTS Base voice them as extra sounds.
- `post.tempo_max` caps the speed-up of `post` (§4.8); `post.tempo_min` (default 1.0: speech is never slowed down) below 1 allows slowing a plain line down to the original's speech span; `post.tolerance` is the overrun that is not sped up; `post.fixed_length_tolerance` the length limit of `fixed_length` lines (also QA's).
- `vc.*`: RVC training (`epochs`, `minutes` of the dataset) and conversion settings (`index_rate`, `protect`, `f0_method`) of `vc train`/`vc` (§5).
- For the other engines `tts.ref_strategy` is **BabelDub's core principle: dubbing in the original character's voice.** `line`: the voice prompt is the original line itself with its source text, so the model carries over the timbre *and* the emotion, pace and delivery of that exact phrase (a scream stays a scream). `char`: the character's best clean reference (for short lines, interjections, lines without a transcript or with music). `auto`: `line` if the original has source text, `line_ref_min_s ≤ dur ≤ 15 s` and no `has_bed`/`nonverbal` flags, otherwise `char`; if the character has no reference, the last resort is the short original. A reference longer than 15 s is cut at its quietest point and fed in `x_vector_only` mode (timbre only). ICL needs the exact transcript of its prompt: a line whose transcript does not fit its audio goes to `char` or timbre-only mode (§4.7 ICL pre-check), and alt takes are not voiced at all (followers, §4.3). QA compares the voice of the synthesis with the character's original (speaker similarity) and sends drifted voices back for regeneration.
- `translation.mode`: `auto` (`adapt` when at least one linked voiced line has an official target text; otherwise `translate`) | `translate` | `adapt` (the agent turns the official text into spoken dub text: numbers as words, Latin names transliterated, colloquial, length budget; lines without one are translated in the same batches) | `official` (voice the official text as is; only when set explicitly). `translate plan` writes the resolved mode to `translation.resolved_mode`.
- Optional keys the CLI reads: `target_locale` (`pt-BR` etc., written by `init`), `tts.device` (`cuda|cpu`), `tts.dtype` (`bf16`), `tts.batch_size` (lines per generation call, default 4), `tts.params` (engine generation kwargs), `tts.options` (model load options, for example `attn_implementation`), `tts.yo` (`keep|strip|restore` for `prepare_for_tts`), `qa.silence_db` (−50), `qa.asr_model`, `qa.sv_model`, `qa.mos_model` (`utmos22-strong`), `qa.emotion_model` (`emotion2vec_plus_base`, opt-in) (qc model folders).
- `set` validates the enumerations `compute.device`, `cast.policy`, `translation.mode`, `tts.ref_strategy` (`auto|bootstrap|line|char`), `tts.tier`, `tts.engine` (`auto|voxcpm|qwen3tts-fast|qwen3tts|chatterbox|dummy`; `init` writes `auto` when any engine speaks the language); `_schema`, `cli_version`, `created`, `slug`, `game_dir`, `target_lang` are protected (another language means a new project).

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
- `flags`: `duplicate`, `alt_take` (set by `link` for several records with one key: the main record is the one with `table_key`, then the one with the exact key, then `_best`, then the highest `_vN`), `nonverbal`, `has_bed` (speech mixed with music/noise), `no_text` (no text), `no_audio` (failed to decode), `subtitle_mismatch` (set and cleared by `analyze`: the ASR of the original differs from `text.src` by CER > 0.35 on a line with words, §4.11). A repeated `extract` keeps `text` and the stage flags; `analyze` restores `subtitle_mismatch` on its next run.
- **Followers** (`core/takes.py:follower_map`). Voiced lines of one `asset_id` with one `text.key`, where at least one member is flagged `alt_take` or `duplicate`, form a group with one **leader**: the line whose `entry.key` equals `text.key`, else an unflagged member, else a member whose key contains `best` (any case), else the lowest `order`/`entry.index`; a member without audio (`no_audio`, zero `audio.dur_s`) never leads while another one has audio. The other members are followers. Alt takes share the subtitle but their audio often does not match it, so dubbing them separately wastes GPU time and an ICL prompt with their transcript can run away. `synth`, `post` and `qa` skip followers (`skipped.follows` / `follows`); `status` counts them as `counts.follows`; `query` exposes `follows` (the leader id, or null); `build` gives every follower its leader's accepted post audio (§4.10) and ignores the follower's own decisions; when the leader is not accepted, followers keep the original.

### 4.4 `inventory/texts.jsonl`
`{key, table, locale, lang, text, source}`: every string in every language from the text assets (`lang` is a two-letter code). Optional: `entry_id`, `key_missing` (key not found in the shared table), `audio`/`file`/`voice` (audio file name; `link` uses it in the file-name pass).

### 4.5 `cast/*`
- `characters.jsonl`: `{char_id, name, name_tgt, gender (m|f|u), age (child|adult|old|u), n_lines, speech_s, share, tier (main|secondary|minor), selected, source (metadata|folder|cluster|unknown), conf, labeled, notes, merged_clusters?}`. `char_id` is the Unicode slug of the name (`cast.characters.char_id_for`); clusters are `cluster_NN`; lines without a speaker when there is no qc worker go to `unknown`.
- **Cluster merge.** Clusters are numbered by speech first; then every cluster whose centroid reaches `cast.merge_threshold` (cosine) with the closest metadata character's voice centroid (`qa.baseline.char_centroids`: up to 12 longest clean originals, at least 2, the lines of the QA baseline) is merged into that character (`cast.cluster.merge_targets`): its lines are assigned to him, his `n_lines`/`speech_s` grow, `source` stays `metadata` and `merged_clusters: [{cluster_id, cos, n_lines, speech_s}]` records it; the merged `cluster_NN` disappears (the numbers of the others do not change). Clusters are not merged with each other beyond what the clustering threshold already decided. Calibration (Low-Budget Repairs, ECAPA): zbyszek ~ cluster_04 0.88, maciek ~ cluster_05 0.89, every other pair 0.06–0.38. `casting_sheet.md` lists the merges.
- `assign.jsonl`: `{id, char_id, conf}` (for a cluster, `conf` is the cosine to the centroid; for a line of a merged cluster, the cluster-to-character centroid cosine).
- `refs.jsonl`: `{ref_id, char_id, line_id, wav, text, lang, dur_s, score, chosen, centroid_cos?, active_rms_db?}`. The `unknown` group has no references: its lines are voiced with the `line` strategy.
- `labels.agent.jsonl` (written by the caster agent): `{char_id, name, name_tgt, gender, age, notes}`; extra fields or an unknown `char_id` reject the whole file (code 2).
- `embeddings.npz`: cache of embeddings by `line id`.

### 4.6 `translate/*`
- `batches/<bid>.in.jsonl` (CLI): first line `{"_batch": bid, "scene", "scenes", "target_lang", "source_lang", "mode", "n", "glossary": [...], "style": "...", "out": "<bid>.out.jsonl", "rules"}`, then `{id, key, speaker, src, src_segmented?, tgt_official, dur_s, speech_s, rate_syl_s, budget_syll, fixed_length, placeholders, context_prev, context_next, task?, current?}` (`task`: `adapt | retranslate | shorten`; `current` is the current `dub_text`).
- **Budget** (`commands/translate.Timing`): `budget_syll` = `round(speech_s × rate_syl_s)`. `speech_s` is the original's speech span: `speech_s` of the line's `inventory/analysis.jsonl` row when present, else the span of its `vad_segments`, else the energy VAD of the original audio (`audio.fit.speech_bounds`), else `dur_s`. `rate_syl_s` is the median syllable rate of the line's character over its accepted dubs (latest decision `accept` for the latest post attempt; `count_syllables(dub_text)` / the dub's raw speech seconds `ratio_raw × target_s` of its post row), clamped to 3–9 syl/s; until the character has 3 such dubs, the language default (`text.syllables.SYLLABLE_RATE`). The same budget is used by `batch submit` and `translate use-official`.
- `src_segmented`: `src` with ` / ` at each pause of the original of at least 250 ms (from `vad_segments`, gaps under 250 ms merged, else the energy VAD); without word timings each mark goes to the word boundary whose syllable share is closest to the speech-time share before the pause, preferring a boundary after punctuation (`text.syllables.segment_text`). Absent when the original has no such pause. The rules tell the agent to keep those pauses as punctuation, to write ё and to avoid stress homographs.
- In `adapt` batches a line with an official text has `task: "adapt"` and its `tgt_official`; lines without one have no `task` (translated from `src`). `submit` takes `tgt_official` as the `sub_text` of an adapted line when the agent gives none.
- `batches/<bid>.out.jsonl` (agent): `{id, dub_text, sub_text?, variants?: [shorter...], notes?}`. The source's placeholders and tags must be present in `dub_text`, `sub_text` and every variant (`check_placeholders`; stage directions like `[snoring]` and line breaks are soft).
- `batches/index.json`: batch state (`open | error | merged`, ids, sha1 of the merged `.out`); `<bid>.errors.json`: errors of the last submit.
- `translations.jsonl` (CLI): `{id, dub_text, sub_text, lang, by (official|agent|human), rev, ts, est_syll, budget_syll, fit (ok|over|under|null), batch?, variants?, picked_variant?, agent_text?, notes?}`. `fit = null` when the duration is unknown; on `over` the CLI itself picks the first fitting variant from `variants` (the agent's original text goes to `agent_text`). `dub_text` keeps the string with tags as in `src`; `prepare_for_tts` cleans it before synthesis.
- `glossary.jsonl` (CLI, canonical): `{term, translation, pronounce?, note?}`; the agent writes `glossary.agent.jsonl` and the CLI merges it (`glossary apply`).

### 4.7 `synth/results.jsonl`
`{id, attempt, engine, model, wav, sr, dur_s, seed, ref_id, text, status (ok|error), error?, rtf?, ts}` plus `ref_mode (bootstrap-src|bootstrap-tgt|line|line-xvec|char|char-xvec)` (`-xvec`: timbre only, the transcript was not sent), `prompt_id?`/`prompt_wav?` (the target-language prompt of `bootstrap-tgt`), `best_of?: {n, usable, picked_seed, speech_s: [...], target_speech_s, runaways?}` (N > 1; `seed` is the picked candidate's), `ref_wav`, `ref_trimmed`, `lang`, `char_id`, `reason (new|retry|retranslated|regen|reref|force)`, `key` (hash of the inputs: engine, model, language, text, reference), `est_s` (spoken length estimated from the dub text), `max_new_tokens?` (the generation cap sent), `ref_reason?` and `src_rate?` (ICL pre-check, below), `retry_reason?` (`runaway`) with `discarded?: [{ref_mode, ref_id, dur_s, hit_token_limit}]`, `batch?` (batch size; a batch shares one seed), `hit_token_limit?`, `vram_peak_gb?`. Failed rows have `status=error`, `wav=null`.
- **Length estimate and cap.** `est_s` = `count_syllables(dub text) / rate`, where the rate is `text.syllables.SYLLABLE_RATE` of the target language (ru 5.5 syl/s) or 5.0 for other languages; `target_s` is the original's `audio.dur_s`. For engines with a token cap (`qwen3tts-fast`, `qwen3tts`, `dummy`): `max_new_tokens = ceil(12.5 · cap_s)`, `cap_s = max(6.0, 2.0 · max(est_s, target_s) + 2.5)` (Qwen3-TTS 12Hz generates 12.5 tokens per second of audio); an explicit `tts.params.max_new_tokens` wins.
- **ICL pre-check.** ICL cloning needs the exact transcript of its prompt. For a `line` prompt the CLI computes `src_rate` = syllables of `text.src` / speech seconds of the original (`audio.dsp.speech_seconds`); outside [1.2, 8.5] syl/s the transcript probably does not match the audio, and the line is voiced with the character reference (`char`), or with the line in timbre-only mode (`line-xvec`) when the character has none (or a `reref` asked for the line). `ref_reason` says why.
- **Runaway policy.** An output is a runaway when it hit the cap (`hit_token_limit`) or `dur_s > 2.2 · max(est_s, target_s) + 2.0`. It is discarded (never an `ok` attempt) and the line is retried alone, not in a batch: first timbre-only on the same reference (engines that use transcripts), then, for a `line` prompt, the character's chosen reference in ICL mode. The row of the first usable take has `retry_reason: "runaway"` and the discarded takes in `discarded`; if every try runs away the row is `status=error`, `error: "runaway"` (no file is kept) and the next `synth` makes a new attempt with a new seed until `tts.max_attempts`; then the line is in `attention` and the agent decides (usually `keep_original`).
- A prompt without a transcript (a character reference without text) is sent timbre-only (`char-xvec`). An `ok` row with `hit_token_limit` (kept by versions before the runaway guard) is not a usable take: `synth` voices the line again and `post` skips it.
- **Batches** are built from lines of similar `est_s` (sorted by character, then `est_s`), because a batch generates until its longest item finishes or the largest cap is hit.

### 4.8 `post/post.jsonl`
`{id, attempt, wav, dur_s, orig_dur_s, target_s, ratio, ratio_raw, tempo, stretch, stretch_method, lufs_in, lufs_out, level_ref, measure (lufs|rms), peak_db, lead_s, trail_s, pause_cut_s, onset_deltas, chunk_tempo: {n, min, max}, sr, ch, frames, exact, fixed_length, over_length, src_wav, src_key, ts}`; rows written by `vc` add `stage: "vc"`, `pre_vc_wav` and `vc: {char_id, engine, model_key, trained, index_rate, protect, f0_method}`.
- Speech spans use `audio.fit.speech_bounds` (20 ms frames within 42 dB of the loudest). `dur_s` is the duration of the whole file; `target_s` is the speech span of the original; `ratio` = speech span of the dub / `target_s` after fitting, `ratio_raw` before it.
- **Fit** (`audio.fit.fit_segments`): the dub is resampled to the original's rate and split into speech chunks (energy VAD, silences of at least 120 ms split). **Speech is never slowed down** (`post.tempo_min` 1.0): a dub shorter than the original keeps its tempo and the rest is silence. A plain line whose speech does not fit the room (file length minus the original's speech onset) first loses part of its internal pauses: every pause between chunks shrinks by the same share of what it may lose, down to max(80 ms, half its length) (`pause_cut_s`); what still does not fit is sped up to at most `post.tempo_max` (an overrun within `post.tolerance` is not sped up; `tempo` > 1 = faster; `stretch` = 1/`tempo`; pitch kept, `audio.stretch.time_stretch`). A `fixed_length` line is aligned segment by segment: the original's speech segments are `vad_segments` of its `inventory/analysis.jsonl` row when present, else its energy-VAD chunks; dub chunks and original segments are matched by a monotone DP (both sides cut into the same number of contiguous groups, minimising Σ|log(dub group span / original group span)| + 0.1 per merged item, `audio.fit.align_chunks`); each dub group starts at its segment's onset and is fitted to that segment's length the same way (pauses, then tempo in [1.0, `post.tempo_max`]); shorter is fine, and a group that still overruns pushes the next one later. `onset_deltas` are the placed minus the original onsets per group (one value for a plain line), `chunk_tempo` summarises the group tempos and `tempo` is their length-weighted mean. 10 ms fades, loudness of the speech matched to the original's speech (LUFS, RMS when it is shorter than 0.4 s), the speech placed at the original's speech start (moved earlier when it would run past the end), true peak ≤ `post.true_peak_db`, channels as in the original. The file has exactly the original's sample count (`exact: true`) whenever the fitted speech fits into it; speech is never cut, so a dub that does not fit makes the file longer (`exact: false`, `not_exact[]` in the output).
- `over_length`: `ratio > 1 + post.fixed_length_tolerance` or the speech does not fit the file (fixed), `ratio > qa.max_duration_ratio` (other lines).
- Without the original audio, silence of the line's `audio.dur_s`/`sr`/`ch` stands in for it (level −20 LUFS).
- `post` treats a line as up to date when its latest row (also a `vc` row) has the same `attempt` and `src_key` and its file exists; `--force` writes a new pre-conversion row, which `vc` converts again.
- Followers (§4.3) are not processed, even when an older run synthesized them (`follows` in the output).

### 4.9 `qa/*`
- `qa.jsonl`: `{id, attempt, char_id, wav, asr_text, cer, cer_skipped, sim, sim_line, sim_char, sim_ref, sim_ratio, sim_threshold, sim_skipped, margin, closest_char, ratio, dur_s, peak_db, verdict (pass|fail), reasons[], ts}`; `reasons ∈ missing_file | qc_error | no_dub_text | empty | silent | cer | sim | identity | duration | peak`.
  - `sim` is the mean of the similarity to the original of this line and to the chosen character reference; `sim_ref` is the character's baseline (the mean pairwise cosine of their originals, `qa/baseline.json`). The check is `sim ≥ sim_threshold`, where `sim_threshold = max(sim_ratio · sim_ref, qa.min_sim_abs)` and `sim_ratio` is `qa.min_sim_ratio_xlang` for a dub in another language than the original audio, else `qa.min_sim_ratio` (§4.1). Calibration (Low-Budget Repairs, Russian Qwen3-TTS dub of English originals): `sim_ref` 0.76, good dubs 0.38–0.66 against the character's centroid, clear failures 0.08–0.11; the same-language bar 0.75 · 0.76 = 0.57 failed 27 of 30 dubs whose Whisper CER median was 0.0.
  - **Identity margin.** When at least two characters have voice centroids (`qa.baseline.char_centroids` over the checked characters plus every selected or `main` one; embeddings come from `cast/embeddings.npz` or the qc worker), `margin` = cosine of the dub to its own character's centroid − the best cosine to another character's centroid (`qa.metrics.identity_margin`); `closest_char` is the character whose centroid is nearest. A character whose centroid is at least `cast.merge_threshold` similar to the own one is the same voice (a merged or not yet merged cluster, one actor in two roles), not another character. `margin < qa.min_identity_margin` gives the reason `identity`. Without two centroids, or without the own one, `margin` is null.
  - **Nonverbal lines** (`flags` has `nonverbal`: screams, laughs) are measured but never fail on `sim`, `identity` or `cer`: `sim_skipped` and `cer_skipped` are `"nonverbal"` (else null).
- QA checks the `wav` of each line's latest `post/post.jsonl` row (the `vc` output after conversion); a QA row is current only for the same `attempt` and `wav`, so a line converted after its check is checked again.
- ASR runs faster-whisper with `vad_filter=True` (`min_silence_duration_ms` 500, `speech_pad_ms` 200) and `condition_on_previous_text=False`, so a silent or noisy tail does not produce hallucinated text ("to be continued…") that inflates CER; when the VAD hears no speech at all (a short quiet bark) the clip is transcribed once more without it. The audio is decoded in memory (soundfile + torchaudio), not through PyAV.
- Followers (§4.3) are not checked and do not count as pending; `qa --ids <follower>` is code 2 with `follows: {id: leader}` (first 10) and `n_follows`.
- `decisions.jsonl` (CLI): `{id, decision, attempt, by (auto|agent|human), note, attention, ts}`; `decision ∈ accept | regen | reref | shorten | retranslate | keep_original | skip`. Automatic decisions: pass → `accept`; `cer|sim|identity|peak|empty|silent` → `regen` while `attempt < tts.max_attempts`, then `keep_original` + attention; `duration` → `shorten` + attention (up to 2 times, then `keep_original`); `missing_file|no_dub_text|qc_error` → no decision, attention. Automatic QA does not overwrite agent/human decisions for the current attempt.
- `decisions.agent.jsonl` (agent): `{id, decision, attempt?, note?}` (no `by`/`ts`); `attempt` defaults to the latest attempt in `post`. `accept` applies to the latest processed attempt: `post` keeps only that one, and the build drops a decision for an old attempt as `stale_attempt`.
- `synth --attempt-next` makes a new attempt for `regen`/`reref` decisions whose `attempt` is ≥ the latest successful attempt; `reref` changes the voice prompt (`line` → `char`, `char` → another reference or `line`).

### 4.10 `build/build_manifest.json`, `build/install.json`
- manifest: `{_schema: "babeldub.build/1", created, build_id, game_fingerprint, target_lang, options{codec, sample_rate?}, containers: [...], models: [{name, revision, license, engine, lines}]}`.
  - `containers[]`: `{asset_id, adapter, relpath, src_sha1, out, out_sha1, replaced, kept, codec, lines, inputs_sha1, built, files: [{relpath, src_sha1 (null = new file), out, out_sha1}], replacements: {line_id: {wav, sha1, via?}}}`. `via` is the leader whose accepted post audio a follower (§4.3) received; the adapter converts it to the follower's own sample rate and channels, and `verify` compares the follower with that wav like any replacement. `relpath = ""` is an asset at the game root. `inputs_sha1` (source + wav sha1s + options) makes the build idempotent (`status: up_to_date`).
  - The packed audio of a line is the `wav` of its latest post row (the `vc` output after conversion), else `post/audio/<uid>.wav`. `models[]` also lists the RVC models behind accepted converted takes (`name: "rvc:<char_id>"`, `revision` = the conversion key, `engine: "applio"`).
  - `build_id` is the sha1 of the content; `verify` records it in `progress.verify.build_id`, and `install`/`reapply` require a match.
- install: `{installed: [{relpath, asset_id, orig_sha1, new_sha1, backup, created}], build_id, ts, state (installing|installed|uninstalled), restored?}`. `installing` is the write-ahead journal (a failure is rolled back); `created: true` marks a new file, which `uninstall` deletes.

### 4.11 `inventory/analysis.jsonl`, `inventory/analysis.npz`

Written by `analyze` (§5) for voiced lines with audio, followers excluded (`skipped.follows`); one row per line, the file is rewritten at the end of a run (rows are appended every 25 lines while it runs, so a re-run resumes). Header schema `babeldub.analysis/1`.
```json
{"id": "...", "uid": "1f0c3a9e7b21", "wav": "audio/orig/1f0c3a9e7b21.wav", "wav_size": 225834, "wav_mtime_ns": 0, "wav_sha1": "...",
 "models": "asr=faster-whisper-large-v3-turbo;emotion=none", "dur_s": 2.35,
 "vad_segments": [[0.08, 0.91], [1.22, 2.2]], "speech_s": 1.81,
 "asr_text": "Okay, let's see.", "asr_lang": "en", "asr_conf": 0.93, "asr_avg_logprob": -0.21, "asr_no_speech_prob": 0.01,
 "words": [{"w": "Okay,", "start": 0.1, "end": 0.42, "prob": 0.97}],
 "prosody": {"f0_median_hz": 132.5, "f0_range_st": 7.1, "rms_db": -21.4, "voiced_ratio": 0.62, "syl_rate": 4.4},
 "emotion": null, "asr_vs_sub_cer": 0.0, "class": "speech", "class_reason": "words", "subtitle_mismatch": false, "ts": "..."}
```
- **Measurements** (qc worker, §6): `vad_segments` are Silero VAD speech segments in seconds (a 250 ms pause splits them; 30 ms padding), `speech_s` their sum; `asr_*` and `words` come from faster-whisper with `word_timestamps=True` in the line's `text.src_lang` (else `source_lang`, else auto-detect), with QA's VAD filter and a retry without it; `asr_conf` is the mean word probability; `prosody` holds the F0 median and 10–90 % range in semitones (`librosa.pyin`, 60–700 Hz), the mean RMS of active frames (within 30 dB of the loudest) and the voiced frame ratio; `emotion` is `{label, score}` of emotion2vec+ only when its opt-in model is installed (else null), and its embedding goes to `analysis.npz` as `emotion_<uid>` (`analysis.store.emotion_vectors`).
- A row is reused while `wav_size`+`wav_mtime_ns` (else `wav_sha1`) and `models` are unchanged; `--force` measures again.
- **Derived on every run**, so a corrected subtitle or a re-run of `link` is picked up without the models: `prosody.syl_rate` = syllables of `text.src` (else of the ASR text) / `speech_s`; `asr_vs_sub_cer` = CER (`qa.metrics.cer`) of `asr_text` against `text.src` (null without a subtitle); `class`, `class_reason` (`analysis/classes.py:classify`); `subtitle_mismatch` = `class ∈ {speech, short_word, mixed}` and `asr_vs_sub_cer > 0.35`, mirrored as the line flag (§4.3).
- **Classes** `speech | short_word | interjection | nonverbal | mixed`: a subtitle made only of interjections (an English/Russian/Polish lexicon: "Hehe…", "Hmm", "Um…", "Ugh", "Oh!", "Ah…", "Хм", "Ох", "Ach"…) is `interjection`, or `nonverbal` when the file name says `nonverbal`; a subtitle that is only a stage direction (`[laughs]`) is `nonverbal`; words with a laugh/groan token, a stage direction or the `nonverbal` flag are `mixed`; other words are `short_word` when `speech_s` < 1.2 s, else `speech`. Without subtitle words the ASR decides: empty or `asr_conf` < 0.45 → `nonverbal`; interjections only → `interjection` (`nonverbal` with the flag); words → `mixed` with the flag, else `short_word`/`speech` by `speech_s`. `interjection` and `nonverbal` lines are meant to keep the original audio (`analysis.classes.KEEP_ORIGINAL_CLASSES`).

## 5. CLI commands

Common flags: `--project <dir|slug>` (otherwise `BABELDUB_PROJECT`, then the active project, then the current folder), `--pretty`. Subcommands (`cast apply-labels`, `cast select`, `decide apply`) accept `--project`/`--pretty` both before and after the action.

| Module | Command | What it does | Output (key fields) |
|---|---|---|---|
| `commands/env.py` | `doctor [--no-import-check]` | OS, Python, uv, ffmpeg/ffprobe, vgmstream-cli, rubberband, git, GPU (nvidia-smi: name, VRAM, driver), free space, which envs (with an import check) and models are present; recommends a tier; the full report is `BABELDUB_HOME/logs/doctor.json`. Code 4: no uv/ffmpeg/ffprobe; 3: envs/models needed | `tier`, `gpu`, `tools{}`, `envs{}`, `models[]`, `missing[]`, `optional_missing[]`, `to_install[]`, `install{}`, `next` |
| | `setup [--engines voxcpm,applio,qc] [--models auto\|none\|a,b] [--tools] [--tier] [--lang] [--accept-license M] [--force] [--dry-run] [--no-import-check]` | Without flags: the TTS engine (`tts.engine=auto`: `voxcpm` on a GPU with ≥ 7.5 GB when it speaks the language, else the first of `qwen3tts-fast`/`qwen3tts`/`chatterbox` for the language), `applio` on a GPU with ≥ 7.5 GB, qc, `auto` models for the tier and language, vgmstream (an explicit `--tier` skips the VRAM check). A `cuda_only` engine is replaced along its `fallback` chain on the `cpu` tier, and asked for explicitly there it is `blocked`. A git engine (`applio`) is fetched into `BABELDUB_HOME/engines/<name>` at its pinned commit (`git init`, `git fetch --depth 1 <url> <commit>`, `git checkout --force --detach FETCH_HEAD`), its `requirements` are installed without the torch lines (`@filter-requirements` → `req-babeldub.txt`), and its `post_install` commands run with the env python inside the checkout (`@cd`); the env marker records the commit, and a checkout at another commit is reinstalled. Creates a venv (`uv venv`, torch cu128/cu124/cpu), installs packages per the catalog, downloads weights (`hf download`) and tools (vgmstream from the GitHub release, sha256); "adopts" an existing working venv; `opt_in` models only with `--accept-license`. Plan: `logs/setup-plan.json` | `planned[]`, `done[]`, `skipped[]`, `failed[]`, `blocked[]`, `download_gb`, `free_gb` |
| | `models list [--engine E]` / `models fetch a,b [--accept-license M] [--force] [--dry-run]` | The `data/models.yaml` catalog with licenses and status (`--engine` follows `models_of`); downloading weights | `models[]` / `done[]`, `skipped[]` |
| `commands/project.py` | `init <game_dir> --lang ru [--cast all\|main\|list] [--chars A,B] [--mode auto\|translate\|adapt\|official] [--slug] [--name] [--project-dir]` | Creates the project (TTS engine by language), registers the game in `games.json`, makes it active; a repeat is idempotent; another game/language in the same slug is code 2; a language without an engine is code 3 | `project`, `slug`, `engine`, `next` |
| | `status` | Stages, counters, next step: `next_stage` is the first stage in `pending`, `running` or `failed`; stages in `attention`/`failed` go to `attention{stage: summary}` with code 3; `counts.follows` = voiced followers (§4.3), never synthesized; `counts.analyzed`, `counts.classes{class: n}` and `counts.subtitle_mismatch` come from `inventory/analysis.jsonl` (§4.11). When `progress.synth.bootstrap_wait` > 0 and `qa` is passed, `next_stage` is `synth` (bootstrap phase 2) with `bootstrap_wait` | `stages{}`, `counts{}`, `next_stage`, `next`, `attention{}`, `done`, `bootstrap_wait?` |
| | `set <section.key>=<value> ...` | Changes `project.json` settings; the type follows the current value; enumerations are validated (§4.1) | `changed{}`, `warnings[]` |
| | `query [--where k=v,k!=v,k~sub ...] [--fields a,b.c] [--limit 20] [--out file]` | Selects lines (merges lines + assign + characters + translations + qa + decisions). Computed fields: `char` (id or name), `has_text`, `translated`, `verdict`, `decision`, `voiced`, `follows` (leader id of a follower, else null), `class` (§4.11); QA metrics as `qa.*`, analysis fields as `analysis.*` (e.g. `analysis.speech_s`) | `rows[]`, `total`, `file?` |
| | `sample --scene X \| --char X \| --ids a,b [--n 30] [--out file.jsonl]` | Context for the agent: lines written to a file (default `logs/samples/<label>.jsonl`) | `file`, `n` |
| | `games` | Game registry | `games[]`, `active` |
| `commands/recon.py` | `scan [--force]` | Detects the engine and middleware (`data/engines.yaml`), calls `discover()` of every adapter (an adapter failure is a warning), writes `recon.json` (with `refs.recon`/`refs.pack`: paths of the skill references), `assets.jsonl`, `report.md` | `engine`, `middleware[]`, `text_systems[]`, `voice_assets[]`, `text_assets[]`, `counts{}`, `level`, `warnings[]`, `anticheat?` |
| | `extract [--kinds dialogue,cutscene,bark\|all] [--limit N] [--ids a,b] [--locales auto\|all\|en-US,...] [--force] [--dry-run]` | `list_lines()` + `extract_audio()` → `lines.jsonl`, `audio/orig/*.wav`; `read_texts()` → `texts.jsonl`. `lines.jsonl` holds only the selected lines (a run with `--limit N` makes a trial project; a run without a limit extends it); `auto` picks one voice locale (source_lang, then en, then the largest); existing WAVs are reused | `lines`, `by_kind{}`, `minutes`, `decoded`, `reused`, `failed`, `texts`, `locales[]`, `voice_locales[]` |
| | `link [--dry-run]` | Links lines to texts (key → normalized key → file name), fills `text`, `source_lang`, the `no_text`/`alt_take`/`duplicate` flags; code 3 when `unlinked > 0` | `linked`, `unlinked`, `with_official_target`, `by_match{}`, `src_lang`, `alt_takes` |
| `commands/analyze.py` | `analyze [--ids a,b] [--limit N] [--force] [--device] [--wait-lock S]` | Requires `link`. Measures the original of every voiced line with audio, whatever the cast selection (followers skipped), with the `qc` worker on the GPU: VAD segments, ASR with word times, prosody, emotion when the opt-in model is installed (no speaker model is loaded); derives the syllable rate, `asr_vs_sub_cer`, the line class and the `subtitle_mismatch` line flag (§4.11); writes `inventory/analysis.jsonl`/`analysis.npz` and touches no other field of `lines.jsonl`. Idempotent per wav and model set; holds `gpu.lock`. `--ids` (ids or uids) leaves the stage status alone; failed lines or lines left by `--limit` give code 3. Without the `qc` env: code 4 | `analyzed`, `up_to_date`, `failed`, `lines`, `classes{}`, `subtitle_mismatch`, `mismatch_ids[]`, `flags_changed`, `skipped{}`, `emotion`, `device`, `elapsed_s`, `remaining?`, `errors[]?`, `next` |
| `commands/cast.py` | `cast [--refs 1..3] [--min-ref-s 3] [--max-ref-s 12] [--threshold 0.35] [--device] [--wait-lock S] [--no-embed] [--force]` | Characters from `speaker_hint` (metadata), the rest as clusters of embeddings from the `qc` worker (if present; otherwise an `unknown` group with a warning); clusters that sound like a metadata character are merged into him (`cast.merge_threshold`, §4.5); tiers, `cast.policy`, choice of references, `casting_sheet.md`. Code 3: unidentified voices exist, or a selected character has no reference | `characters`, `selected`, `main[]`, `uncertain[]`, `no_ref[]`, `clusters` (left after merging), `merged`, `merged_into{char_id: [cluster_id]}`, `refs` |
| | `cast apply-labels` | Validates and merges `labels.agent.jsonl` (all or nothing) | `updated`, `labeled`, `uncertain[]` |
| | `cast select --chars A,B \| --policy all\|main [--device] [--wait-lock] [--no-embed]` | Changes the character selection, picks references for the new ones | `policy`, `selected[]`, `count`, `new_refs`, `no_ref[]` |
| `commands/translate.py` | `translate plan` | Resolves the mode (`auto` → `adapt`/`translate`, §4.1), computes coverage | `mode`, `with_official`, `official_share`, `need_agent`, `no_text`, `next` |
| | `translate use-official [--force]` | Copies the official translation into `translations.jsonl` (`by=official`); does not touch agent/human rows without `--force`; lines without official text go to `missing_official.txt`, code 3 | `copied`, `unchanged`, `missing`, `over_budget` |
| | `batch make [--size 60] [--ids a,b] [--from-decisions] [--force]` | Cuts batches by scene for untranslated lines (or for `adapt`); `--ids` retranslates, `--from-decisions` takes lines with a `retranslate`/`shorten` decision; `--force` recreates open batches | `batches[]`, `lines`, `open` |
| | `batch next` | The first open batch | `batch`, `state (new\|ready\|error)`, `in`, `out`, `errors_file?`; `batch: null, done: true` |
| | `batch submit <bid> [--force]` | Validates `.out.jsonl` (all ids, extra ids, empty `dub_text`, types, placeholders/tags) and merges it into `translations.jsonl`; errors give code 2 and `<bid>.errors.json`; exceeding the syllable budget gives code 3 | `merged`, `errors[]`, `over_budget[]`, `over_fixed[]`, `picked_variants` |
| | `glossary check` | A glossary term in the source without the agreed translation in `dub_text` is a violation (code 3, `glossary_violations.jsonl`) | `terms`, `checked`, `violations[]`, `total` |
| | `glossary apply` | Validates and merges `glossary.agent.jsonl` into `glossary.jsonl` | `added`, `updated`, `total` |
| `commands/voice.py` | `synth [--chars A,B] [--ids a,b] [--limit N] [--attempt-next] [--engine auto\|voxcpm\|qwen3tts-fast\|qwen3tts\|chatterbox\|dummy] [--device cuda\|cpu] [--wait-lock S] [--batch N] [--best-of N] [--force] [--dry-run]` | Resolves the engine (§4.1 `tts.engine`) and the effective `ref_strategy` (`auto` → `bootstrap` for `voxcpm`), prepares the voice prompts (§4.1), runs the engine worker in its venv (one model load per run, batches per character when the worker reports `info.batch`), generates `best_of` candidates per line and keeps the best (§4.1), writes `synth/raw` and `results.jsonl` after every line; holds `gpu.lock`. Notes say which engine was chosen and why, and every fallback. Idempotent: the same text with the same engine/model is not repeated (`qwen3tts-fast` and `qwen3tts` count as one engine: same models, same output per seed). Followers are skipped (`skipped.follows`); bootstrap lines that wait for their character's prompt are `skipped.bootstrap_wait` (recorded as `progress.synth.bootstrap_wait`; `next` asks for `post`, `vc`, `qa`, `synth`); runaways are discarded and retried (§4.7). The dry run reports `ref_strategy` and `best_of` and may write the bootstrap prompt caches | `done`, `failed`, `remaining`, `rtf`, `batch`, `best_of`, `vram_peak_gb`, `by_ref{}`, `skipped{}`, `attention[]`, `runaway[]?`, `runaway_recovered?`, `notes[]` |
| | `post [--ids] [--limit] [--force] [--dry-run] [--stretch-method rubberband\|ffmpeg-rubberband\|atempo]` | Never-slower fit (pauses first, then tempo up to `post.tempo_max`; `fixed_length` lines chunk by chunk on the original's segments), exact original length, loudness and format as in the original (§4.8); `post.tempo_min ≤ 1 ≤ post.tempo_max` within 0.5–2 or code 2 | `done`, `up_to_date`, `exact`, `not_exact[]?`, `over_length[]`, `stretch{}`, `next` (`babeldub vc`) |
| `commands/vc.py` | `vc train --chars A,B [--epochs 20] [--minutes 30] [--force] [--dry-run] [--device] [--wait-lock S]` | Per character: a dataset of its clean original lines (voiced kinds, no flags, 2–20 s, uid order, up to `--minutes`) copied to `vc/<char_id>/dataset/`; Applio (`vc/rvc.py`, subprocesses in `BABELDUB_HOME/engines/applio` with the `applio` env) `preprocess` → `extract` (rmvpe) → `train` (40 kHz, pretrained, batch 8, `--save-every-epoch` = epochs) → `index` in a fresh `logs/<name>/` (`name` = `bd_<slug>_<char>_<hash>`, ASCII); the highest-epoch `<name>_<E>e_<steps>s.pth` and the `.index` are copied to `vc/<char_id>/model.pth`/`model.index` with `model.json`, then the Applio training folder (~1.4 GB of checkpoints) is removed. An existing model is skipped without `--force`. Requires `extract`; needs CUDA (else code 2) and the env (else code 4); holds `gpu.lock`; logs `logs/vc-train-<name>.log` | `trained[]`, `skipped[]`, `no_data[]?`, `failed[]?`, `epochs`, `plan[]` (dry run) |
| | `vc [--chars A,B] [--ids a,b] [--index-rate X] [--protect X] [--force] [--dry-run] [--device] [--wait-lock S]` | Requires `post`. For each non-follower line whose character has a model: Applio `batch-infer` (`--index-rate`, `--protect`, `--f0-method`, `--proposed-pitch`) on the pre-conversion take (`pre_vc_wav` of a `vc` row, else the post row's `wav`), then `audio.fit.conform`: resample to the original rate, pad/trim to the post take's sample count and channels, the speech level back to the post row's `level_ref`, true peak ≤ `post.true_peak_db` → `vc/audio/<uid>.wav` and a `stage: "vc"` row appended to `post.jsonl` (§4.8). A line is up to date when its latest row's `vc.model_key` (model + settings) matches. Characters without a model pass through (`passthrough{}` and a note). Code 4 without the env only when there is something to convert; `CUDA_VISIBLE_DEVICES=""` on the cpu device | `converted`, `up_to_date`, `passthrough{}`, `failed`, `errors[]?`, `notes[]?`, `next` |
| `commands/qa.py` | `qa [--ids] [--limit] [--force] [--rebaseline] [--device] [--wait-lock S]` | The `qc` worker: ASR (CER against `dub_text`), voice similarity to the original and to the character reference relative to the character's baseline (cross-language ratio, absolute floor), the speaker identity margin among the characters' voice centroids, duration, peaks → `qa.jsonl`; `nonverbal` lines skip the voice and CER checks; automatic decisions → `decisions.jsonl` (§4.9). Without the `qc` env: code 4 | `checked`, `pass`, `fail`, `accept`, `regen`, `shorten`, `keep_original`, `attention[]`, `pending`, `cer_median`, `sim_median`, `device` |
| | `decide apply [--by agent\|human]` | Validates and merges `decisions.agent.jsonl` (all or nothing; repeats are skipped) | `applied`, `skipped`, `by_decision{}` |
| | `report` | `qa/report.md` with per-character totals and a "needs a decision" list | `file`, `lines`, `checked`, `pass`, `attention` |
| `commands/pack.py` | `build [--codec auto\|pcm16\|fadpcm] [--only ASSET_ID,...] [--sample-rate N] [--force] [--dry-run]` | Lines whose latest decision is `accept` are grouped by container, each with its followers (leader's audio, `via`); `adapter.build()` runs into staging and, on success, the result moves to `build/out/<relpath>`; manifest (§4.10). Unchanged inputs are `up_to_date`; a failure of one container does not affect the others | `containers[{asset_id, status, replaced, kept, codec}]`, `replaced`, `total_containers`, `build_id`, `followers?`, `failed?`, `attention?` |
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
→ {"op": "synth", "id": "...", "text": "...", "lang": "ru", "ref_wav": "...", "ref_text": "...", "ref_lang": "en", "out": "...wav", "seed": 1234, "params": {"x_vector_only": false, "max_new_tokens": 400},
   "prompt_wav": "...", "prompt_text": "..."}   (prompt_*: optional continuation prompt, bootstrap-tgt; both or neither)
← {"ok": true, "op": "synth", "id": "...", "wav": "...", "sr": 24000, "dur_s": 2.1, "rtf": 0.4, "gen_s": 0.8, "mode": "icl", "hit_token_limit": false, "vram_peak_gb": 4.3}
← {"ok": false, "op": "synth", "id": "...", "error": "CUDA out of memory"}
→ {"op": "synth_batch", "seed": 1234, "items": [<synth>, ...]}   ← {"ok": true, "results": [<synth reply>, ...]}
→ {"op": "stats"} / {"op": "ping"} / {"op": "quit"}
```

v0.1 engines: `voxcpm` (`tts_voxcpm.py`: VoxCPM2 through the `voxcpm` package, `VoxCPM.from_pretrained(model, load_denoiser=False, optimize=options.optimize (false), device)`; `ref_wav` → `reference_wav_path` (timbre, no transcript), `prompt_wav`/`prompt_text` → `prompt_wav_path`/`prompt_text`; `ref_text` and `x_vector_only` are ignored; `params`: `cfg_value` (2.0), `inference_timesteps` (10), `min_len`, `max_len`, `normalize`, `retry_badcase*`; the seed goes to `torch.manual_seed` and to `generate(seed=)` when supported; 48 kHz; `info.batch: false`, `synth_batch` is a loop; `mode` `ref|ref+prompt`), `qwen3tts-fast` (`tts_qwen3_fast.py`: the Qwen3-TTS models through `faster-qwen3-tts` with CUDA graphs; CUDA only, `load` refuses another device; one text per generate call, so `load` reports `info.batch: false` and `synth_batch` is a sequential loop; warm-up after load; `max_new_tokens` is kept within `options.max_seq_len` (2048) − 512, because the static cache would otherwise end a loop silently instead of reporting `hit_token_limit`), `qwen3tts` (`tts_qwen3.py`, official `qwen-tts`, ICL prompt ref_audio + ref_text, batched), `chatterbox` (`tts_chatterbox.py`), `dummy` (`tts_dummy.py`: a TEST engine without a neural network for CI and end-to-end tests; runs in the CLI interpreter; supports `synth_batch`; accepts `prompt_wav`/`prompt_text` like VoxCPM2, so `ref_strategy=bootstrap` can be tested). RVC (`applio`) is not a JSONL worker: `vc` and `vc train` run Applio's `core.py` as subprocesses (§5).

A `synth` reply may carry `hit_token_limit: true` (the output reached `0.97 · max_new_tokens / 12.5` s) and `mode` (`icl|xvec`). The `dummy` engine simulates a runaway for tests: the word `RUNAWAY` in `text` loops in every mode, in `ref_text` only in ICL mode, and `params.dummy_runaway_xvec` makes timbre-only requests loop too; a runaway answer is exactly as long as the cap (60 s without one) with `hit_token_limit: true`, and normal output is cut at the cap like a real engine.

QC worker (`workers/qc_worker.py`, env `qc`):
```text
→ {"op": "load", "asr_model": "<dir>", "sv_model": "<dir>", "sv_savedir": "<dir>", "mos_model": "<dir>", "emotion_model": "<dir>",
   "device": "cuda", "compute_type": null}   (every model is optional; asr/sv load at once, mos/emotion/VAD on first use)
← {"ok": true, "device": "cuda", "asr": true, "sv": false, "mos": true, "emotion": false, "info": {...}}
→ {"op": "asr", "id": "...", "wav": "...", "lang": "ru", "options": {"vad_filter": true}}   ← {"ok": true, "id": "...", "text": "...", "lang": "ru", "dur_s": 2.1}
→ {"op": "embed", "id": "...", "wav": "..."}                        ← {"ok": true, "id": "...", "emb": [192 floats], "dur_s": 2.1}
→ {"op": "sim", "id": "...", "wav": "...", "refs": ["..."]}         ← {"ok": true, "id": "...", "sim": 0.71, "sims": [...]}
→ {"op": "vad", "id", "wav", "options"?}     ← {"segments": [[0.08, 0.91], ...], "speech_s": 1.81, "dur_s": 2.35}
→ {"op": "asr_words", "id", "wav", "lang"?}  ← {"text", "lang", "lang_prob", "words": [{"w", "start", "end", "prob"}], "avg_logprob", "no_speech_prob", "dur_s"}
→ {"op": "prosody", "id", "wav"}             ← {"f0_median_hz", "f0_range_st", "rms_db", "voiced_ratio", "dur_s"}
→ {"op": "emotion", "id", "wav"}             ← {"label", "score", "emb": [768 floats]}      (opt-in emotion2vec+, needs funasr)
→ {"op": "mos", "id", "wav"}                 ← {"mos": 3.42}                                 (UTMOS22 strong)
→ {"op": "score", "id", "wav", "text", "lang", "orig_wav", "refs": [...], "want": ["cer", "sim", "emo", "mos"]}
   ← {"cer", "asr_text", "sim", "emo_cos", "mos", "unavailable"?: [...]}   (a metric without its model or input is null and listed)
```
The worker decodes a file once for consecutive ops on it (soundfile + torchaudio in memory, never PyAV). `score`'s CER uses the normalisation of `qa.metrics.cer`; `emo_cos` is the cosine of the emotion embeddings of `wav` and `orig_wav`. `QcSession.open(..., need_mos=, need_emotion=)` passes the optional models only when their folders exist (`qa.qc.mos_model_dir`, `emotion_model_dir`) and exposes `vad`, `asr_words`, `prosody`, `emotion`, `mos` and `score`.

The CLI-side client is `babeldub/workers/client.py`: `WorkerClient(env|python_path, script, timeout, load_timeout, log_path).load()/request()/close()` (timeouts kill the process; after a crash it restarts, repeats `load` and retries the request once; errors `WorkerError`, `WorkerTimeout`, `WorkerCrashed`; a missing env is `EXIT_ENV`). `GpuLock(project/state/locks/gpu.lock, owner, wait_s)` is an exclusive PID lock on the GPU; the lock of a dead process is released, a busy lock is `EXIT_USAGE` with a `--wait-lock` hint. The qc session for `cast`/`qa` is `qa/qc.py:QcSession`.

## 7. Audio API (`babeldub/audio`)

`io.py`: `read_audio(path) -> (np.ndarray float32 [frames, channels], sr)`, `write_wav(path, data, sr, subtype="PCM_16")` (atomic), `probe(path) -> {sr, ch, dur_s, frames, subtype, format}`.
`dsp.py`: `to_mono` (1-D), `to_channels(data, ch)`, `resample(data, sr_in, sr_out, quality=16)` (polyphase sinc with a Kaiser window, no external dependencies), `trim_silence(data, sr, threshold_db=-45, pad_s=0.04) -> (data, lead_s, trail_s)`, `voiced_bounds(data, sr) -> (start, end) | None`, `speech_seconds(data, sr)`, `loudness_lufs(data, sr)` (BS.1770-4, K-weighting, gating), `rms_db`, `level_db(data, sr, measure="auto")` (LUFS from 0.4 s, RMS for shorter), `peak_db(data)`, `true_peak_db(data, sr)`, `match_loudness(data, sr, target, ceiling_db=-1.0, measure=...)`, `fade(data, sr, ms)`, `pad_silence(data, sr, lead_s, trail_s)`.
`fit.py`: `speech_bounds(data, sr, threshold_db=-42) -> (start, end) | None` (20 ms frames within 42 dB of the loudest), `speech_chunks(data, sr, min_gap_s=0.12) -> [(start, end)]` (samples), `speech_segments_s` (the same in seconds), `speech_span(data, sr)`, `length_score(speech_s, target_s)` (|log ratio|), `align_chunks(dub, orig, max_merge=8) -> [((i0, i1), (j0, j1))]`, `fit_segments(dub, dub_sr, orig, orig_sr, tempo_max, tolerance, ceiling_db, stretcher, tempo_min=1.0, fixed_length=False, orig_segments=None) -> FitResult` (§4.8; `pause_cut_s`, `onset_deltas`, `chunk_tempos`), `fit_to_original(dub, dub_sr, orig, orig_sr, tempo_min, tempo_max, tolerance, ceiling_db, stretcher) -> FitResult` (the older bounded-tempo fit), `conform(audio, sr, ref, ref_sr, level_ref, measure, ceiling_db) -> (audio, peak_db)` (the `vc` pass).
`stretch.py`: `time_stretch(in_wav, out_wav, ratio, method=None) -> "rubberband" | "ffmpeg-rubberband" | "atempo" | "copy"` (ratio = output duration / input duration; the rubberband CLI with `-3 -D`, then ffmpeg's rubberband filter, then an `atempo` chain; nothing available is `EXIT_ENV`), `available_methods()`, `atempo_chain(tempo)`.

## 8. Text (`babeldub/text`)

`placeholders.py`: `extract_placeholders(text) -> list[str]` (`{0}`, `{name}`, `%s`, `%d`, `<tag>`, `[tag]`, `\n`, Unity Smart Strings `{Var:plural:...}`), `check_placeholders(src, tgt) -> [{severity: error|warn, kind: missing|extra, token, expected, found}]`, `has_errors(problems)`.
`syllables.py`: `count_syllables(text, lang)` (vowels for ru/uk/pl…; vowel groups for de/es/fr…; a heuristic for en; CJK by characters), `budget_syllables(dur_s, lang, rate=None) -> int | None` (syllables per second by language, or `rate`), `measured_rate(text, speech_s, lang)`, `character_rate(rates, lang, min_samples=3)` (median, clamped to 3–9; the language default with fewer samples), `segment_text(text, segments, lang) -> str | None` (' / ' at the original's pauses), `fit_status(est, budget, over=1.15, under=0.5) -> ok|over|under|None`.
`normalize.py`: `prepare_for_tts(text, lang, numbers=True, yo="keep"|"strip"|"restore", stress=False)`: removes markup (`strip_markup`, including speaker labels), spells numbers out (`numbers_to_words`, if `num2words` is available), handles `ё` (`apply_yo`), removes U+0301 stress marks (`strip_stress`) unless `stress=True` (only for stress models).

## 9. Model catalog (`runtime/src/babeldub/data/models.yaml`)

```yaml
engines:
  voxcpm:
    env: voxcpm
    torch: {version: "2.8.0", index: "https://download.pytorch.org/whl/cu128"}
    packages: ["voxcpm @ git+https://github.com/OpenBMB/VoxCPM.git", "soundfile", "numpy"]
    worker: tts_voxcpm.py
    languages: [ar, my, zh, da, nl, en, fi, fr, de, el, he, hi, id, it, ja, km, ko, lo, ms, "no", pl, pt, ru, es, sw, sv, tl, th, tr, vi]
    cuda_only: true
    fallback: qwen3tts-fast
  applio:
    env: applio
    role: vc                  # not a TTS engine: the vc stage
    torch: {version: "2.11.0", index: "https://download.pytorch.org/whl/cu128"}
    git: {url: "https://github.com/IAHispano/Applio", commit: "5171e1dcf31a33d1afeb14ef7c6aac6dadae5dc1"}
    requirements: requirements-engine.txt          # installed without its torch/torchaudio lines
    post_install: [[core.py, prerequisites, --pretraineds-hifigan, --models, --no-exe]]
    cuda_only: true
  qwen3tts-fast:
    env: qwen3tts-fast
    torch: {version: "2.8.0", index: "https://download.pytorch.org/whl/cu128"}
    packages: ["faster-qwen3-tts==0.5.4", "transformers==5.15.1", "soundfile", "numpy"]
    worker: tts_qwen3_fast.py
    languages: [zh, en, ja, ko, de, fr, ru, pt, es, it]
    models_of: qwen3tts       # runs the qwen3tts models
    cuda_only: true           # never installed on the cpu tier, never run without CUDA
    fallback: qwen3tts        # what setup/synth use instead
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
- Engines: `voxcpm` (VoxCPM2, the default on GPUs with ≥ 7.5 GB; model `VoxCPM2`, 4.7 GB, peak VRAM 6.5 GB), `applio` (RVC v2, stage `vc`; a git engine with `role: vc`), `qwen3tts-fast` (Qwen3-TTS on smaller GPUs; `transformers==5.15.1` is pinned because 5.20.x fails with `'MimiConfig' object has no attribute 'rope_theta'`), `qwen3tts`, `chatterbox`, `qc` (faster-whisper + SpeechBrain ECAPA + Silero VAD + librosa pyin + UTMOS22), `dummy` (`env: null`, for tests). Engine fields beyond §9's list: `role` (`tts|qc|vc`; models also `asr|sv|mos|emotion`), `git {url, commit}`, `requirements`, `post_install` (§5 `setup`). API: `babeldub.envsetup.catalog` (`load_catalog`, `default_model`, `model_dir`, `is_model_downloaded`, `engine_for_language` (preference `qwen3tts-fast`, `qwen3tts`, `chatterbox`), `setup_tts_engine` (the TTS engine `setup` installs, `tts.engine=auto` → `voxcpm` on a big GPU), `big_gpu(tier, vram_gb)` (≥ 7.5 GB), `runnable_engine` (the first engine of the `fallback` chain that is not CUDA only on the `cpu` tier), `Catalog.models_for_engine` (follows `models_of`), `tier_for_vram`); `EngineSpec.source_dir()` = `BABELDUB_HOME/engines/<name>` for git engines.
- Models with `commercial: false` get `opt_in` and are installed only with `--accept-license`.
- A model with `url` (and `sha256`) is one weight file downloaded directly into its folder instead of a Hugging Face repo (`utmos22-strong`: the SpeechMOS release asset of the MIT UTMOS22 strong learner; a wrong sha256 fails the download). The `qc` env also carries `librosa` (pyin) and `speechmos` (pinned commit). `emotion2vec_plus_base` (FunASR Model License 1.1: non-standard, revocable) is `opt_in` and additionally needs `funasr` in the env.
- By default `synth` takes the downloaded general (not language-specific) model of the engine, the larger one first; language fine-tunes (`qwen3-tts-12hz-1.7b-ru-stress-cf`) are used only when set explicitly in `tts.model`: on the test lines they preserved the original actor's voice worse.
- Human-readable overview of models, tiers, licenses and measured VRAM: `docs/MODELS.md`.

## 10. Tests and code quality

- `pytest` in `runtime/tests/<area>/`; synthetic data only (generated FSB5/banks/texts). No game assets in the repository.
- `runtime/tests/e2e/test_pipeline_synthetic.py` runs every stage through `cli.main` on a synthetic Unity game with an FMOD bank (the `dummy` engine with best of 2, `adapt` translation, `vc` pass-through, a fake qc worker, installation into a copy of the game and rollback); it also checks the honest `EXIT_ENV` of `qa` without the `qc` env. The VoxCPM2 worker is tested with fake `torch`/`voxcpm` modules (`tests/voice/test_worker_voxcpm.py`), bootstrap and best-of on the dummy engine (`tests/voice/test_voice_bootstrap.py`), `vc train`/`vc` with a fake Applio `core.py` (`tests/vc/test_vc.py`).
- Python ≥ 3.10, type annotations, `ruff` (config in `pyproject.toml`).
- WAV/FLAC through `soundfile`; paths through `pathlib`; UTF-8 everywhere; atomic writes through `core.jsonl`.
- Every command: a handler `def cmd_x(args) -> int` that ends with `return emit({...}, code)`; stage progress through `project.mark_stage(stage, status, **summary)`.
