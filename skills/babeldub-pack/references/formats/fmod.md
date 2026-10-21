# FMOD Studio: `.bank` banks and FSB5

Level in v0.1: **A** — the CLI extracts, rebuilds, verifies and installs on its own. An encrypted bank is level C (a message only). Format recon (how the CLI finds lines, keys and speakers) is in the `babeldub-recon` skill, `references/audio/fmod.md`; this file covers the build.

Markers: [verified] — a primary source or an experiment; [per guides] — a secondary source; [not verified] — a hypothesis.

## 1. Structure

- `.bank` is a RIFF file of form `FEV `: a `FMT` chunk (format version), `LIST 'PROJ'` (metadata: events, the `STBL` audio table, the `SNDH` table, hashes …) and one or more `SND ` chunks, each holding a plain FSB5 [verified: vgmstream, analysis of the test game].
- `SNDH` is the table of FSBs inside the bank: an absolute offset and size for each FSB. An FSB5 starts at an absolute offset that is a multiple of 32 [verified].
- FSB5: header, subsound headers (sample rate, channels, data offset, sample count), name table, data. **One codec for the whole FSB** (a header field): 2 = PCM16, 15 = Vorbis, 16 = FADPCM, 17 = Opus, etc. [verified: vgmstream `fsb5.c`].
- The audio table (`STBL`, localized audio table) references subsounds **by index**; subsound names can repeat. Therefore the order, number and names of entries must not change [verified by behavior; the semantics of `STBL` are not publicly documented].
- `Master.strings.bank` holds event paths and GUIDs; it is not needed to replace audio [verified].

## 2. How the CLI rebuilds

1. Every FSB that receives replacements is rewritten **in full** in the target codec, with the same number, order and names of subsounds. Kept subsounds are copied byte for byte if the codec does not change; otherwise they are decoded (our own PCM/FADPCM decoder or `vgmstream-cli`) and re-encoded with the original sample rate and channels.
2. FSBs without replacements are copied byte for byte.
3. The new FSB is inserted in place of the old one with 32-byte alignment; `SNDH`, the `SND ` chunk size and the RIFF size are updated. Metadata outside `SND` does not change.

Codec (`babeldub build --codec`):

| `--codec` | Result | Size (FSB with the Low-Budget Repairs lines, 140.7 min) |
|---|---|---|
| `auto` (default) | if the original is already PCM16 or FADPCM — the same; otherwise (Vorbis) — FADPCM | — |
| `fadpcm` | FMOD ADPCM, our encoder | ~305 MB at 48 kHz |
| `pcm16` | uncompressed | ~1115 MB at 48 kHz |
| (original) Vorbis | our code does not write it | 83.5 MB |

Why not Vorbis: FMOD does not store the Vorbis setup header in FSB5, only its CRC32, and takes the header itself from a built-in table (vgmstream knows 161 such headers). Our own encoder would have to hit exactly those, otherwise the audio does not decode. Only FSBank or `fsbankcl.exe` from the FMOD Engine encode Vorbis reliably; downloading them requires an FMOD account, and these files must not be shipped in the plugin [verified by documentation and source code; the FSBank path is not implemented in v0.1].

The FMOD runtime accepts PCM16 and FADPCM: a bank rebuilt from the test game's `Dialogue_en-US.bank` with PCM16 (48 and 24 kHz) and FADPCM loaded in FMOD Studio 2.03.06 (the library from the game folder, via ctypes), the keys resolved and the lines played; decoding matched the reference bit for bit [verified by experiment]. **This was not checked in the running game** [not verified].

## 3. Verification (`babeldub verify`)

Without running the game: the RIFF size and the chunk walk; the `SNDH` entries (FSB5 magic, alignment, sizes); metadata outside `SND` byte for byte as in the original; the same number of FSBs; in every FSB the same number, order and names of subsounds; untouched FSBs byte for byte; kept subsounds with the same sample rate, channels and length; replaced ones decode to the expected length and match our audio; if available — a cross-check with `vgmstream-cli`.

`verify --game-runtime` additionally loads `fmodstudio` from the game folder in a separate process (NOSOUND output, nothing plays, the game does not start) and, for sample lines, checks `getSoundInfo(key)`, the length and the PCM against our decoder bit for bit. Only at the user's explicit request.

## 4. Line length

- An **asynchronous programmer instrument** (typical for dialogue) plays the whole line: a 6.0 s replacement for a 1.92 s line played in full [verified by experiment on the runtime].
- A **synchronous instrument** plays only the part of the sound under the timeline cursor — a long replacement gets cut; the asynchronous one has a Cut mode that can also cut the sound off [verified by FMOD documentation; Cut mode in a specific game's events was not checked].
- **Cutscenes** on the engine's timeline (Unity Timeline, etc.) have a fixed clip length: the CLI marks such lines `fixed_length`.
- That is why popular GUI rebuild tools require "the same duration or shorter"; BabelDub allows longer lines only where it is safe (length policy — the `babeldub-voice` skill).

## 5. Localized banks and language selection

- A localized audio table builds a separate bank per locale with a suffix (`Dialogue_en-US.bank`, `Dialogue_pl-PL.bank`); their metadata and event GUIDs are identical, the audio table and the FSBs differ [verified on the test game].
- **The game's code** decides which bank to load. In v0.1 the CLI replaces the bank of the locale the lines were extracted from. Example: in Low-Budget Repairs only `en-US` and `pl-PL` are hard-coded in the voice menu, and `en-US` loads by default; so the Russian dub replaces `Dialogue_en-US.bank`, and a player with the English voice setting hears Russian. The game will not load a separate `Dialogue_ru-RU.bank` by itself without a code patch or a mod loader ([runtime-mods.md](runtime-mods.md)) [verified by analysis of the test game's IL2CPP].
- Tell the user this in the report: the voice of the selected language was replaced; rollback — `babeldub uninstall`.

## 6. Pitfalls

| Pitfall | Symptom | What to do |
|---|---|---|
| encrypted bank | `scan`: level C, no FSB5 magic | v0.1 does not build it; the encryption key is stored in the game's FMOD settings [per guides] — put it in the report |
| old FMOD versions | bank version outside the range vgmstream knows | `verify` will show problems; do not install |
| SFX and video in the same FSB as the lines | they get re-encoded too when the codec changes | loudness and sound do not change, but the file size grows; check the videos (`video_*`) in the game by ear |
| PCM16 size | the bank is 10+ times larger than the original | `fadpcm` (the default for Vorbis) |
| Steam "Verify integrity" | the bank reverted to the original | `babeldub reapply` |

## 7. What not to do

- Do not rebuild the bank with third-party GUI tools on top of the installed dub: the `install.json` log will no longer match, and `uninstall` will leave the file as "changed".
- Do not put `fsbankcl.exe`, `fsbank.dll` or other FMOD Engine files into the project or the plugin: the FMOD license does not allow it.
