# Ren'Py

Read this if the game folder has `game/`, `renpy/`, `*.rpa`, `*.rpyc`.

## Level in BabelDub v0.1

**C**: v0.1 has no dedicated Ren'Py adapter. If the voice files sit in `game/` as separate files (not in `.rpa`), `babeldub scan` may show them through the `loose` adapter; go by the `level` in the scan output. Plan: a candidate for level A in v0.2, because Ren'Py has a native voice translation mechanism.

## How to identify

`game/`, `renpy/`, archives `game/*.rpa`, scripts `*.rpy` and compiled `*.rpyc`.

## Where the voice is

- Usually `game/voice/*.ogg` or inside `.rpa`.
- The `voice "file.ogg"` statement in scripts; `config.voice_filename_format`; **`config.auto_voice`**: the file name is built from the line identifier (`{id}` is replaced with the dialogue identifier).

## Where the text is

Scripts `*.rpy`/`*.rpyc` and translations in `game/tl/<language>/`; see `text/renpy.md`.

## Extraction tools

| Tool | Purpose | Status as of 2026-10 |
|---|---|---|
| unrpa | unpacks `.rpa` | 2.3.0 (2019), getting outdated |
| rpatool | unpacks and packs `.rpa` | moved to Codeberg |
| unrpyc | decompiles `.rpyc` | v2.0.4 |
| Ren'Py SDK | Launcher → Generate Translations and Extract Dialogue (line identifiers for `auto_voice`) | official |

RPA format (a header with an XOR key and a zlib-pickle index): details not checked.

## Native rebuild path (from the Ren'Py documentation, verified against the primary source)

- Translation: `game/tl/<language>/*.rpy` with `translate <language> <id>:` and `translate <language> strings:` blocks.
- **File translation:** the file `game/tl/<language>/<path>` is used instead of `game/<path>` when that language is active. Example from the documentation: `game/tl/french/omelette.ogg` instead of `game/omelette.ogg`.
- Auto-voice works the same way if the path under `tl/<language>` mirrors the original. So the dub goes next to the originals without touching them.

## Pitfalls

- unrpyc compatibility with the Ren'Py version.
- Modified RPA archives.
- Fonts without the target alphabet (for example, Cyrillic): set the font style in a `translate <language> python:` block.

## Links

- Ren'Py Voice — https://www.renpy.org/doc/html/voice.html
- Ren'Py Translation — https://www.renpy.org/doc/html/translation.html
- unrpa — https://github.com/Lattyware/unrpa ; rpatool — https://codeberg.org/shiz/rpatool ; unrpyc — https://github.com/CensoredUsername/unrpyc
