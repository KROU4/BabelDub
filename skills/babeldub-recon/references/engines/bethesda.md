# Bethesda Creation Engine (Oblivion, Fallout 3/NV/4/76, Skyrim, Starfield)

Read this if the game folder has `Data/*.bsa` or `*.ba2`, `*.esm`, `Data/Sound/Voice/`.

## Level in BabelDub v0.1

**C**: CLI v0.1 does not parse these formats. Planned later at level B/C: lip sync needs `FonixData.cdf` from the Creation Kit.

## How to identify

- BSA archives (Oblivion, FO3/NV, Skyrim LE/SSE) and BA2 archives (FO4, FO76, Starfield).
- Loose files in `Data/` override the archives (per community guides).

## Where the voice is

- `Data/Sound/Voice/<Plugin>/<VoiceType>/<topic>_<FormID>_<n>.fuz`.
- FUZ is a `FUZE` container holding `.lip` (lip sync) and `.xwm` (xWMA) (per community guides; the byte layout of the header is not verified).
- Several NPCs share one voice type: a character is not the same as a voice folder.

## Where the text is

Localized plugins: `Data/Strings/<plugin>_<language>.STRINGS`, `.DLSTRINGS`, `.ILSTRINGS`. The standard community editor is xTranslator.

## Extraction tools

BSArch (xEdit), Bethesda Archive Extractor, Unfuzer, Yakitori Audio Converter (per community guides).

## Native rebuild path

- Loose files in `Data/Sound/Voice/...` with the same names.
- `.xwm`: `xWMAEncode` from the DirectX SDK (per community guides).
- `.lip`: the Creation Kit or FaceFXWrapper ("generate native LIP files … without … Creation Kit"; Skyrim and Fallout4 types, language `USEnglish`; needs `FonixData.cdf` from the Creation Kit).
- How good the lip sync is for non-English speech (for example, Russian) with the English phoneme model is not verified.
- A community pipeline example: Mantella generates native `.lip`/`.fuz` via FaceFX/LipGen; xVASynth makes AI voices for mods.

## Pitfalls

- Precomputed lip sync: without new `.lip` files the lips will not match the speech.
- A voice type is shared by many NPCs.
- The line duration is stored in related files.

## Links

- FaceFXWrapper — https://github.com/Nukem9/FaceFXWrapper
- xVA-Synth — https://github.com/DanRuta/xVA-Synth
- Mantella — https://github.com/art-from-the-machine/Mantella
- CK wiki: batch voice generation — https://ck.uesp.net/wiki/How_to_generate_voice_files_by_batch
