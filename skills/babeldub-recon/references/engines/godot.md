# Godot

Read this if the game folder has a `*.pck` with the `GDPC` magic, or an exe with GDPC at its end.

## Level in BabelDub v0.1

**C**: CLI v0.1 does not parse Godot; instructions only. Support is planned later, in v0.3 (level B: needs Godot of the same version, and the AES key for encrypted games).

## How to identify

- A `.pck` next to the exe, or a PCK embedded in the exe (`GDPC` magic).
- AES-256 encryption is possible; the key is embedded in the binary and is extracted with the gdke tool.

## Where the voice is

On export, source files are imported into resources `.godot/imported/*.oggvorbisstr` (`AudioStreamOggVorbis`), `.sample` (WAV), `.mp3str`. The source path leads to them through `.import` files (per community guides).

## Where the text is

See `text/godot.md`: CSV → `.translation` (`OptimizedTranslation` stores hashes instead of keys), `.po`/`.mo`; the list of translations is in the project settings (`project.binary`).

## Extraction tools

| Tool | Purpose | Status as of 2026-10 |
|---|---|---|
| gdsdecomp / GDRE Tools | Godot 2–4: `--recover`, `--extract`, `--pck-create`, `--pck-patch`, `--patch-translations=<CSV>=<res path>`, `--key` | v2.7.4 |
| GodotPckTool | extract, list, add | v2.3 |
| gdke | extracts the PCK encryption key | exists (details not checked) |

## Native rebuild path

- **Text:** GDRE `--patch-translations`.
- **Audio:** import the new OGG files into an empty Godot project of **the same version** (`godot --headless --import`, the engine is open), take the imported resources and replace them in the PCK with GDRE `--pck-patch`. Not verified in practice.

## Pitfalls

- The format of imported resources depends on the Godot version.
- MD5 in the PCK (GDRE has options to ignore it).
- PCK encryption.
- C# (.NET) games are separate builds.

## Links

- gdsdecomp — https://github.com/GDRETools/gdsdecomp
- GodotPckTool — https://github.com/hhyyrylainen/GodotPckTool
- gdke — https://github.com/char-ptr/gdke
