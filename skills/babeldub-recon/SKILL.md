---
name: babeldub-recon
description: "Find out what is inside a video game before localizing it and dubbing it in the original characters' voices: detect the engine, audio middleware (FMOD, Wwise, CRIWARE, loose files, video) and text tables, inventory the voice lines, extract the original audio and link subtitles to voice lines and analyze the original speech (VAD, ASR, line classes) with babeldub scan, extract, link and analyze. Use for the recon stage or for questions like 'what engine is this game on', 'where are the voice files and subtitles', 'can this be repacked'. Example triggers: 'localize this game', 'dub the voices', 'что внутри игры', 'какой движок', 'где озвучка и субтитры', 'переведи игру', 'сделай русскую озвучку', 'doblar el juego'."
license: MIT
compatibility: "Local agent with shell access to the game folder (read-only); babeldub CLI 0.1.0 installed via the babeldub-doctor skill; vgmstream-cli for decoding. Works without GPU."
metadata:
  babeldub-cli: "0.2.0"
---

# BabelDub: game recon

This skill answers "what engine is the game on", "where are the voice and the texts", "which languages", "can it be packed back", and prepares the original voice lines. Those lines later become the voice prompts for dubbing: the dub speaks in the original character's voice. So recon must find **the voice itself** for every line and **its exact source text**.

Talk to the user in the user's language. Keep commands, flags, paths and JSON field names as they are.

The skill is universal. Low-Budget Repairs (Unity + FMOD + Unity Localization) appears in the examples only as an illustration.

## Rules

1. **The game folder is read-only.** Do not copy into it, rename anything in it or unpack in place. All working files go into the project (`BABELDUB_HOME/projects/<slug>`).
2. **Run nothing from the game folder:** no exe, scripts or DLLs. Game folders sometimes contain unrelated executables. Checking with the game's own runtime (`babeldub verify --game-runtime`) happens only at the user's explicit request.
3. **Do not read whole game binaries.** Look at folder listings, file names and sizes, and CLI output.
4. **List the unknown, do not guess.** If a format is not recognized, say so.
5. **Online games and anti-cheat** (EasyAntiCheat, BattlEye, VAC in multiplayer): do not modify by default; tell the user.

## Steps

1. The project must exist (`babeldub init`, skill `babeldub-localize`). Check with `babeldub status`.
2. `babeldub scan` → `engine{id, version, backend}`, `middleware[]`, `text_systems[]`, `voice_assets[]`, `text_assets[]`, `counts{}`, `level`, `warnings[]`, `next`. The detailed report is `inventory/report.md` (read it section by section; the "What to read next" section lists the references).
3. Read the references **only for what was found**:
   - engine: `references/engines/<engine>.md`;
   - audio: `references/audio/<format>.md`;
   - texts: `references/text/<system>.md`.

   The CLI names the files itself: `inventory/recon.json` → `refs.recon` and `refs.pack`, and in `report.md` the "What to read next" section (paths like `skills/babeldub-recon/references/audio/fmod.md`). Which references match what was found:

   | Found (id in `recon.json`) | File |
   |---|---|
   | `unity` (backend `il2cpp` or `mono`) | `references/engines/unity.md` |
   | `unreal` | `references/engines/unreal.md` (texts: `references/text/unreal-locres.md`) |
   | `godot` | `references/engines/godot.md`, `references/text/godot.md` |
   | `renpy` | `references/engines/renpy.md`, `references/text/renpy.md` |
   | `rpgmaker` | `references/engines/rpgmaker.md`, `references/text/rpgmaker.md` |
   | `gamemaker` | `references/engines/gamemaker.md` |
   | `source`, `source2` | `references/engines/source.md` |
   | `bethesda` | `references/engines/bethesda.md` |
   | `cryengine`, `xna`, `electron`, `love2d`, `telltale`, unrecognized engine | `references/engines/other.md` |
   | `fmod` | `references/audio/fmod.md` |
   | `wwise` | `references/audio/wwise.md` |
   | `criware` | `references/audio/criware.md` |
   | `bink` | `references/audio/video.md` |
   | separate audio files (adapter `loose`) | `references/audio/loose.md` |
   | `unity-localization` | `references/text/unity-localization.md` |
   | `gettext` and other text formats | `references/text/generic.md` |
