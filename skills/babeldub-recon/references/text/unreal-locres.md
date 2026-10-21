# Unreal: `.locres` texts

Read this if the game is on Unreal Engine 4/5 (see `engines/unreal.md`).

## Level in BabelDub v0.1

**C**: CLI v0.1 does not read `.locres`. Planned for v0.3 at level B (an encrypted pak needs the AES key).

## Where the text is

- `Content/Localization/<Target>/<culture>/<Target>.locres` (+ `.locmeta`) inside a `.pak` or IoStore (`.utoc`/`.ucas`).
- Keys are namespace + key + hash.
- Subtitle strings may also sit in `SoundWave.Subtitles` (with timings inside the sound asset).

## Extraction tools

| Tool | Purpose |
|---|---|
| FModel / CUE4Parse | browses paks, exports `.locres` → JSON, AES keys |
| UnrealLocres | `.locres` ↔ CSV |
| UnrealLocresEditor | `.locres` editor |
| LocRes-Builder | builds `.locres` |
| UEExtractor | extracts text from pak/ucas |

UE4LocalizationsTool is archived.

## Native path

Replace the `.locres` of an existing culture and put it into a mod pak with the `_P` suffix (`repak pack` for `.pak`, `retoc to-zen` for IoStore). A new culture must be allowed by the game (culture list or settings).

## Link to the voice

`DialogueWave`, SoundWave names or Wwise/FMOD events next to the string key in the dialogue data. There may be no direct shared key: search by sampling.

## Pitfalls

- AES encryption of paks, `.sig` signatures.
- Fonts without Cyrillic.
- Subtitle timings in SoundWave when line lengths change.

## Links

- FModel — https://github.com/4sval/FModel ; CUE4Parse — https://github.com/FabianFG/CUE4Parse
- UnrealLocres — https://github.com/akintos/UnrealLocres ; UnrealLocresEditor — https://github.com/Snoozeds/UnrealLocresEditor
- LocRes-Builder — https://github.com/matyalatte/LocRes-Builder ; UEExtractor — https://github.com/SolicenTEAM/UEExtractor
- repak — https://github.com/trumank/repak ; retoc — https://github.com/trumank/retoc
