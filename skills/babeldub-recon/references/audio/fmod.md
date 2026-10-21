# FMOD Studio (`.bank`, FSB5)

Read this if the game folder has `*.bank`, `*.strings.bank`, `fmodstudio.dll`/`fmod*.dll` or `*.fsb`.

## Level in BabelDub v0.1

| What | Level |
|---|---|
| FMOD Studio localized audio tables (programmer instruments), FSB5 inside `.bank` | **A**: adapter `fmod`: extraction, building its own FSB5 (PCM16 or FADPCM), bank patching, verification |
| keeping the Vorbis codec (same size as the original) | B (planned for v0.2): needs FSBank / `fsbankcl.exe` from the FMOD Engine, which the user installs |
| banks with an encryption key | B (planned for v0.3) |
| FMOD banks in other engines (Unreal, custom) | same format; not tested on non-Unity games |

## How to identify

- `*.bank`, `*.strings.bank`, `fmodstudio.dll` or `fmod*.dll`. The FMOD version is the DLL file version.
- `*.fsb` without `.bank`: FMOD Core (FSB5) or FMOD Ex (FSB4, older).
- The voice is usually in a separate bank with the locale in its name: `Dialogue_en-US.bank`, `Dialogue_FRE.bank` and so on (localized audio table).

## `.bank` structure

- A RIFF of form `FEV `: `FMT ` (bank format version; vgmstream knows versions `0x28` to `0x86`, Low-Budget Repairs has `0x92`), `LIST 'PROJ'` (metadata: events, buses, instruments, including `SNDH`, `STBL`, `HASH`), one or more `SND ` chunks.
- `SND ` = zero padding + a plain FSB5. The FSB5 starts at an **absolute** offset that is a multiple of 32.
- `SNDH`: the table of FSBs inside the bank: absolute offset and size of each FSB5.
- `STBL`: the audio table's table; as observed, key hash → subsound index. If the count and order of subsounds stay the same, it keeps working (verified by experiment; field semantics not verified).
- `Master.strings.bank`: the GUID ↔ path mapping (`event:/…`, `bank:/…`). Not needed to replace audio; needed to recover event paths.
- **Encryption:** FMOD Studio can set an encryption key for sample data; in FMOD for Unity it is stored in the serialized `FMODStudioSettings` ("Bank Encryption Key"). If the `FSB5` magic inside `SND ` is visible, the bank is not encrypted.

## FSB5 and codecs

- FSB5 header (version 1, 0x3C bytes): subsound count, header sizes, name and data tables, **one codec for the whole FSB**, flags, a 16-byte GUID/hash (zero is allowed).
- Codecs: 1 PCM8, 2 PCM16, 3 PCM24, 4 PCM32, 5 PCMFLOAT, 6 GCADPCM, 7 IMAADPCM, 8 VAG, 9 HEVAG, 10 XMA, 11 MPEG, 12 CELT, 13 AT9, 14 XWMA, **15 VORBIS**, **16 FADPCM**, 17 OPUS.
- **Do not write Vorbis with custom code:** FSB5 does not store the setup header, only its CRC32 (setup ID), and the decoder takes the header from a built-in table (vgmstream has 161 of them). Only FSBank encodes Vorbis reliably.
- **PCM16** is raw little-endian samples; **FADPCM** is ADPCM with a 0x8C-byte frame per channel (~3.66:1 against PCM16). BabelDub writes both itself; decoding matched the reference bit for bit.
- FSBank encodes Opus in FSB5 only for Xbox Series X|S, PS5 and Switch; on Windows it is not verified.

## How a voice line, its key and its subtitle link up

- **Audio table:** files from a folder outside the FMOD project are packed into a bank and played by a programmer instrument by key. The default key is the file name without extension; `keys.txt` defines "key,file" pairs. A **localized audio table** builds one bank per locale, with a suffix.
- At runtime: an event with a programmer instrument → the `CREATE_PROGRAMMER_SOUND` callback → `Studio::System::getSoundInfo(key)` → `createSound` → subsound.
- A key may contain a subfolder. In Low-Budget Repairs the key = the event path without `event:/` (for example `Dialogue/Act2/Zbyszek/dlg_a2_m16_d00.i00_AboutRoom`), and the FSB subsound name = the Unity Localization string key (1187 matches).
- **Subsound names can repeat**, so a line must be addressed by container, FSB number and index. That is why a BabelDub line `id` is `fmod:<relpath>#fsb<k>:<index>`, with the key in `entry.key`.
- The speaker is often visible in the event path (`event:/Dialogue/<Act>/<Character>/<key>`).

## Duration: where a long line gets cut

- An **asynchronous** programmer instrument plays the sound in full: on the test game a 6.0 s replacement for a 1.92 s line played completely (verified with the FMOD 2.03.06 runtime from the game folder).
- A **synchronous** instrument plays only the part under the timeline cursor → a long replacement gets cut. Hence the rule of community GUI tools: "the same duration or shorter".
- An asynchronous instrument in **Cut** mode stops when its trigger conditions stop being met (the flag was not checked in the test game).
- **Cutscenes** with a timeline (in Unity, a fixed clip length) cut or overlap a long line: the CLI flags such lines `fixed_length` and fits them into the original length.

## Extraction tools

