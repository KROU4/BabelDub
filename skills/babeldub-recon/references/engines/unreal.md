# Unreal Engine 4/5

Read this if the game folder has `<Game>/Content/Paks/*.pak` (possibly with `.utoc`/`.ucas`) and `<Game>-Win64-Shipping.exe`.

## Level in BabelDub v0.1

| What | Level | Comment |
|---|---|---|
| voice in FMOD banks inside UE (`Content/FMOD/Desktop/*.bank`) | see `audio/fmod.md` | the bank format does not depend on the engine; not tested on non-Unity games |
| voice in Wwise | see `audio/wwise.md` | |
| `.locres` texts | C in v0.1 (planned B, v0.3) | see `text/unreal-locres.md` |
| SoundWave (native UE audio) | C | needs a cook in the UE Editor of the same version |

## How to identify

- `Content/Paks/*.pak`: paks (versions v1–v11).
- `.utoc` + `.ucas` next to an "empty" `.pak`: **IoStore** (UE5 and late UE4).
- AES-256 encryption (the key is in the exe), `.sig` signatures.
- `Content/FMOD/Desktop/*.bank`: FMOD; `.bnk`/`.wem` or uasset media: Wwise.

## Where the voice is

- `SoundWave` (`.uasset/.uexp`, data in `.ubulk` or inline; formats OGG, ADPCM, Opus, Bink Audio in UE5; per-version details not verified).
- `DialogueWave` + `DialogueVoice`.
- Localized assets in `Content/L10N/<culture>/...`: the native per-culture voice mechanism (per community guides).
- Often middleware: Wwise or FMOD.

## Where the text is

`Content/Localization/<Target>/<culture>/<Target>.locres` (+ `.locmeta`); keys are namespace + key + hash. Subtitle strings may also sit in `SoundWave.Subtitles`. Details: `text/unreal-locres.md`.

## Extraction tools

| Tool | Purpose | Status as of 2026-10 |
|---|---|---|
| FModel / CUE4Parse | viewing and export (`.locres` → JSON, audio), AES key handling | active |
| repak | `.pak` unpack/pack/list, AES; default mount point `../../../`; reads and writes versions 2–5, 7, 8A/8B, 9, 11 (UE 4.0–5.3); versions 1, 6, 10 not verified by the authors | v0.2.3, Apache-2.0 |
| retoc | IoStore: `unpack`, `unpack-raw`, `pack-raw`, `list`, `verify`; mod build with `to-zen` (legacy → IoStore), back with `to-legacy`; there is no separate `pack` command | v0.1.5, MIT |
| UnrealLocres, UnrealLocresEditor, LocRes-Builder, UEExtractor | `.locres` ↔ CSV, editor, text extraction | active (UE4LocalizationsTool is archived) |
| RE-UE4SS | runtime: Lua/C++ mods, function hooks | v3.0.1 + experimental |

## Native rebuild path

- **Mod pak:** folder `ModName_P/<Game>/Content/...` → `repak pack` → file `ModName_P.pak` in `Content/Paks/` or `~mods/`. **The `_P` suffix is mandatory.** For UE ≤ 4.27 with `.sig`, modders copy any `.sig` under the mod's name (per community guides).
- **IoStore games:** the mod is also IoStore (`.pak` + `.utoc` + `.ucas`); build it with `retoc to-zen` or UnrealReZen (per community guides).
- **Audio:** cook the SoundWave in the UE Editor of **the same version** (the editor is free) with the same package path. Audio through Wwise or FMOD is rebuilt per the middleware references.
- **Text:** replace the `.locres` of an existing culture in the `_P` pak. A new culture must be allowed by the game.

## Pitfalls

- AES encryption of paks (the key is extracted from the exe), `.sig` signatures.
- EasyAntiCheat in many UE games → do not modify by default.
- FaceFX and lip-sync curves, subtitle timings in SoundWave.
- The cook version must match the game.

## Links

- FModel — https://github.com/4sval/FModel ; CUE4Parse — https://github.com/FabianFG/CUE4Parse
- repak — https://github.com/trumank/repak ; retoc — https://github.com/trumank/retoc
- UnrealLocres — https://github.com/akintos/UnrealLocres ; UnrealLocresEditor — https://github.com/Snoozeds/UnrealLocresEditor ; LocRes-Builder — https://github.com/matyalatte/LocRes-Builder ; UEExtractor — https://github.com/SolicenTEAM/UEExtractor
- RE-UE4SS — https://github.com/UE4SS-RE/RE-UE4SS
- Dmgvol guides (`BasicModding/UnrealPak.md`, `BasicModding/IoStorePacking.md`) — https://github.com/Dmgvol/UE_Modding
- Epic forum: Loading Content and Pak Files at Runtime — https://forums.unrealengine.com/t/knowledge-base-primer-loading-content-and-pak-files-at-runtime/536669
