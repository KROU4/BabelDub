# Unity: Addressables, Unity Localization, AudioClip

Level in v0.1: **read-only**. The `unity-localization` adapter reads string tables (texts and the official translation) but **does not write** them: `babeldub build` refuses for such assets with exit code 2 and a link here. Voice in Unity games with FMOD or Wwise is built by the adapters of that middleware ([fmod.md](fmod.md), [wwise.md](wwise.md)). Recon — the `babeldub-recon` skill, `references/engines/unity.md` and `references/text/unity-localization.md`.

Markers: [verified] — a primary source or an experiment; [per guides]; [not verified].

## 1. Texts: Unity Localization

- String tables live in Addressables bundles: `SharedTableData` (keys: `m_Id` → `m_Key`) and one `StringTable` per locale (`m_LocaleId.m_Code`, entries `m_Id` → `m_Localized`) [verified from the `com.unity.localization` 1.5 source code].
- **Changing the text of an existing language** is simple: find the `StringTable` of the right locale and replace `m_Localized`. Editing a table with UnityPy 1.25.4 passed a write-then-read round trip on the test game [verified]; how the game accepts the modified bundle — [not verified].
- **Adding a new language** is much harder: it needs a `Locale` asset, tables for every collection and new entries in the Addressables catalog. It is more practical to replace the contents of an existing locale the player does not need [not verified].

In v0.1 the game's subtitles do not change: the player sees the official (or source) text and hears the dub. Writing tables and zeroing the CRC are planned for v0.2.

## 2. CRC in the Addressables catalog

- Addressables 1.22.3 loads a local bundle with `AssetBundle.LoadFromFileAsync(path, crc)`; if the CRC in the catalog is non-zero, Unity checks it against the contents and refuses to load a mismatched bundle [verified from Unity source code and documentation]. In the test game all 55 bundles have a non-zero CRC [verified].
- So after editing a bundle, `m_Crc` in `catalog.json` must be zeroed or recomputed (in a 1.x catalog the field sits inside the base64 string `m_ExtraDataString`).
- Tool: AddressablesTools, the `patchcrc` command — zeroes `m_Crc` for all entries; version 3 replaces `catalog.json` itself and keeps the original as `catalog.json.old` [verified from the v3 source code]. It also works with the binary `catalog.bin` (Addressables 2.x, Unity 6), but writing some of its versions is marked by the authors as insufficiently tested [per guides].
- The exact algorithm for recomputing the CRC "over the uncompressed contents" has not been researched [not verified].

Manual path until v0.2 (only if the user explicitly asks to update the subtitles and understands the risk): edit the `StringTable` with UnityPy in a copy of the bundle → `patchcrc` on a copy of the catalog → install both files with a backup. In v0.1 the agent does **not do this itself**: only the CLI writes to the game folder.

## 3. Voice in Unity's built-in audio (AudioClip)

- An AudioClip stores audio as FSB5 in `.resource` / `.resS` or inside the bundle; the codec format is set by the `m_CompressionFormat` field [verified via UABEA].
- **There is no ready-made open importer**: UnityPy and UABEA only export audio [verified].
- Options [not verified, an experiment on a game is needed]:
  1. our own PCM FSB5 into the resource and editing the AudioClip fields (`m_Resource`, `m_CompressionFormat`, `m_Frequency`, `m_Channels`, `m_Length`) with UnityPy;
  2. a Unity Editor of the same version: import the WAV, build a bundle and move the FSB and the fields into the game file [per modder guides];
  3. runtime substitution with a mod loader ([runtime-mods.md](runtime-mods.md)).
- None of this is automated in v0.1: describe the options to the user in the report.

## 4. Pitfalls

- **IL2CPP**: MonoBehaviour types are recovered from the bundle's TypeTree; if it is missing, a TypeTree generator working from `GameAssembly.dll` and `global-metadata.dat` is needed — the CLI warns about this honestly.
- **Fonts**: TextMeshPro SDF atlases often lack Cyrillic; a new alphabet needs a replacement Font Asset or a fallback [per guides]. Matters only when editing texts.
- **An integrity check** restores both the bundles and the catalog.
