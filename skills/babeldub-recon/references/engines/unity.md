# Unity

Read this if `scan` reported Unity, or the game folder has `<Game>_Data/`, `UnityPlayer.dll`.

## Level in BabelDub v0.1

| What | Level | Comment |
|---|---|---|
| voice in FMOD Studio banks | **A** | adapter `fmod`, see `audio/fmod.md` |
| voice in Wwise | see `audio/wwise.md` | `.wem` encoding only in Wwise Authoring |
| Unity Localization texts | reading | adapter `unity-localization` reads the tables for `link`; writing into the game comes in later versions |
| I2 Localization | C | see `text/i2-localization.md` |
| built-in Unity audio (AudioClip) | C | no open importer; needs an experiment (planned for v0.3) |

## How to identify

- `<Game>_Data/`, `UnityPlayer.dll`, `globalgamemanagers`: Unity. The Unity version is in the `globalgamemanagers` header or readable via UnityPy.
- `GameAssembly.dll` + `<Game>_Data/il2cpp_data/Metadata/global-metadata.dat`: IL2CPP.
- `<Game>_Data/Managed/Assembly-CSharp.dll`: Mono (decompiles easily; mod loader BepInEx 5).
- `StreamingAssets/aa/catalog.json` or `catalog.bin`, `settings.json`: Addressables; the version is in `settings.json` → `m_AddressablesVersion`.

## Where the voice is

1. **Audio middleware** (common in voiced games): FMOD (`*.bank` in `StreamingAssets`, `fmodstudio.dll` in `Plugins/x86_64`) or Wwise (`*.bnk`, `*.wem`, `*.pck`). Then the voice is covered by the matching `audio/` reference.
2. **Built-in Unity audio: AudioClip.** Fields `m_LoadType`, `m_Channels`, `m_Frequency`, `m_BitsPerSample`, `m_Length`, `m_Resource{m_Source, m_Offset, m_Size}`, `m_CompressionFormat` (PCM, Vorbis, ADPCM, MP3, VAG, HEVAG, XMA, AAC, GCADPCM, ATRAC9: values 0…9). The data itself is FSB5, stored in a `.resource` (`resources.resource`, `sharedassetsN.resource`) or in a bundle.
3. **Addressables and AssetBundles:** UnityFS bundles (LZ4/LZMA) containing `CAB-…` (SerializedFile) and `CAB-….resS/.resource`.

Other files in `<Game>_Data/`: `globalgamemanagers(.assets)`, `level*` (scenes), `sharedassetsN.assets`, `resources.assets`, `.resS` (textures and meshes), `StreamingAssets/` (raw files).

## Where the text is

- Unity Localization: `text/unity-localization.md`.
- I2 Localization: `text/i2-localization.md`.
- TextAsset, MonoBehaviours with dialogue tables (Yarn, Ink, Pixel Crushers and the like): `text/generic.md`.

## Extraction tools

| Tool | Purpose | Status as of 2026-10 |
|---|---|---|
| UnityPy | Python: reads and writes bundles and SerializedFile, edits typetrees; AudioClip **export only** | 1.25.4, MIT; used by the CLI |
| AssetRipper | exports the project and assets (audio to wav/ogg) | 2.0.0, GPL-3.0 |
| UABEA / UABEANext | GUI: asset editing, dumps; AudioClip **export only** | UABEA v8, UABEANext nightly |
| AssetsTools.NET | C# read/write library (the base of UABEA) | v24, MIT |
| AddressablesTools | reads and writes `catalog.json`/`.bin`; `patchcrc` zeroes `m_Crc` | v3; no license stated on GitHub |
| AssetStudioMod | viewing and export | v0.19.0 |
| Il2CppDumper, Cpp2IL | recover IL2CPP types and logic (to find how the game picks the language and the bank) | Il2CppDumper v6.7.46, Cpp2IL active |

## Native rebuild path

