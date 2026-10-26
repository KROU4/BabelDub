---
name: babeldub-pack
description: "Put a finished dub back into the game as native files: rebuild engine containers (FMOD banks, loose audio and others) with the accepted lines in the original characters' voices, verify them before install, install with backup and sha1 checks, roll back and reapply after game updates or Steam integrity checks. Use for build, verify, install, uninstall, reapply and repack questions, e.g. 'install the dub', 'restore the original voices', 'rebuild after the game update', 'собери банк', 'установи озвучку', 'откати озвучку', 'верни оригинал', 'пересобери после обновления', 'instala el doblaje', 'restaura las voces originales'."
license: MIT
compatibility: "Local agent with shell access to the game folder; babeldub CLI 0.1.0 (install via the babeldub-doctor skill); vgmstream-cli and ffmpeg for some formats. No GPU needed. Native rebuild in v0.1: FMOD Studio banks and loose audio files; other engines follow the format references."
metadata:
  babeldub-cli: "0.3.0"
---

# BabelDub: build, install and rollback

This skill covers the `build`, `verify` and `install` stages and rollback. The task: turn the accepted lines, voiced **in the voices of the original heroes**, into files the game accepts as its own, and install them **reversibly**: with a backup of the originals, sha1 checks and one-command rollback.

This skill complements the `packer` role from the `babeldub-localize` skill (`references/roles/packer.md`). Talk to the user in the user's language.

## Core rules

1. **Only** `babeldub install`, `babeldub uninstall` and `babeldub reapply` **write to the game folder.** Never copy, rename or delete anything there by hand. In Claude Code, the BabelDub guard hook enforces this as well.
2. **Do not install without a successful `verify`** — the CLI refuses on its own if the current build has not passed verification.
3. **Never run the game's executables.** Verification with the game's own audio library (`verify --game-runtime`) — only at the user's explicit request.
4. **Online games and anti-cheat** — do not modify. `install` and `reapply` refuse on their own when they find EasyAntiCheat, BattlEye and the like; overriding this (`--force`) is allowed only for a game known to be offline single-player, and only by the user's explicit decision.
5. **Do not distribute built containers**: they hold third-party assets (SFX, music from the same file). Share the project and the instructions; everyone builds on their own machine.

## Workflow

### 1. Build

```text
babeldub build [--codec auto|pcm16|fadpcm]
```

- The build takes only lines whose latest QA decision is `accept` and that have a `post/audio/<uid>.wav` file. The rest (`keep_original`, `skip`, no decision) play in the original.
- For each changed container, the format adapter writes `build/out/<relpath>`, and the manifest goes to `build/build_manifest.json` (containers, sha1 of the source and the result, codec, number of replaced and kept lines, models and their licenses).
- Output: `containers[]`, `replaced` and attention lists (`missing_wav`, `stale_attempt`, `unknown_lines` …). `stale_attempt` means the `accept` decision refers to an attempt other than the latest: a new QA decision is needed.
- Exit code 2 "native rebuild is not supported (level B/C)" — the format cannot be built in v0.1. Do not build anything by hand: describe the path from the format reference (below) in the report.
- `--codec auto` (the default) for FMOD keeps the original's codec when we can write it (PCM16 or FADPCM) and replaces Vorbis with FADPCM. Details — [references/formats/fmod.md](references/formats/fmod.md).

### 2. Verification

```text
babeldub verify [--game-runtime]
```

- The adapter checks every built container without running the game: the structure; the number, order and names of entries; that replaced lines decode to the expected length and match our audio; that everything else stayed as it was.
- Output: `ok`, `problems[]`. `ok = false` or exit code 3 — do not install: put the reasons in the report; one rebuild (`babeldub build --force`) and a repeated verification are allowed.
- `--game-runtime` (FMOD) loads the FMOD Studio library from the game folder in a separate process, without sound and without starting the game, and checks every sample line against it. This is the only place where BabelDub touches the game's code, so use it only at the user's explicit request.

### 3. Install

```text
babeldub install [--dry-run]
```

- Before installing, the CLI checks: the build passed `verify`; no process from the game folder is running (the game itself, its launcher, its crash handler); there is no anti-cheat; the game files are the very originals the build was made from (sha1).
- The originals are copied to `build/backup/<relpath>` and checked by sha1, then the game file is replaced atomically (a temporary file in the same folder plus a rename). A failure in the middle of the install rolls back the files already replaced. Log — `build/install.json`.
- Output: `installed[]`, `backup`, `rollback` = `babeldub uninstall`. `--dry-run` shows the plan without writing anything.
- Errors: "the game is running" — close the game and the launcher; "not the originals this build was made from" — the game was updated or modified after the build: run `build` → `verify` → `install` again.

### 4. Rollback and reinstall

```text
babeldub uninstall          restore the originals from the backup (with sha1 checks)
babeldub reapply            install the dub again after a game update or a Steam integrity check
```

Details — what exactly the commands do, what `left_changed` and `changed[]` mean, what to do after a game update or when the backup is lost — [references/install-rollback.md](references/install-rollback.md).

## What to say in the report

- Which game files were replaced, where the backup is (`build/backup`), which codec was used.
- That the behavior of the rebuilt files in the **running** game is not checked automatically: if there is no sound or the game crashes, run `babeldub uninstall`, then rebuild with a different codec (for FMOD — `--codec pcm16`).
- The rollback command (`babeldub uninstall`) and the command that brings the dub back after an update (`babeldub reapply`).
- For cutscenes and video — ask the user to check that lines are not cut off.
- Model licenses from `build/build_manifest.json`; a reminder not to distribute the built containers.

## Formats

The container's support level is shown by `babeldub scan` (`level`) and `inventory/report.md`, which also link to the right reference. Read **only the reference for your** format:

| Format | Level in v0.1 | Reference |
|---|---|---|
| FMOD Studio `.bank` / `.fsb` | A: own rebuild, verification, install | [references/formats/fmod.md](references/formats/fmod.md) |
| loose audio files (`voice/*.ogg`, `*.wav` …) | A: re-encoding into the original's format | [references/formats/loose.md](references/formats/loose.md) |
| Unity Localization and Addressables (texts) | read-only; writing and CRC — next version | [references/formats/unity-addressables.md](references/formats/unity-addressables.md) |
| Wwise `.bnk` / `.wem` / `.pck` | B: extraction works, building requires Wwise Authoring | [references/formats/wwise.md](references/formats/wwise.md) |
| Unreal Engine 4/5 | C: instructions | [references/formats/unreal.md](references/formats/unreal.md) |
| Ren'Py | C: instructions (native path `game/tl/`) | [references/formats/renpy.md](references/formats/renpy.md) |
| Godot | C: instructions | [references/formats/godot.md](references/formats/godot.md) |
| runtime substitution (BepInEx, MelonLoader, UE4SS) | C: fallback path | [references/formats/runtime-mods.md](references/formats/runtime-mods.md) |

Markers in the references: **[verified]** — a primary source or an experiment; **[per guides]** — a secondary source; **[not verified]** — a hypothesis that must be confirmed before acting on it.

## References

- Backup, install, rollback, `reapply`, Steam, antivirus, lost backup — [references/install-rollback.md](references/install-rollback.md). Read it before the first install and on any failure of `install`, `uninstall` or `reapply`.
- The format reference from the table above — when `build` refused because of the level, or when you need to explain to the user what is possible for this engine.
