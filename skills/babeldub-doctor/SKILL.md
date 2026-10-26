---
name: babeldub-doctor
description: "Check and prepare the machine for BabelDub, which localizes and re-voices any video game in the original characters' voices. Installs or updates the babeldub CLI with uv, checks the NVIDIA GPU, VRAM, ffmpeg, vgmstream and disk space, then sets up the local GPU voice-cloning TTS engines, models and tools. Use on first run, when the babeldub command is missing or has the wrong version, when a babeldub command exits with code 4, or when the user asks to check the setup. Example triggers: 'set up BabelDub', 'check my environment', 'ffmpeg is missing', 'localize this game', 'dub the voices', 'проверь окружение', 'установи BabelDub', 'переведи игру', 'сделай русскую озвучку', 'doblar el juego'."
license: MIT
compatibility: "Local agent with shell access; Windows 10/11 or Linux; Python 3.10+ and uv; network access to PyPI, GitHub and Hugging Face for setup; NVIDIA GPU with 8+ GB VRAM recommended."
metadata:
  babeldub-cli: "0.3.0"
---

# BabelDub: environment and installation

This skill prepares the machine to dub a game in the voices of its original characters. It installs the `babeldub` CLI at the right version, checks the hardware and utilities, and installs the synthesis engines, models and tools. It asks the user no questions: it prints the install plan, and installs models with permissive licenses (Apache-2.0, MIT) without asking.

Talk to the user in the user's language. Keep commands, flags, paths and JSON field names as they are.

Apply this skill:

- on the first BabelDub run;
- when the `babeldub` command is missing or its version differs from `metadata.babeldub-cli` (`0.1.0`);
- when a `babeldub` command returns exit code 4 (or `doctor` returns code 3 with a non-empty `todo[]`);
- when the user asks to check the environment.

## 1. CLI at the right version

1. Run `babeldub --version`. Expect `babeldub 0.1.0`. If it matches, go to step 2.
2. Otherwise run `scripts/bootstrap.py` from this skill's folder (in Claude Code the skill folder is `${CLAUDE_SKILL_DIR}`):
   - in Claude Code: `python "${CLAUDE_PLUGIN_ROOT}/skills/babeldub-doctor/scripts/bootstrap.py" --plugin-root "${CLAUDE_PLUGIN_ROOT}"`;
   - on other hosts: `python <skill folder>/scripts/bootstrap.py` or `uv run --no-project --script <skill folder>/scripts/bootstrap.py`.

   The script installs the CLI with `uv tool install` from the `runtime/` folder of the plugin or repository; if there is none, from the release git tag `v0.1.0`. It prints one JSON object:

   | Field | Meaning |
   |---|---|
   | `action` | `none`, `installed`, `reinstalled`, `planned` |
   | `installed`, `expected` | installed and expected versions |
   | `exe`, `on_path` | path to `babeldub` and whether it is on `PATH` |
   | `source`, `source_kind` | where it was installed from: `plugin`, `repo`, `git`, `explicit`, `env` |
   | `cli_error` | tail of the error if the CLI does not start |
   | `hint`, `path_hint` | what to do about a problem |

   Flags: `--check` (check only), `--force` (reinstall), `--dry-run` (show the command), `--source <runtime folder or git+https requirement>`.

3. `bootstrap.py` exit codes:
   - 0: done;
   - 4 without uv: install uv with the official command from `hint` (Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` or `winget install astral-sh.uv`; Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`), open a new terminal and retry;
   - 4 on an install error: read `detail` and `hint` (network, no git for a git source, wrong `--source`);
   - 1: the package is installed but `babeldub --version` fails. Show `cli_error` in the report and stop.
4. If `on_path` is false: call the CLI by the full path from `exe` for the rest of the session, or run `uv tool update-shell` and open a new terminal.

## 2. Diagnostics

```text
babeldub doctor
```

Checks the OS, Python, utilities, the GPU (name, VRAM, driver via `nvidia-smi`), free disk space, engine venvs and downloaded models; recommends a tier. The full report is in the file named by the `report` field.

| Output field | Meaning | What to do |
|---|---|---|
| `missing[]` (code 4) | a required utility is missing: `uv`, `ffmpeg`, `ffprobe` | install commands are in `install{}` (Windows: `winget install Gyan.FFmpeg`; Linux: `sudo apt install ffmpeg`); then open a new terminal and run `doctor` again |
| `todo[]` (code 3) | an engine venv (`env:<name>`) or a model (`model:<name>`) is missing | step 3 |
| `optional_missing[]` | an optional utility is missing: `vgmstream-cli`, `rubberband`, `uvx`, `git` | `vgmstream-cli` is needed to extract most formats: `babeldub setup --tools`. `rubberband` (GPL) is installed by the user if they want it; without it the CLI stretches audio with `ffmpeg atempo` |
| `gpu`, `tier` | the graphics card and the recommended tier | with an NVIDIA GPU the CLI runs all neural compute on it automatically (`compute.device=auto`). Without an NVIDIA GPU synthesis runs on the CPU and takes hours: continue, but warn the user |
| `warnings[]` | low disk space, low VRAM, a broken venv, data in an older default location | follow the warning text (for example, `babeldub setup --engines <name> --force` for a broken venv) |

What is installed by default (CLI v0.1 catalog; the current list is `babeldub models list`):

