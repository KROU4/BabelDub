# Other text sources (CSV, JSON, XML, dialogue systems, custom formats)

Read this if the game's texts are not in Unity Localization, I2, `.locres`, Godot, Ren'Py or RPG Maker.

## Level in BabelDub v0.1

In v0.1 the CLI reads texts only through the adapters reported by `babeldub scan` (`text_assets[]`). If there are no texts there, `babeldub link` can link lines by file names at most, and lines without text keep the original voice. A custom format is level **C**: describe it in the report.

## Where to look

- Files with language codes in the name or path: `en`, `en-US`, `english`, `ru`, `ru-RU`, `russian`, native language names.
- Extensions: `*.csv`, `*.tsv`, `*.json`, `*.xml`, `*.po`, `*.txt`, `*.ini`, `*.loc*`, `*.lang`, `*.strings`.
- Dialogue systems in Unity: Yarn, Ink, Pixel Crushers Dialogue System, Articy; their data sits in a TextAsset or MonoBehaviour (UnityPy).
- Game data with `audio` and `text` fields side by side (dialogue graphs, ScriptableObjects).
- GameMaker: the `STRG` chunk in `data.win`, `datafiles/*.json|csv|ini` (see `engines/gamemaker.md`).
- Source: `resource/closecaption_<language>.txt` (see `engines/source.md`).
- Bethesda: `Data/Strings/*.STRINGS|DLSTRINGS|ILSTRINGS` (see `engines/bethesda.md`).

## How to link text to voice

1. Shared key: the audio file or entry name = the string key (the most common case).
2. A key in the sound event metadata.
3. The game's dialogue table with sound and text fields.
4. Speech recognition of the original plus fuzzy matching (not in CLI v0.1).

## What to check in the texts found

- Whether there is an **official translation** into the target language: then use mode `official` or `adapt`, and take the terminology from it.
- Placeholders and markup: `{0}`, `{name}`, `%s`, `%d`, `\n`, `<color=…>`, `[tag]`, the game's own tags.
- Encoding (UTF-8, UTF-16 in Source, legacy encodings in RPG Maker).
- Game fonts: whether they have glyphs of the target alphabet (matters only when writing text; in v0.1 the texts in the game do not change).

## What not to do

- Do not unpack game archives inside the game folder.
- Do not rewrite the game's text files: in v0.1 the texts in the game do not change.
