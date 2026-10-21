# Loose audio files (WAV, OGG, MP3 and others)

Read this if the voice is stored as separate files rather than in a middleware container: `voice/`, `vo/`, `sound/vo/`, `game/voice/`, `Data/Sound/Voice/` (loose), `audio/se/` and the like.

## Level in BabelDub v0.1

**A**: adapter `loose`:

- finds `.ogg`, `.wav`, `.mp3`, `.flac`, `.opus`, `.m4a`, `.aac` files;
- a voice asset is the topmost folder whose name indicates speech (`voice/`, `vo/`, `dialogue/`, `speech/`, `narration/`, `game/voice` in Ren'Py and the like), with all files inside; other audio is grouped by folder as music and effects;
- takes the speaker and the scene from subfolder names when they look like a character name or a scene number;
- `build` re-encodes only the replaced files into the original format (same container and codec, sample rate, channels); `install` puts them over the originals, with a backup.

`babeldub scan` shows exactly what was picked up in a specific game (`voice_assets[]`, `inventory/assets.jsonl`, `inventory/report.md`).

## Where it appears

| Engine | Where the voice is | Reference |
|---|---|---|
| Ren'Py | `game/voice/*.ogg` (if not in `.rpa`) | `engines/renpy.md` |
| Source 1 | `sound/vo/*.wav`, `*.mp3` (loose or in a VPK) | `engines/source.md` |
| RPG Maker MV/MZ | `audio/se/*.ogg` (if not encrypted) | `engines/rpgmaker.md` |
| GameMaker | external `.ogg` files for streams | `engines/gamemaker.md` |
| Bethesda | loose files in `Data/Sound/Voice/` (usually `.fuz`, not plain audio) | `engines/bethesda.md` |
| custom engines | folders named `voice`, `vo`, `dialog`, or with language codes | `engines/other.md` |

## How to tell voice from other audio

- Names: `dlg_`, `vo_`, `bark_`, `voice`, a character name, a language code in the path.
- Duration and share of speech: the CLI classifies SFX and music by `kind`.
- Whether a text with the same key exists nearby (file name = string key): then `babeldub link` links them by file name.

## Native path

Replace a file with a file of the same name and format: sample rate, channels and codec as in the original. Sometimes the game lets loose files override archives (Source 1 `custom/`, Bethesda `Data/`) or swaps files by language folder (Ren'Py `game/tl/<language>/`); then the original can stay untouched.

## Pitfalls

- Companion files: lip-sync phonemes in WAV (Source), a `.lip` next to the file (Bethesda) are not updated on replacement.
- Encrypted "loose" files (RPG Maker `.rpgmvo`, `.ogg_`) are not plain audio.
- Duration stored in scripts or timelines.
- Steam "Verify integrity" restores the originals → `babeldub reapply`.
