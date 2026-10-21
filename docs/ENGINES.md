# Supported engines and formats

BabelDub aims at **any** game. `babeldub scan` detects the engine, the audio middleware and the text system, and reports a support level for every voice asset. What BabelDub can do for a game depends mostly on **where the voice lives** (FMOD, Wwise, loose files…), not on the engine: FMOD and Wwise appear in Unity, Unreal and custom engines alike.

This page describes BabelDub **v0.1**. The "Planned" column comes from the project research and the skill references; it is a plan, not a promise.

## Support levels

| Level | Meaning | What happens |
|---|---|---|
| **A** | The CLI extracts, rebuilds and verifies on its own, without proprietary software | The pipeline runs automatically up to `babeldub install` (with backup and rollback) |
| **B** | Needs a tool the user installs (WwiseConsole, UE Editor, Godot of the same version, `fsbankcl`), a mod loader or a manual step | The lines can still be extracted, translated and dubbed; `babeldub build` refuses with exit code 2 and the agent describes the manual path from the format reference |
| **C** | Read-only or instructions only in v0.1 | The agent describes the path from the reference; nothing is built by hand and nothing is written into the game folder |
| refused | Online games with anti-cheat | `install` and `reapply` refuse by default |

The level of a particular game is in the `scan` output: `level` is the best level among the voice assets (C if no voice was found); the level of each asset is in `voice_assets[]` and `inventory/assets.jsonl`. `inventory/report.md` and `inventory/recon.json` (`refs.recon`, `refs.pack`) point to the references the agent must read.

## What v0.1 does automatically

| Adapter | What it handles | Level |
|---|---|---|
| `fmod` | FMOD Studio banks (`.bank`, FSB5) and `.fsb`: lists lines, decodes them, writes its own FSB5 (PCM16 or FADPCM) keeping the number, order and names of subsounds, patches the bank, verifies | A |
| `loose` | Loose audio files (OGG, WAV, MP3, FLAC, Opus, M4A, AAC) in "voice" folders (`voice/`, `vo/`, `dialogue/`, `speech/`, `narration/`, `game/voice/` …): lists lines, decodes, re-encodes each replacement into the original's format | A |
| `unity-localization` | Unity Localization string tables (Addressables): reads every language for `link`, finds the official translation; does not write into the game | A for reading (C if the bundles have no typetree) |
| `wwise` | Wwise `.bnk`, `.pck`, `.wem`: lists and decodes the voice for use as voice prompts; no rebuild (needs WwiseConsole) | B |

Every other engine below is detected by `scan`, and the agent gets a reference for it, but the CLI does not parse its containers yet.

## Support matrix