| Tool | What it does | License |
|---|---|---|
| vgmstream (`vgmstream-cli -s N -o out.wav file.bank`) | `.bank` (FEV + FSB5), `.fsb`, Vorbis/FADPCM/PCM/Opus; the BabelDub CLI uses it for decoding | permissive, ISC-style |
| python-fsb5 | reads FSB5, OGG from Vorbis via libvorbis | MIT |
| Fmod5Sharp | C#: decodes `.fsb`/`.bank`, Vorbis → OGG | MIT |
| Fmod-Bank-Tools (+ CLI) | extraction via FMOD and rebuild via FSBank | GPL-3.0 + FMOD EULA |
| FSB-BANK-Extractor-Rebuilder | GUI: extraction and rebuild | GPL-3.0 |
| the game's own `fmodstudio.dll` | exact PCM as the game hears it | in BabelDub only with the `verify --game-runtime` flag |

## Rebuild

| Path | How | Pros | Cons |
|---|---|---|---|
| **A. Own FSB5 (PCM16/FADPCM) + bank patch**: the BabelDub path | a new FSB5 with the same count, order, names and channels; insertion with 32-byte alignment; update of `SNDH` and the RIFF size | no proprietary tools; the FMOD runtime accepted it (experiment) | larger file than with Vorbis |
| B. FSBank API / `fsbankcl.exe` + bank patch | native Vorbis | same size as the original, encryption | the FMOD Engine can be downloaded only after logging in at fmod.com; we do not ship FSBank in the plugin (per a copy of the EULA from a third-party repository, redistribution is not allowed; the official copy was not checked), so the user installs it |
| C. Ready-made GUIs (Fmod-Bank-Tools, FenixProFmod, openFmodBank, IZH318) | FSBank and the same patch under the hood | fast by hand | GUI, Windows; require the same file type and a duration "the same or shorter" |
| D. Rebuild in FMOD Studio | needs the original `.fspro` project | official | unrealistic for someone else's game |
| E. Own Vorbis | libvorbis aimed at a known setup ID | same size as the original | risk of a setup header mismatch; not worth the effort |

Specifics of path A:

- **One codec for the whole FSB**, so the whole FSB is re-encoded, including SFX and video audio stored next to the voice lines. Subsounds that are not replaced are decoded from the original and encoded into the same new codec.
- Keep the FSB header tail (`0x1C..0x3B`) from the original if possible; zeros also work.
- `babeldub build --codec auto|pcm16|fadpcm`. Size of FSB #1 in Low-Budget Repairs (140.7 min): Vorbis original 83.5 MB; PCM16 48 kHz 1115 MB; PCM16 24 kHz 558 MB; FADPCM 48 kHz 305 MB; FADPCM 24 kHz 153 MB.
- FADPCM is cheaper on the CPU than Vorbis (FMOD benchmark for Windows: 3.10 % vs 6.88 % DSP).

## Verification and installation

- `babeldub verify`: bank structure, `SNDH`, subsound count and order, decoding.
- Loading the bank with the game's own library (`verify --game-runtime`) happens only at the user's explicit request.
- **Which bank the game loads** is decided by its code. A new `Dialogue_ru-RU.bank` works only if the game loads it; it is more reliable to replace the contents of the bank for the locale the game actually loads (in Low-Budget Repairs, `Dialogue_en-US.bank`, because the audio menu knows only `en-US` and `pl-PL`). `babeldub install` does the replacement, with a backup.
- **A rebuilt bank has not been tested in the running game.** If there is no sound: `babeldub uninstall` → `babeldub build --codec pcm16` → `verify` → `install`.
- FMOD banks have no checksums (unlike Unity Addressables bundles).

## Runtime alternative

A BepInEx 6 (IL2CPP) or MelonLoader plugin intercepts the programmer sound callback or `getSoundInfo` and returns `createSound("<line file>", CREATESTREAM)`; FMOD Core reads OGG, WAV and FLAC directly. Not tested in the test game. Downsides: the user installs the mod loader, fragility on updates.

## Pitfalls

- Synchronous instruments, Cut mode and cutscene timelines cut long lines.
- One codec for the whole FSB: neighboring SFX change too (PCM16 is lossless; FADPCM is lossy compression).
- The bank encryption key.
- Do not distribute a rebuilt bank: it contains other people's assets.

## Links

- vgmstream — https://github.com/vgmstream/vgmstream (`src/meta/fsb5.c`, `src/meta/fsb5_fev.c`, `src/coding/fadpcm_decoder.c`, `src/coding/libs/vorbis_codebooks_fsb.h`)
- FMOD Studio: Dialogue and Localization — https://www.fmod.com/docs/2.03/studio/dialogue-and-localization.html
- FMOD Studio API Guide, 13.9 — https://www.fmod.com/docs/2.03/api/studio-guide.html
- FMOD Instrument Reference (Async, Cut) — https://www.fmod.com/docs/2.03/studio/instrument-reference.html
- FSBank API — https://www.fmod.com/docs/2.03/api/fsbank-api.html ; Platform Details Windows — https://www.fmod.com/docs/2.03/api/platforms-win.html
- FMOD for Unity Settings — https://www.fmod.com/docs/2.03/unity/settings.html ; licensing — https://www.fmod.com/licensing
- python-fsb5 — https://github.com/HearthSim/python-fsb5 ; Fmod5Sharp — https://github.com/SamboyCoding/Fmod5Sharp
- Fmod-Bank-Tools — https://github.com/Wouldubeinta/Fmod-Bank-Tools ; FSB-BANK-Extractor-Rebuilder — https://github.com/IZH318/FSB-BANK-Extractor-Rebuilder
