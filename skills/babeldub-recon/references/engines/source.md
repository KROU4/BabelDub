# Source and Source 2 (Valve)

Read this if the game folder has `*_dir.vpk`, `gameinfo.txt` and `bin/` (Source 1), or `game/*/pak01_dir.vpk`, `gameinfo.gi`, `*_c` resources (Source 2).

## Level in BabelDub v0.1

- **Source 1: C** in v0.1 (planned later at B: loose files and subtitles). If the voice sits in loose files, `babeldub scan` may show them through the `loose` adapter.
- **Source 2: C**: needs the game's Workshop Tools.

## Source 1

### Where the voice is

- VPK archives (`pak01_dir.vpk` + parts); loose files in `custom/` or in the game folder override the VPK (per community guides).
- Voice: `sound/vo/...` (`.wav`, `.mp3`). WAV files may carry lip-sync phonemes (FacePoser).
- Soundscripts `scripts/game_sounds*.txt` map sound names to files; `scenes.image` stores compiled choreography.

### Where the text is

Subtitles: `resource/closecaption_<language>.txt` (UTF-16) → `.dat` via `captioncompiler`. **Caption tokens are sound names**, so the "line ↔ subtitle" link is direct (per community guides).

### Extraction and rebuild tools

| Tool | Purpose |
|---|---|
| VPKEdit | creates, reads and writes VPK; CLI `vpkeditcli` |
| ValvePython/vpk | Python VPK library |
| valve_captioncompiler | `captioncompiler` rewritten in Python/C |

Native path: loose files in `custom/` or a new VPK; subtitles: `closecaption_<language>.txt` → `.dat` via captioncompiler.

## Source 2

- VPK v2, compiled `*_c` resources (audio is `.vsnd_c`).
- Reading: Source 2 Viewer (ValveResourceFormat), release 20.1.
- Recompiling: Workshop Tools (`resourcecompiler`), if the game has them (not verified).

## Pitfalls

- `sv_pure` and VAC in multiplayer → single-player only; do not modify online games by default.
- Lip-sync phonemes in WAV are lost when the file is replaced.
- Valve's developer wiki (VDC) page on Closed Captions was under maintenance on 2026-10-21; the subtitle details come from secondary sources.

## Links

- VPKEdit — https://github.com/craftablescience/VPKEdit ; vpk — https://github.com/ValvePython/vpk
- Source 2 Viewer — https://github.com/ValveResourceFormat/ValveResourceFormat
- captioncompiler — https://github.com/agentOfChaos/valve_captioncompiler
- VDC Closed Captions — https://developer.valvesoftware.com/wiki/Closed_Captions
