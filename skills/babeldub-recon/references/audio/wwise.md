# Wwise (Audiokinetic): `.bnk`, `.wem`, `.pck`

Read this if the game folder has `*.bnk`, `*.wem`, `*.pck`, `AkSoundEngine*.dll` or `GeneratedSoundBanks/`.

## Level in BabelDub v0.1

**B**: adapter `wwise` finds and decodes the voice but does not rebuild:

- voice lines from `.bnk` (media in `DATA` via `DIDX`), from `.pck` (streams, external sources, banks inside the package) and from separate `.wem` files;
- names come from `SoundbanksInfo.xml`/`.json` if the game ships them; otherwise the media ID serves as the key;
- the language comes from the package's language map or the language folder (`English(US)` → `en-US`);
- decoding: PCM directly, other codecs through `vgmstream-cli`.

The originals work as voice prompts for dubbing; translation and synthesis run as usual. **Rebuilding is impossible without Wwise Authoring:** only WwiseConsole of the same Wwise version as the game can encode WAV into `.wem` (an Audiokinetic account is required). For such assets `babeldub build` returns code 2 "native rebuild is not automated (support level B)"; describe the path below to the user. Full support is planned for v0.2.

## Formats

- **`.bnk`**: chunks `BKHD` (version, bank ID), `DIDX` (media ID, offset, size), `DATA` (embedded `.wem` or the prefetch part of streams), `HIRC` (events, actions, sounds, containers, codec parameters), `STID`, `STMG`/`INIT` and others.
- **`.wem`**: RIFF/RIFX `WAVE`, codec in `fmt`: `0xFFFF` Vorbis (the Wwise variant with packed codebooks), `0x0002`/`0x8311` ADPCM/PTADPCM, `0x0001`/`0xFFFE` PCM, `0x3040`/`0x3041` Opus, `0x3039` OpusNX, as well as XMA, AAC, ATRAC9, DSP.
- **`.pck`** (`AKPK`): language map, tables of banks, streams and external sources. Localized games have separate `.pck` files or folders per language; the language is chosen at runtime (`SetCurrentLanguage`).
- **IDs** are 32-bit FNV-1 hashes of lowercase names (ShortID). Names are recovered with `wwnames` lists (wwiser).

## Linking a voice line to its subtitle

Event (`Play_VO_xxx`) → Sound → `sourceID` → `.wem`. The event name or ID most often sits in the game's dialogue data next to the string key. Dialogue Events use switch/state (character, language).

## Extraction tools

| Tool | Purpose |
|---|---|
| vgmstream | decodes `.wem/.bnk/.pck`, TXTP |
| wwiser | parses `.bnk` into XML/a tree, names, TXTP (event → sounds) |
| bnkextr | extracts `.wem` from `.bnk` |
| ww2ogg (+ revorb) | Wwise Vorbis → Ogg (old) |
| Wwise-Unpacker | batch unpacking of `pck/bnk` |
| BnkExtractor | library for extracting `wem/bnk/pck` |

## Rebuild

1. **WAV → `.wem`**: only Wwise Authoring of the same version and with the same Conversion Settings (codec, quality, sample rate) as the game. A working call example is in the ZZAR code: `WwiseConsole convert-external-source <project.wproj> --source-file list.wsources --output <dir>` (`.wsources` is XML with the root `ExternalSourcesList`). The syntax is not verified against the official Wwise documentation. Downloading Wwise through the Audiokinetic Launcher requires an account.
2. **Replacement inside `.bnk`/`.pck`:** wwiseutil ("replacement .wem files are allowed to be larger or smaller… all metadata… updated", not updated since 2018), BNKReplacer, PckTool (.NET), mhws-sound-tool (Rust), RingingBloom (Capcom games).
3. A full-pipeline precedent: hsr-ru-voiceover (Honkai: Star Rail): English `.wem` files as references, WwiseConsole → `wem`, `pck` rebuild, an installer with rollback.

## Pitfalls

- The replacement codec must match the original: the source and codec type are recorded in `HIRC` (per community guides). Whether a Vorbis `.wem` can be replaced with a PCM `.wem` without editing `HIRC` is not verified.
- Streaming with prefetch: the start of the sound sits both in the bank's `DATA` and in the `.pck`, so update both.
- Identical IDs in several `.pck` files (for example `SoundBank_*` and `Streamed_*` in Zenless Zone Zero).
- Markers and cues in `.wem` sometimes drive subtitles or lip sync (not verified).
- Duration: plain Play actions do not limit it; Music Segments and cutscene timings do.
- Keep the sample rate and channels on replacement: whether Wwise tolerates changing them is not verified.

## Links

- vgmstream — https://github.com/vgmstream/vgmstream (`src/meta/wwise.c`)
- wwiser — https://github.com/bnnm/wwiser ; bnkextr — https://github.com/eXpl0it3r/bnkextr ; ww2ogg — https://github.com/hcs64/ww2ogg
- Wwise-Unpacker — https://github.com/Vextil/Wwise-Unpacker ; BnkExtractor — https://github.com/AssetRipper/BnkExtractor
- wwiseutil — https://github.com/hpxro7/wwiseutil ; BNKReplacer — https://github.com/EtiTheSpirit/BNKReplacer ; PckTool — https://github.com/coconutbird/PckTool ; mhws-sound-tool — https://github.com/eigeen/mhws-sound-tool ; RingingBloom — https://github.com/Silvris/RingingBloom
- ZZAR — https://github.com/Pucas01/ZZAR
- hsr-ru-voiceover — https://github.com/VadimDeeptech/hsr-ru-voiceover
- Audiokinetic Q&A (convert external sources) — https://www.audiokinetic.com/qa/1539/how-do-convert-from-wav-wem-when-using-external-audio-source