4. `babeldub extract` → `lines`, `by_kind{}`, `minutes`, `texts`, `locales[]`.
5. `babeldub link` → `linked`, `unlinked`, `with_official_target`.
6. `babeldub analyze` (the `qc` worker on the GPU; code 4 without the `qc` env → `babeldub-doctor`) → `analyzed`, `classes{speech, short_word, interjection, nonverbal, mixed}`, `subtitle_mismatch`, `mismatch_ids[]`. It measures every voiced original (VAD speech segments, ASR with word times, pitch and loudness) into `inventory/analysis.jsonl` and flags lines whose subtitle does not match what is actually said (`subtitle_mismatch`). `interjection` and `nonverbal` lines ("Hehe…", "Hmm", screams) are meant to keep the original audio; mention the class counts and the mismatches in the report.
7. Check the result with `babeldub query` samples (always with `--fields` and `--limit`):
   - unrecognized audio: `--where kind=unknown --fields id,entry.key,audio.dur_s --limit 30`;
   - dialogue without text: `--where kind=dialogue --fields id,entry.key,flags --limit 30` and look for `no_text`;
   - subtitles that do not match the audio: `--where flags=subtitle_mismatch --fields id,text.src,analysis.asr_text --limit 30`;
   - line classes: `--where class=interjection --fields id,text.src,analysis.speech_s --limit 30`.
8. Return the summary to the orchestrator in the scout role format (`babeldub-localize/references/roles/scout.md`).

Exit codes: 0: continue; 2: read `hint` (usually a previous stage has not been run); 3: there are warnings or unparsed entries, work through them with the references; 4: a decoder or the environment is missing, use the `babeldub-doctor` skill; 1: error, see the project's `logs/`.

## How to identify the engine and middleware from files

| Sign in the game folder | Conclusion | Reference |
|---|---|---|
| `<Game>_Data/`, `UnityPlayer.dll`, `globalgamemanagers` | Unity | `engines/unity.md` |
| `GameAssembly.dll` + `<Game>_Data/il2cpp_data/Metadata/global-metadata.dat` | Unity IL2CPP | `engines/unity.md` |
| `<Game>_Data/Managed/Assembly-CSharp.dll` | Unity Mono | `engines/unity.md` |
| `StreamingAssets/aa/catalog.json` or `catalog.bin`, `settings.json` | Unity Addressables | `engines/unity.md` |
| bundles `localization-string-tables-*`, `localization-assets-shared*` | Unity Localization | `text/unity-localization.md` |
| asset `I2Languages` (usually in `resources.assets`) | I2 Localization | `text/i2-localization.md` |
| `*.bank`, `*.strings.bank`, `fmodstudio.dll` / `fmod*.dll` | FMOD Studio | `audio/fmod.md` |
| `*.fsb` without `.bank` | FMOD Core (FSB) or FMOD Ex (FSB4, older) | `audio/fmod.md` |
| `*.bnk`, `*.wem`, `*.pck`, `AkSoundEngine*.dll`, `GeneratedSoundBanks/` | Wwise | `audio/wwise.md` |
| `*.acb`, `*.awb`, `*.cpk`, `*.usm`, `cri_*.dll` | CRIWARE (ADX2, Sofdec) | `audio/criware.md`, `audio/video.md` |
| `<Game>/Content/Paks/*.pak` (+ `.utoc`/`.ucas`), `<Game>-Win64-Shipping.exe` | Unreal 4/5 (`.utoc` means IoStore) | `engines/unreal.md`, `text/unreal-locres.md` |
| `*.pck` with the `GDPC` magic, or an exe with GDPC at its end | Godot | `engines/godot.md`, `text/godot.md` |
| `game/`, `renpy/`, `*.rpa`, `*.rpyc` | Ren'Py | `engines/renpy.md`, `text/renpy.md` |
| `www/js/rpg_core.js` or `js/rmmz_core.js`, `data/System.json` | RPG Maker MV or MZ | `engines/rpgmaker.md`, `text/rpgmaker.md` |
| `Game.rgss3a`, `RGSS3*.dll` (`.rgss2a` is VX, `.rgssad` is XP) | RPG Maker VX Ace / VX / XP | `engines/rpgmaker.md` |
| `data.win`, `audiogroup*.dat`, `options.ini` | GameMaker | `engines/gamemaker.md` |
| `*_dir.vpk`, `gameinfo.txt`, `bin/` | Source 1 | `engines/source.md` |
| `game/*/pak01_dir.vpk`, `gameinfo.gi`, `*_c` resources | Source 2 | `engines/source.md` |
| `Data/*.bsa` or `*.ba2`, `*.esm`, `Data/Sound/Voice/` | Bethesda Creation Engine | `engines/bethesda.md` |
| separate `*.wav`, `*.ogg`, `*.mp3` in folders like `voice/`, `vo/`, `sound/` | loose audio files | `audio/loose.md` |
| `*.bik`, `*.bk2`, `*.usm`, `*.mp4`, `*.webm` | video clips | `audio/video.md` |
| none of the above | custom engine | `engines/other.md`, `text/generic.md` |

