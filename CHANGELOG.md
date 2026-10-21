# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).
Change the version only with `scripts/bump_version.py`; the heading of a release section must match the tag without the `v`.

## [Unreleased]

## [0.1.0] - 2026-10-21

First public release: the full pipeline for localizing and dubbing a game in the voices of its original characters, driven by an AI agent.

### Added

- **Dubbing in the original character's voice.** The voice prompt for each line is the original line itself with its source text (`tts.ref_strategy=line`), so the timbre, emotion and delivery of that exact phrase carry over. Short, noisy and untranscribed lines use the character's best reference (`char`); by default the strategy is chosen automatically (`auto`).
- **Skills following the Agent Skills standard:** `babeldub-localize` (orchestrator), `babeldub-doctor`, `babeldub-recon`, `babeldub-cast`, `babeldub-translate`, `babeldub-voice`, `babeldub-qa`, `babeldub-pack`. The same files work in Claude Code, Codex, Gemini CLI, Antigravity, Cursor, Copilot, OpenCode and when uploaded to claude.ai.
- **Claude Code subagents** for the pipeline roles (recon, casting, glossary, translation, voice, QA, packing). They are generated from `skills/babeldub-localize/references/roles/`; on other hosts the same roles run in the main session.
- **Claude Code hooks:** protection of a registered game folder against writes that bypass `babeldub install`, and a summary of the active project at session start.
- **The `babeldub` CLI** with one contract (one JSON object on output, exit codes 0–4): environment (`doctor`, `setup`, `models`), project (`init`, `status`, `set`, `query`, `sample`, `games`), game analysis (`scan`, `extract`, `link`), characters (`cast`), translation (`translate`, `batch`, `glossary`), voice (`synth`, `post`), quality control (`qa`, `decide`, `report`), build and install (`build`, `verify`, `install`, `uninstall`, `reapply`).
- **GPU-first compute:** when an NVIDIA GPU is present, all neural compute (TTS, ASR, speaker embeddings, clustering) runs on it (`compute.device=auto`), and `setup` installs CUDA builds of torch whenever a GPU is visible.
- **Format adapters:** FMOD Studio (`.bank`/FSB5), Unity Localization tables, Wwise, loose audio files. `babeldub scan` shows the support level (A/B/C) for a particular game.
- **Alternative takes and duplicates are dubbed once.** Lines of one container that share a subtitle key and are flagged `alt_take`/`duplicate` follow a leader (the take named like the key, else an unflagged one, else `_BEST`, else the first): only the leader is synthesized, post-processed and checked, and the build gives every follower the leader's accepted dub (`via` in the build manifest). Alt takes often do not match their subtitle, and on Low-Budget Repairs an ICL prompt with such a take looped for 55.9 s on a 3.8 s line.
- **Runaway guard for voice cloning.** A per-line generation cap from the estimated dub length and the original (`max_new_tokens = ceil(12.5 · max(6, 2 · max(est_s, target_s) + 2.5))`); a take that hits the cap or runs far past a plausible length is discarded and the line is retried alone, timbre-only on the same prompt and then with the character reference, so one loop no longer drags a whole batch to the cap. An ICL pre-check sends lines whose transcript cannot fit their audio (outside 1.2–8.5 syllables per second) to the character reference or timbre-only mode. Batches group lines of similar estimated length. `synth/results.jsonl` records `ref_mode` (`line|line-xvec|char|char-xvec`), `ref_reason`, `retry_reason` and the `discarded` takes.
- **Quality control:** back-transcription of the dub (CER against the translation; faster-whisper with a voice-activity filter and without conditioning on previous text, so silent or noisy tails do not produce hallucinated text), similarity of the synthesized voice to the character's original, duration and peaks; automatic `regen`/`reref`/`shorten` decisions by thresholds.
- **Install with rollback:** backup of the originals with sha1 checks, atomic replacement, `uninstall`, and `reapply` after a game update.
- **Data location:** environments, models, tools and projects live in `BABELDUB_HOME`, by default `~/.babeldub` (`C:\Users\<user>\.babeldub` on Windows); `doctor` reports data left in older default locations.
- **Packaging for hosts:** Claude Code marketplace and plugin, `.codex-plugin`, Agent Plugins 1.0 (`plugin.json`), Gemini CLI extension (`gemini-extension.json`, `GEMINI.md`).
- **Releases:** `babeldub-plugin`, `babeldub-skills` (one zip per skill for claude.ai) and `babeldub-kit` (a workspace folder next to the game), `SHA256SUMS`.
- **Helper scripts:** `bump_version.py`, `validate_skills.py`, `build_release.py`, `install.ps1`, `install.sh`; CI on Ubuntu and Windows.
- **Documentation (English):** install per host (`docs/HOSTS.md`), engine support (`docs/ENGINES.md`), models and hardware tiers (`docs/MODELS.md`), responsible use (`docs/LEGAL.md`), the CLI contract (`docs/dev/CONTRACT.md`); a Russian README (`README.ru.md`).

[Unreleased]: https://github.com/KROU4/BabelDub/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/KROU4/BabelDub/releases/tag/v0.1.0