| Engine / format | Extraction (originals, texts) | Native rebuild | Level in v0.1 | What the user needs | Planned | References |
|---|---|---|---|---|---|---|
| **FMOD Studio**, localized audio tables (programmer instruments), FSB5 in `.bank` | yes: own parser + vgmstream | yes: own FSB5 writer (PCM16 / FADPCM) + bank patch; `--codec auto` keeps PCM16 or FADPCM and replaces Vorbis with FADPCM | **A** | nothing | — | [recon](../skills/babeldub-recon/references/audio/fmod.md), [pack](../skills/babeldub-pack/references/formats/fmod.md) |
| FMOD, keep the Vorbis codec (same size as the original) | yes | no | B | FSBank `fsbankcl` from FMOD Engine (fmod.com login) | v0.2 | same as above |
| FMOD, encrypted bank | no (`scan` reports it) | no | C | the bank encryption key (stored in the game's FMOD settings) | B, v0.3 | same as above |
| FMOD in other engines (Unreal, custom) | as for Unity | as for Unity | A for audio (not tested on non-Unity games); texts depend on the engine | nothing | — | same as above |
| **Loose audio files** (Ren'Py, RPG Maker, Source, many indie games) | yes | yes: re-encoding into the original container, codec, sample rate and channels | **A** | ffmpeg (OGG/Opus/MP3 fall back to libsndfile where possible; AAC/M4A need ffmpeg) | side-by-side install (Ren'Py `game/tl/`, Source `custom/`) is a manual path for now | [recon](../skills/babeldub-recon/references/audio/loose.md), [pack](../skills/babeldub-pack/references/formats/loose.md) |
| **Unity Localization** (StringTable in Addressables) | yes: texts of every language + official translation | no (subtitles are not changed in v0.1) | **A** (read); C without typetree | optional TypeTreeGeneratorAPI package when the bundles lack a typetree | writing tables, v0.2 | [recon](../skills/babeldub-recon/references/text/unity-localization.md), [pack](../skills/babeldub-pack/references/formats/unity-addressables.md) |
| **Wwise** (`.bnk`, `.wem`, `.pck`) | yes: PCM directly, other codecs through vgmstream | no: WAV → `.wem` only in Wwise Authoring; replacing media with wwiseutil / BNKReplacer / PckTool | **B** | Wwise Authoring of the same version as the game (Audiokinetic account) | full support, v0.2 | [recon](../skills/babeldub-recon/references/audio/wwise.md), [pack](../skills/babeldub-pack/references/formats/wwise.md) |
| Unity built-in audio (AudioClip) | no | no | C | an experiment is needed; fallback: a runtime mod (BepInEx / MelonLoader) | v0.3 | [recon](../skills/babeldub-recon/references/engines/unity.md) |
| I2 Localization (Unity) | no | no | C | — | B, v0.3 | [recon](../skills/babeldub-recon/references/text/i2-localization.md) |
| Unreal Engine 4/5, texts (`.locres`) | no (FModel, CUE4Parse, UnrealLocres) | no (`.locres` in a `_P` pak with repak / retoc `to-zen`) | C | the AES key if the pak is encrypted | B, v0.3 | [recon](../skills/babeldub-recon/references/engines/unreal.md), [text](../skills/babeldub-recon/references/text/unreal-locres.md), [pack](../skills/babeldub-pack/references/formats/unreal.md) |
| Unreal Engine 4/5, SoundWave | no (FModel) | no: cook in UE Editor | C | Unreal Editor of the same version | later | same as above |
| Unreal with FMOD or Wwise | see the FMOD / Wwise rows | | | | | |
| Ren'Py | voice as loose files through `loose` (if not inside `.rpa`) | loose files: yes; native `game/tl/<language>/` path: manual | C (A for loose voice files) | unrpa / rpatool and unrpyc for `.rpa` / `.rpyc` | candidate for A, v0.2 | [recon](../skills/babeldub-recon/references/engines/renpy.md), [text](../skills/babeldub-recon/references/text/renpy.md), [pack](../skills/babeldub-pack/references/formats/renpy.md) |
| Godot 3/4 | no (GDRE Tools) | no: GDRE `--pck-patch`, `--patch-translations`; audio imported in Godot of the same version | C | Godot of the same version; the AES key (gdke) for encrypted games | B, v0.3 | [recon](../skills/babeldub-recon/references/engines/godot.md), [text](../skills/babeldub-recon/references/text/godot.md), [pack](../skills/babeldub-pack/references/formats/godot.md) |
| RPG Maker MV/MZ, XP/VX/Ace | unencrypted loose audio through `loose` | no | C | RPGMakerDecrypter, rvpacker-txt-rs | text A, voice B (depends on the game's plugins); later | [recon](../skills/babeldub-recon/references/engines/rpgmaker.md), [text](../skills/babeldub-recon/references/text/rpgmaker.md) |
| GameMaker | no | no | C | UndertaleModTool | B, later | [recon](../skills/babeldub-recon/references/engines/gamemaker.md) |
| Source 1 | loose voice files through `loose` | no (loose files in `custom/` + `closecaption` → `.dat`; lip-sync phonemes are lost) | C | VPKEdit, captioncompiler | B, later | [recon](../skills/babeldub-recon/references/engines/source.md) |
| Source 2 | no | no | C | the game's Workshop Tools (`resourcecompiler`) | — | same as above |
| Bethesda Creation Engine (`.fuz` = `.lip` + `.xwm`) | no | no | C | `FonixData.cdf` from the Creation Kit (lip sync), xWMAEncode, FaceFXWrapper, BSArch | B/C, later | [recon](../skills/babeldub-recon/references/engines/bethesda.md) |
| CRIWARE (ACB/AWB/HCA) | no | no | C | the HCA key, VGAudio, CriCodecs | B, later | [recon](../skills/babeldub-recon/references/audio/criware.md) |
| Video (Bink, USM, MP4/WebM, `video_*` tracks in banks) | no | no | out of scope: cutscene audio keeps the original voice and is listed in the report | — | demux, voice/background separation, timed dubbing, remux; later | [recon](../skills/babeldub-recon/references/audio/video.md) |
| CryEngine, XNA / FNA / MonoGame, Electron / NW.js / HTML5, LÖVE, Telltale Tool, custom engines | depends on the audio: `fmod`, `wwise` or `loose` when found | as for that audio | as for that audio; custom archives are C | — | — | [recon](../skills/babeldub-recon/references/engines/other.md), [text](../skills/babeldub-recon/references/text/generic.md) |
| Runtime substitution (BepInEx, MelonLoader, XUnity.AutoTranslator, RE-UE4SS) | — | a mod-loader plugin written for the specific game | C (fallback) | the mod loader | — | [pack](../skills/babeldub-pack/references/formats/runtime-mods.md) |
| Online games with anti-cheat (Easy Anti-Cheat, BattlEye, Riot Vanguard, nProtect GameGuard, XIGNCODE3, kernel drivers next to the game) | — | — | **refused by default** | `install --force` only for a known offline single-player game, by the user's explicit decision | — | [install and rollback](../skills/babeldub-pack/references/install-rollback.md) |

## Automatic steps and user-installed tools

At level A the whole chain is automatic: `scan` → `extract` → `link` → `cast` → `translate` → `synth` → `post` → `qa` → `build` → `verify` → `install`, with a backup and `babeldub uninstall` / `babeldub reapply`. The game folder stays read-only until `install`, and BabelDub never runs the game's executables (the optional `verify --game-runtime` check, only on the user's explicit request, loads FMOD's library from the game folder in a child process).

| Tool | Who installs it | Needed for |
|---|---|---|
| `vgmstream-cli` | `babeldub setup --tools` (official GitHub release, sha256 checked) | decoding FSB5, Wwise and other game audio |
| `ffmpeg`, `ffprobe` | the user (required; `doctor` returns code 4 without them) | conversion, re-encoding loose files, `atempo` time stretching |
| Rubber Band (`rubberband`) | the user (optional, GPL, not vendored) | higher-quality time stretching; otherwise `ffmpeg atempo` |
| WwiseConsole, `fsbankcl`, UE Editor, Godot, mod loaders, decryption keys | the user | level B/C manual paths, see the matrix |

## How detection works

`scan` reads signatures from [`runtime/src/babeldub/data/engines.yaml`](../runtime/src/babeldub/data/engines.yaml): file and folder names, globs on relative paths, extensions and magic bytes (for example `GDPC` in a Godot `.pck` or `AKPK` in a Wwise `.pck`). Each matching signal adds its weight to the entry's score; an engine, middleware, text system or anti-cheat is reported once its score reaches `min_score`, and the engine with the highest score wins. Each entry names its `recon_ref` (a file under `skills/babeldub-recon/references/`) and, where one exists, a `pack_ref` (under `skills/babeldub-pack/references/formats/`).

Things to tell the user regardless of the level:

- a Steam integrity check or a game update restores the original files: run `babeldub reapply`;
- precomputed lip sync (`.lip`, Source phonemes, FaceFX) is not updated when audio is replaced;
- encryption (FMOD bank keys, AES in Unreal and Godot, CRIWARE HCA keys, encrypted RPG Maker resources) blocks extraction or rebuild until the user provides the key;
- share a patcher or instructions, never rebuilt game containers: they contain other people's assets (see [LEGAL.md](LEGAL.md)).

## Adding an engine

See [CONTRIBUTING.md](../CONTRIBUTING.md#how-to-add-an-engine-or-a-format-an-adapter): an adapter in `runtime/src/babeldub/adapters/<name>/`, signatures in `engines.yaml`, synthetic tests, references for the agent and a row in this table. To request support for a game, open a "New game or engine support" issue.