Audio middleware does not depend on the engine: FMOD and Wwise appear in Unity, in Unreal and in custom engines. Check both signs.

## Support levels in BabelDub v0.1

| Level | Meaning | What to do |
|---|---|---|
| **A** | the CLI extracts, rebuilds and verifies on its own, without proprietary software | stages run automatically |
| **B** | needs a tool the user installs (WwiseConsole, UE Editor, Godot of the same version, `fsbankcl`), a mod loader or a manual step | describe honestly in the report what is needed; CLI v0.1 does not do such a rebuild |
| **C** | v0.1 only reads and gives instructions | describe the path from the reference; do not rebuild anything by hand |

`babeldub scan` shows the level of a specific game (field `level`: the best level among voice assets, C if no voice was found; the level of each asset is in `voice_assets[]` and `inventory/assets.jsonl`). CLI v0.1 adapters:

| Adapter | What it does | Level |
|---|---|---|
| `fmod` | FMOD Studio banks (`.bank`, FSB5): voice lines, decoding, building its own FSB5, verification | A |
| `loose` | separate audio files (OGG, WAV, MP3, FLAC, Opus, M4A, AAC) in "voice" folders: voice lines, decoding, re-encoding the replacement into the original format | A |
| `unity-localization` | reads Unity Localization tables for `link`; no writing into the game | A for reading (C if the bundles have no typetree) |
| `wwise` | `.bnk`, `.pck`, `.wem`: voice lines and decoding for voice prompts; no rebuild (needs WwiseConsole) | B |

The CLI does not parse the other engines from the table above yet: for them there are only references.

## How a voice line links to its subtitle

From most to least common:

1. **Shared key:** the voice file name (FSB or Wwise subsound name, SoundWave name) equals the string key. In Low-Budget Repairs 1187 lines are linked this way.
2. **Key in event metadata:** the programmer instrument name in FMOD, the Dialogue Event in Wwise, `DialogueWave` in UE, the caption token in Source (it is the sound script name).
3. **Game data:** dialogue graphs, ScriptableObjects or JSON with `audio` and `text` fields. They are found by decompilation or string search; in v0.1 this is manual work and material for the report.
4. **Ren'Py:** the line identifier and `config.auto_voice`.
5. **Fallback:** speech recognition of the original plus fuzzy matching. CLI v0.1 does not have it: lines without text keep the original voice.

`babeldub link` implements item 1 (exact key → normalized key → file name). Everything else goes into the scout report as hypotheses.

## What to preserve when replacing audio

- The order, count and names of entries in a container: engine tables refer to them by index.
- The channel count (mono → mono). FMOD allows changing the sample rate and codec; for Wwise, CRI and Unity this is not verified.
- Loudness relative to the original line, not an absolute target.
- Duration where the audio is played by a synchronous instrument or a timeline (cutscenes, video) and where the length is stored in metadata (Bethesda `.lip`, Source phonemes). The CLI flags such lines `fixed_length`.

## Pitfalls to report

- Steam "Verify integrity of game files" and game updates restore the originals → `babeldub reapply`.
- Anti-cheat → refuse by default.
- Precomputed lip sync (`.lip`, Source phonemes, FaceFX) is not updated when the audio is replaced.
- Fonts without the target alphabet (for example, Cyrillic): matters if texts change (in v0.1 the texts in the game do not change).
- Encryption: the FMOD bank key, AES in Unreal and Godot, HCA keys in CRIWARE, encrypted RPG Maker resources.
- Distribute a patcher, not rebuilt game containers: those contain other people's assets.

## References

Read a file only when `scan` or the folder listing showed the matching sign.

- Engines: `references/engines/`: [unity](references/engines/unity.md), [unreal](references/engines/unreal.md), [godot](references/engines/godot.md), [renpy](references/engines/renpy.md), [rpgmaker](references/engines/rpgmaker.md), [gamemaker](references/engines/gamemaker.md), [source](references/engines/source.md), [bethesda](references/engines/bethesda.md), [other](references/engines/other.md).
- Audio: `references/audio/`: [fmod](references/audio/fmod.md), [wwise](references/audio/wwise.md), [criware](references/audio/criware.md), [loose](references/audio/loose.md), [video](references/audio/video.md).
- Texts: `references/text/`: [unity-localization](references/text/unity-localization.md), [i2-localization](references/text/i2-localization.md), [unreal-locres](references/text/unreal-locres.md), [godot](references/text/godot.md), [renpy](references/text/renpy.md), [rpgmaker](references/text/rpgmaker.md), [generic](references/text/generic.md).

Markers in the references: "verified" means a primary source or an experiment on a test game; "per community guides" means a secondary source; "not verified" means a hypothesis that must be confirmed before acting on it.
