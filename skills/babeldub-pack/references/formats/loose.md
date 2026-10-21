# Loose audio files

Level in v0.1: **A** — the `loose` adapter rebuilds lines stored as separate files (`voice/*.ogg`, `vo/*.wav`, `game/voice/*.ogg` in Ren'Py, etc.). How the adapter finds such files — the `babeldub-recon` skill, `references/audio/loose.md`.

## 1. What the build does

- Each file is one line; the voice asset is the topmost folder with a "voice" name (`voice`, `vo`, `dialogue`, `speech`, `narration` …) with all files inside it.
- `babeldub build` writes **only the replaced** files to `build/out/<path in the game>`, encoded **like the original**: the same container and codec, the same sample rate and channel count.
- Encoding: WAV and FLAC — directly; OGG Vorbis, Opus, MP3 — through ffmpeg (without it — with libsndfile where possible); AAC/M4A — only through ffmpeg. No suitable encoder — exit code 4 and the hint `babeldub setup --tools`, or install ffmpeg.
- `babeldub install` replaces these files over the originals with a backup; other game files are not touched.

## 2. What to preserve

- **File name and path**: the game looks up audio by them.
- **Format, sample rate, channels**: the engine may rely on them (streaming, mixing).
- **Loudness relative to the original** — `post` matches it.

## 3. Native paths without replacing the originals [per guides, not automated in v0.1]

Some engines let you put files alongside without touching the originals:

| Engine | Mechanism | Reference |
|---|---|---|
| Ren'Py | the file `game/tl/<language>/<path>` is used instead of `game/<path>` when that language is active | [renpy.md](renpy.md) |
| Source 1 | loose files in `custom/` or the game folder override the VPK | the `babeldub-recon` skill, `references/engines/source.md` |
| Bethesda | loose files in `Data/` override BSA/BA2 archives (but voice there is `.fuz` with lip sync) | the `babeldub-recon` skill, `references/engines/bethesda.md` |

In v0.1 the CLI always replaces the original files in place (with a backup). Switching to side-by-side files is a manual path; you can describe it to the user in the report.

## 4. Pitfalls

- **Companion files**: lip-sync phonemes inside the WAV (Source) and `.lip` files next to it (Bethesda) are not updated on replacement — the lips will drift from the audio [per guides].
- **"Loose" files that are actually encrypted** (RPG Maker `.rpgmvo`, `.ogg_`) are not plain audio; the adapter will not pick them up.
- **Durations recorded in scripts or timelines** — these are `fixed_length` lines.
- **An integrity check** in Steam restores the originals → `babeldub reapply`.
