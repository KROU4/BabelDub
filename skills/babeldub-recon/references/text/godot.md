# Godot: `.translation`, `.po`, CSV translations

Read this if the game is on Godot (see `engines/godot.md`).

## Level in BabelDub v0.1

**C**: CLI v0.1 does not read Godot texts; instructions only.

## How it works

- CSV is imported into `.translation`. **`OptimizedTranslation` stores hashes instead of keys**, so GDRE recovers keys with heuristics and the `--translation-hint` hint.
- `.po`/`.mo` are loaded as Translation.
- The list of translations is in the project settings (`project.binary`).

## Extraction and rebuild tools

- GDRE Tools (gdsdecomp) v2.7.4: `--recover`, `--extract`; writing translations: `--patch-translations=<CSV>=<res path>`, then `--pck-patch`.
- Encrypted PCK: the key (`--key`) is extracted with the gdke tool.

## Link to the voice

The translation key often equals the audio file name or is used by the game's dialogue plugin; check with a sample. If the keys were recovered by heuristics, the link may be inaccurate.

## Pitfalls

- Recovered `OptimizedTranslation` keys are inaccurate.
- MD5 in the PCK, encryption.
- Fonts without the target alphabet (for example, Cyrillic).

## Links

- gdsdecomp — https://github.com/GDRETools/gdsdecomp
- gdke — https://github.com/char-ptr/gdke