| Purpose | Models | License |
|---|---|---|
| synthesis, NVIDIA GPU with 8 GB+ (venv `voxcpm`) | `VoxCPM2` (30 languages, 48 kHz, peak VRAM 6.5 GB) | Apache-2.0 |
| timbre transfer, NVIDIA GPU with 8 GB+ (venv `applio`, checkout `BABELDUB_HOME/engines/applio` at a pinned commit) | Applio (RVC v2) with its pretrained HiFi-GAN, rmvpe and contentvec files; the per-character models are trained later by `babeldub vc train` | MIT |
| synthesis, smaller GPUs (venv `qwen3tts-fast`) | `Qwen3-TTS-12Hz-1.7B-Base`; `qwen3-tts-12hz-1.7b-ru-stress-cf` (more accurate Russian stress, but a worse match to the character's voice) only via `babeldub models fetch` when the user decides so | Apache-2.0 (part of the ru-stress-cf training texts has no stated license: a catalog warning) |
| synthesis, CPU tier (venv `qwen3tts`) | `Qwen3-TTS-12Hz-0.6B-Base` | Apache-2.0 |
| synthesis without VoxCPM2 for languages outside Qwen3-TTS (for example pl, tr) | `chatterbox-multilingual` (every file carries the inaudible PerTh watermark) | MIT |
| QA and casting (venv `qc`) | `faster-whisper-large-v3-turbo` (speech recognition), `spkrec-ecapa-voxceleb` (speaker similarity) | MIT, Apache-2.0 |

VoxCPM2 speaks ar, my, zh, da, nl, en, fi, fr, de, el, he, hi, id, it, ja, km, ko, lo, ms, no, pl, pt, ru, es, sw, sv, tl, th, tr, vi; Qwen3-TTS zh, en, ja, ko, de, fr, ru, pt, es, it. The `applio` venv is large (~8 GB with its CUDA libraries).

## 3. Engines, models, utilities

1. Show the plan: `babeldub setup --dry-run`. Output: `planned[]` (what gets installed, size, license), `download_gb`, `blocked[]`, `plan` (a file with details).
2. Licenses: `babeldub models list` (outputs `models[]` with licenses and status).
   - By default only models with permissive licenses (Apache-2.0, MIT) are installed.
   - The CLI blocks non-commercial (NC) models (`blocked[]`) until explicit consent. Install one only after the user asked for that model themselves and confirmed its license; then run `babeldub setup --accept-license <model>`. Without such a request, leave NC models alone.
   - Copy catalog warnings (a model's `warning`, for example about the origin of its training data) into the report.
3. Install: `babeldub setup` (engines, models and utilities for the recommended tier and the project language), or explicitly `babeldub setup --engines voxcpm,applio,qc --tools --models auto` (`voxcpm`, `applio` and `qwen3tts-fast` are CUDA only; on the `cpu` tier use `qwen3tts`). `applio` needs `git`. A single model: `babeldub models fetch <name>`.
4. Output: `done[]`, `skipped[]`, `failed[]`, `blocked[]`.
   - Code 1 (`failed[]`): read the log of the failed step (its path is in the output or in the plan file), fix the cause, rerun `setup` once. Network failures usually pass on the second try.
   - Code 3 (`blocked[]`): the reason is in the plan file (`reason`): no uv or git, a non-commercial license, no `uvx`/`huggingface_hub`.
   - Code 4 "not enough disk space": free up space or move `BABELDUB_HOME` (section 4).
5. Run `babeldub doctor` again: code 0, `missing[]` and `todo[]` are empty.

Installation is long: venvs with torch and model weights take gigabytes (the CLI warns when less than ~20 GB is free). Before starting, tell the user in one line what is being installed and roughly how much it is (`download_gb` from the plan). On hosts with background commands, run `setup` in the background.

## 4. Where the data lives

- `BABELDUB_HOME`: `~/.babeldub` on every OS (Windows: `C:\Users\<user>\.babeldub`); override it with the `BABELDUB_HOME` environment variable. The default is deliberately not `%LOCALAPPDATA%`: packaged hosts such as the Claude desktop app virtualise that folder, so other terminals and agent hosts would not see the downloaded models.
- Inside: `envs/<engine>` (engine venvs), `models/<name>` (weights), `hf-cache/`, `tools/` (vgmstream and others), `projects/<slug>` (projects), `games.json` (registry of game folders).
- If `doctor` warns that it found BabelDub data in an older default location, follow the warning: move its `envs/`, `models/`, `tools/` and `projects/` into the current `BABELDUB_HOME`, or set `BABELDUB_HOME` to the old folder.
- Low on space on the system drive: set `BABELDUB_HOME` to another drive **before** `setup` and `init`, and use a short path (Windows `MAX_PATH`).
- Nothing goes into the game folder or the plugin folder: the plugin is replaced on update.

## 5. Result

Return a short summary to the orchestrator (or to the user, if the skill was called directly):

```json
{"cli": "0.1.0", "exe": "...", "tier": "8gb", "gpu": "NVIDIA GeForce RTX 3060 Ti 8 GB", "installed": ["voxcpm", "applio", "qc", "vgmstream-cli"], "missing": [], "todo": [], "warnings": []}
```

The values in the example are illustrative: take them from the output of `bootstrap.py`, `babeldub doctor` and `babeldub setup`.

## Rules

1. Do not run game executables and do not write anything into game folders.
2. Do not install non-commercial models without the user's explicit request and consent.
3. Do not download GPL utilities (Rubber Band) into BabelDub: the user installs them if they want.
4. Do not change system settings or drivers. Move `BABELDUB_HOME` only if the user asked for it or the default location clearly lacks disk space; state the move and the new path in the report.
