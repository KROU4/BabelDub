# Ren'Py: scripts and `game/tl/` translations

Read this if the game is on Ren'Py (see `engines/renpy.md`).

## Level in BabelDub v0.1

**C**: CLI v0.1 does not read Ren'Py scripts; instructions only. Ren'Py is a candidate for level A in v0.2: the engine has a native mechanism for translating both text and voice.

## How it works (per the Ren'Py documentation)

- Dialogue lines are in `game/*.rpy` (compiled `*.rpyc`, sometimes inside `.rpa`).
- Translation: `game/tl/<language>/*.rpy`:
  - `translate <language> <id>:` translates a dialogue line by its identifier;
  - `translate <language> strings:` translates interface strings;
  - `translate <language> python:` holds code for the language (for example, a custom font).
- Ren'Py SDK: Launcher → **Generate Translations** (`tl/` templates) and **Extract Dialogue** (line identifiers).

## Link to the voice

- `voice "file.ogg"` before a line is an explicit link.
- `config.auto_voice`: the voice file name is built from the line identifier (`{id}`). Then the identifier is the key of both the text and the sound.
- File translation: a file under `game/tl/<language>/` with the same relative path is used instead of the original when that language is active. The dubbed voice goes there, without touching the original.

## Extraction tools

- unrpa or rpatool: unpack `.rpa`.
- unrpyc: decompile `.rpyc` when the source `.rpy` files are missing (compatibility depends on the Ren'Py version).

## Pitfalls

- Modified RPA archives.
- Fonts without Cyrillic: set the font style in `translate <language> python:`.

## Links

- Ren'Py Translation — https://www.renpy.org/doc/html/translation.html
- Ren'Py Voice — https://www.renpy.org/doc/html/voice.html
- unrpyc — https://github.com/CensoredUsername/unrpyc ; unrpa — https://github.com/Lattyware/unrpa ; rpatool — https://codeberg.org/shiz/rpatool