- **Voice in FMOD or Wwise:** follow the middleware reference.
- **Texts:** UnityPy `obj.patch(tree)` → `env.file.save(packer="lz4")` (round trip verified on a test game; in-game behavior not verified). After changing a bundle, zero or recompute `m_Crc` in the Addressables catalog (see below).
- **AudioClip** (not verified, outside v0.1):
  1. a custom PCM FSB5 in the resource plus AudioClip field edits via UnityPy (`m_Resource.m_Offset/m_Size`, `m_CompressionFormat=0`, `m_BitsPerSample=16`, `m_Frequency`, `m_Channels`, `m_Length`). Hypothesis: Unity audio is built on FMOD Core, but this was not tested on a game;
  2. the Unity Editor of the same version (free): import WAV, build an AssetBundle and transplant the FSB and AudioClip fields; a modder technique (per community guides);
  3. runtime: BepInEx or MelonLoader + a Harmony patch of `AudioSource.Play` that loads OGG.

### CRC in Addressables

- Addressables 1.22.3 loads a local bundle via `AssetBundle.LoadFromFileAsync(path, crc)`; with a non-zero CRC, a mismatch makes the load fail.
- The CRC is stored in `catalog.json` (1.x): the `m_ExtraDataString` field is base64 holding `AssetBundleRequestOptions` (`m_Hash`, `m_Crc`, `m_BundleName`, `m_BundleSize` …).
- Workaround: `AddressablesTools` `Example patchcrc catalog.json` sets `m_Crc=0` on all entries; v3 replaces `catalog.json` itself and keeps the original as `catalog.json.old`. Zeroing only the changed bundles is possible by the format but not verified.
- Addressables 2.x (Unity 6) writes a binary `catalog.bin` by default; AddressablesTools supports it (writing is marked "not been thoroughly tested").

## Runtime path (fallback)

No asset rebuild: a mod-loader plugin replaces the audio when it is loaded or played.

- BepInEx 5.x for Mono (stable 5.4.23.5); BepInEx 6 for IL2CPP is pre-release only (`v6.0.0-pre.2`, 2024) plus bleeding-edge builds.
- MelonLoader: Mono and IL2CPP, v0.7.3.
- XUnity.AutoTranslator: intercepts UI text; IL2CPP support is incomplete (per its README).
- Downsides: the user installs the mod loader, fragility on game updates, anti-cheat, antivirus reactions.

## Pitfalls

- Addressables CRC when editing bundles.
- TextMeshPro fonts (SDF atlases) often lack Cyrillic glyphs: translating into a new alphabet requires replacing the Font Asset or adding a fallback.
- Adding a new language to Unity Localization is hard (a `Locale` asset, tables, catalog entries); replacing the contents of an existing locale is more practical (not verified).
- The game picks the language bank or table in code: which file to replace is sometimes visible only after decompiling IL2CPP.

## Example: Low-Budget Repairs

Unity 2022.3.62f3, IL2CPP; FMOD Studio 2.03.6 (`fmodstudio.dll` in `Plugins/x86_64`); Unity Localization via Addressables 1.22.3 (55 bundles, all with `m_Crc ≠ 0`). The voice is in `StreamingAssets/Dialogue_en-US.bank` and `Dialogue_pl-PL.bank`; the game loads `Dialogue_<voice language>.bank`, and the menu hard-codes only `en-US` and `pl-PL`. So v0.1 replaces the contents of `Dialogue_en-US.bank`, with a backup.

## Links

- UnityPy — https://github.com/K0lb3/UnityPy
- AssetRipper — https://github.com/AssetRipper/AssetRipper
- UABEA — https://github.com/nesrak1/UABEA ; UABEANext — https://github.com/nesrak1/UABEANext ; AssetsTools.NET — https://github.com/nesrak1/AssetsTools.NET
- AddressablesTools — https://github.com/nesrak1/AddressablesTools
- Addressables 1.22.3 `AssetBundleProvider.cs` — https://github.com/needle-mirror/com.unity.addressables/blob/1.22.3/Runtime/ResourceManager/ResourceProviders/AssetBundleProvider.cs
- `AssetBundle.LoadFromFileAsync` — https://docs.unity3d.com/ScriptReference/AssetBundle.LoadFromFileAsync.html
- AssetStudioMod — https://github.com/aelurum/AssetStudioMod
- Il2CppDumper — https://github.com/Perfare/Il2CppDumper ; Cpp2IL — https://github.com/SamboyCoding/Cpp2IL
- BepInEx — https://github.com/BepInEx/BepInEx ; MelonLoader — https://github.com/LavaGang/MelonLoader ; XUnity.AutoTranslator — https://github.com/bbepis/XUnity.AutoTranslator
