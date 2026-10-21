# Ren'Py: `game/tl/<language>/` and voice

Level in v0.1: **C** — CLI v0.1 has no dedicated Ren'Py adapter. If the voice is stored as separate files (`game/voice/*.ogg`, not inside `.rpa`), the `loose` adapter extracts and builds it ([loose.md](loose.md)), replacing the originals in place. The native path below is for manual work. Recon — the `babeldub-recon` skill, `references/engines/renpy.md` and `references/text/renpy.md`.

Markers: [verified] — Ren'Py documentation; [per guides]; [not verified].

## 1. Native localization [verified: Ren'Py documentation]

- Translations live in `game/tl/<language>/*.rpy`: `translate <language> <id>:` blocks for dialogue and `translate <language> strings:` for UI strings.
- Voice: the `voice "file.ogg"` statement, `config.voice_filename_format`, and also **`config.auto_voice`**, where `{id}` is replaced with the line identifier.
- **File translation:** the file `game/tl/<language>/<path>` is used instead of `game/<path>` when that language is active. Example from the documentation: `game/tl/french/omelette.ogg` replaces `game/omelette.ogg` in French. Auto-voice works the same way if the path under `tl/<language>` mirrors the original.

This means that for Ren'Py the dub can be installed **alongside**, without touching the originals: put the `post/audio` files under `game/tl/<language>/` at the same relative paths and switch the language on in the game.

## 2. How to do it before an adapter exists [not automated in v0.1]

1. If the voice is in `game/voice/` as separate files, the simplest option is the normal BabelDub path: the `loose` adapter builds it and `babeldub install` replaces the files with a backup. The dub will play with any UI language.
2. If a "per-language" dub is required (the original stays for other languages), use the manual path from section 1. The user does it (or the agent, **only at the user's explicit request** and only in a copy), because only the CLI writes to the game folder in BabelDub.
3. If the voice and scripts are inside `.rpa`, they are unpacked with `unrpa` or `rpatool` (moved to Codeberg), and `.rpyc` scripts are decompiled with `unrpyc` [per guides]. The Ren'Py SDK can "Generate Translations" and "Extract Dialogue" (line identifiers for `auto_voice`) [verified: documentation].

## 3. Pitfalls

- `unrpyc` compatibility with the Ren'Py version; modified RPA archives [per guides].
- Fonts without Cyrillic (or another target script): for translated texts, set a font style in a `translate <language> python:` block [per guides].
- Duration: Ren'Py usually does not cut a line off, but text auto-advance may depend on the audio length [not verified].
