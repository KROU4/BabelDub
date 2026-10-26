# Troubleshooting

Read this file on exit codes 1 and 4, GPU out of memory, odd QA metrics and installation problems. Every section is: symptom → cause → action. If the symptom is not listed, look at the latest stage log in the project's `logs/` (by section, not whole) and at the `where` field of the JSON.

## 1. CLI and environment

| Symptom | Cause | Action |
|---|---|---|
| `babeldub: command not found` / "is not recognized as a command" | the CLI is not installed or uv's tool directory is not on `PATH` | `babeldub-doctor` skill: `skills/babeldub-doctor/scripts/bootstrap.py`; if `on_path` = false, call the CLI by the full path from the `exe` field, or run `uv tool update-shell` and open a new terminal |
| `babeldub --version` ≠ `metadata.babeldub-cli` | an old or foreign build is installed | `bootstrap.py --force` |
| `doctor` code 3, `todo[]` not empty; `synth`/`qa`/`cast` code 4 | engine venv or weights missing | `babeldub setup --dry-run` → `babeldub setup` |
| `doctor` code 4, `missing[]`: `ffmpeg`, `ffprobe`, `uv` | the user installs the required tools | commands are in the `install{}` field of the `doctor` output (Windows: `winget install Gyan.FFmpeg`; Linux: `sudo apt install ffmpeg`); then a new terminal and `babeldub doctor` |
| `extract` code 4, no `vgmstream-cli` | the decoder is not downloaded | `babeldub setup --tools` |
| no `rubberband` (`optional_missing[]`) | the tool is optional (GPL license, not shipped) | the CLI stretches audio with `ffmpeg atempo`; stretching quality is slightly lower, this is not an error. It can be installed: Windows `winget install BreakfastQuay.RubberBand`, Linux `sudo apt install rubberband-cli` |
| `setup` code 3, `blocked[]` | no uv/git, a non-commercial license without consent, no `uvx` and `huggingface_hub` | the reason is in the plan file (`plan`); do not enable NC models without the user's explicit request |
| `setup` code 4 "not enough disk space" | not enough space for weights and venvs | free some space or move `BABELDUB_HOME` before installing |
| `doctor` warns "found BabelDub data from an older default location" | data left in a pre-release default (`%LOCALAPPDATA%\BabelDub`, the XDG data dir, or the Claude desktop app's virtualised `LocalCache`) | move its `envs/`, `models/`, `tools/` and `projects/` into the current `BABELDUB_HOME` (default `~/.babeldub`, `C:\Users\<user>\.babeldub` on Windows), or set `BABELDUB_HOME` to the old folder to reuse it |
| `doctor` does not see the GPU | no NVIDIA driver or `nvidia-smi` | work is possible on CPU, but synthesis takes hours; warn the user in the report |
| not enough disk space | tier-8 GB models take ~7–8 GB, each venv ~3 GB (estimate, not verified), decoded originals take space too (for Low-Budget Repairs ~2.3 h of audio gave ~1.1 GB of WAV) | point `BABELDUB_HOME` to a disk with free space before `setup` and `init`; extra synthesis attempts in `synth/raw` may be deleted only after the project is finished |
| path errors on Windows (path too long) | `MAX_PATH` | a short `BABELDUB_HOME`, e.g. `D:\BabelDub` |

`BABELDUB_HOME` defaults to `~/.babeldub` (`C:\Users\<user>\.babeldub` on Windows); set the `BABELDUB_HOME` environment variable to move it.

## 2. GPU and synthesis

| Symptom | Cause | Action |
|---|---|---|
| warning "GPU is available but the worker runs on cpu" | the engine venv has a CPU-only torch build | `babeldub setup --engines <engine> --force` reinstalls the CUDA wheels; then rerun the stage. Do not accept a silent CPU run while a GPU is present |
| `compute.device=cuda but no NVIDIA GPU is visible` (code 2) | `compute.device` is forced to `cuda`, but the driver does not report a GPU | check the driver with `nvidia-smi`, or `babeldub set compute.device=auto` |
| `CUDA out of memory` | on 8 GB the models fit one at a time (VoxCPM2 peaks at 6.5 GB); the Windows desktop takes part of the memory | do not run `synth`, `vc` and `qa` at the same time; close other GPU applications; retry with a smaller chunk (`--limit`); if that does not help — a lighter engine: `babeldub setup --engines qwen3tts-fast`, `babeldub set tts.engine=qwen3tts-fast` (or `tts.model=Qwen3-TTS-12Hz-0.6B-Base` with it) and a note in the report |
| `vc train` fails or `vc` writes no output | the `applio` env or checkout is broken, or the dataset is too small | read the `log`/`tail` of the output; `babeldub setup --engines applio --force`; a character needs a few minutes of clean lines of 2–20 s |
| GPU busy (`gpu.lock`) | another GPU stage is still running | wait for it to finish (`babeldub status`); do not start a second GPU process |
| the worker fails on individual lines (`failed` > 0) | rare text, empty line, broken reference | rerun `synth` for them; if the same error repeats — decision `skip` or `keep_original` through the qa role |
| very slow (`rtf` above 1) | CPU mode or a weak GPU | synthesize in chunks by character; main characters first; warn about the time |
| the voice of the "wrong" character | wrong speaker attribution in `cast` | the caster checks the character (`query --where char=<id>`) and fixes the labels; then `reref` on the affected lines |

## 3. Dub quality

| Symptom in the metrics | Likely cause | Action |
|---|---|---|
| low `qa.sim` relative to `qa.sim_ref` on many lines of a character (reason `sim`) | noisy or emotionally extreme originals used as the prompt (`line`); with VoxCPM2, shouts or whispers voiced from the calm long reference | qa role: `reref` (prompt — the character reference); if the defect is systemic — voice role: with VoxCPM2 `babeldub set tts.tags_reference=bucket` (a long reference per arousal bucket), with other engines `babeldub set tts.ref_strategy=char` while synthesizing this character (`synth --chars <A>`), then set `auto` back |
| the voice changes gender or age | a reference with someone else's voice, a cluster mixed characters | the caster checks the references and labels; then `reref` |
| high `qa.cer` (unintelligible, omissions, "hallucinations"; see `qa.asr_text`) | text too long or complex, rare words, Latin script, numbers written as digits; with VoxCPM2 also a delivery control text that garbles words | `regen` (another seed, the CLI sets it itself; with `tags` it is voiced without the delivery tags); if it repeats — `retranslate` with simplification, numbers as words, Latin script per `pronounce` in the glossary. If a character's tagged lines keep failing `cer` while its untagged ones pass — voice role: `babeldub set tts.delivery_tags=off` |
| the delivery does not fit the scene: many `mos`/`flat` failures of tagged lines, or tags on lines that are plainly neutral | a wrong measurement (too few lines of the character, uniformly processed audio) or translators that set `delivery` on too many lines | look at `babeldub query --where char=<id> --fields id,text.src,translation.delivery --limit 50`; translators overdo it → `babeldub set tts.delivery_tags=measured`; the measurement is wrong on some lines → `babeldub batch make --ids <id,...>` and the translator sets `delivery` for them; on most lines → `tts.delivery_tags=off`; record the reason |
| no delivery tags at all (`counts.delivery` of `status` shows only `none`) | `analyze` did not run, the characters have fewer than 5 comparable lines each, or `tts.delivery_tags=off` | run `babeldub analyze`; small casts get no measured tags by design — the long reference alone carries the delivery |
| `qa.ratio` above `qa.max_duration_ratio` (reason `duration`), `over_length[]` from `post` | the target-language text is longer than the original (Russian is usually 10–20 % longer) | the CLI sets `shorten` → `babeldub batch make --from-decisions` → the translator shortens |
| reasons `qc_error`, `missing_file`, `no_dub_text` | QA worker failure, no file after `post`, no translation | `babeldub qa --ids <ids>`; `babeldub post --ids <ids>`; `retranslate` |
| an accent of the original language is audible (high `cer` on all of a character's lines with normal `sim` and a clean text) | cross-lingual cloning carries over the accent of the reference's language | with VoxCPM2: `babeldub set tts.ref_strategy=bootstrap` (target-language prompts from the character's accepted dubs, no delivery tags) and a note in the report; otherwise a known risk: record it in the report; if the game has several voice-over languages, note it as an idea for the next run |
| the "wrong" intonation on short lines | for short lines the prompt is the character reference, not the line itself | normal for `char`; if the line is ≥ 1.6 s and has text, check that `ref_strategy` is not pinned to `char` |
| peaks, clipping (`peak_db` above the ceiling) | loud original, a shout | rerun `post`; if it repeats — `regen` |

The agent cannot hear audio: decisions are made from metrics only. Do not put a subjective "sounds bad" without metrics into the report.

## 4. Recon and linking

| Symptom | Cause | Action |
|---|---|---|
| `scan` found no voice containers | engine or format not supported in v0.1 | `babeldub-recon` skill, the engine reference; report to the user with the manual path |
| `level` C or B | the format can be read but not built without an external tool | do not try to build by hand; describe the path from the reference |
| encrypted containers (FMOD bank key, AES on paks, HCA) | encryption | v0.1 has no automatic path; record it in the report |
| many `unlinked` after `link` | subtitle keys do not match audio names | the scout looks at a sample, looks for a pattern (prefixes, subfolders, case) and writes a hypothesis into the report; lines without text keep the original |
| SFX, music, video in a dialogue container | a shared bank or cutscene video audio | they get `kind` `sfx`/`music`/`unknown` and are not voiced; video is out of scope in v0.1 |
| anti-cheat or online warning | EAC, BattlEye, VAC | stop, tell the user, install nothing |

## 5. Build and install

| Symptom | Cause | Action |
|---|---|---|
| `build` code 2 "native rebuild is not supported" | the adapter is at level B or C | the path from the `babeldub-recon` reference; write nothing to the game folder |
| `verify` found `problems[]` | entry count mismatch, codec error | do not install; rebuild (`build --force`); if it repeats — report |
| `install` cannot replace a file | the game is running or the file is locked | close the game and its launcher, retry `install` |
| no sound in the game, or silence instead of lines | the game did not accept the codec of the rebuilt container (not checked in the running game) | `babeldub uninstall`; `babeldub build --codec pcm16` → `verify` → `install`; report the result |
| lines are cut off in the game | a sync tool, a cutscene timeline or a mode that cuts the audio | shorten such lines (`shorten`) to the original length; mark them in the report |
| the dub disappeared after an update or a Steam integrity check | the client restored the original files | `babeldub reapply`; if `changed[]` is not empty — `build` → `verify` → `install` |
| everything must go back to how it was | — | `babeldub uninstall` (sha1 check against the backup) |
