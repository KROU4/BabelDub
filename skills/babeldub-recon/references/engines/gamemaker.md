# GameMaker

Read this if the game folder has `data.win`, `audiogroup*.dat`, `options.ini`.

## Level in BabelDub v0.1

**C**: CLI v0.1 does not parse GameMaker; instructions only. Planned later at level B (UndertaleModTool scripts must be adapted for batch runs).

## How to identify

`data.win` is an IFF `FORM` container with chunks GEN8, OPTN, LANG, EXTN, SOND, AGRP, SPRT, BGND, PATH, SCPT, GLOB, SHDR, FONT, TMLN, OBJT, ROOM, TPAG, CODE, VARI, FUNC, STRG, TXTR, **AUDO** and others (per community guides).

## Where the voice is

- The `AUDO` chunk in `data.win`.
- `audiogroupN.dat`: its own `FORM` with `AUDO`.
- External `.ogg` files for streams.

## Where the text is

The `STRG` chunk in `data.win`, or included files (`datafiles/*.json`, `*.csv`, `*.ini`). Fonts are often bitmap fonts: a new alphabet (for example, Cyrillic) has to be imported. See `text/generic.md`.

## Extraction and rebuild tools

**UndertaleModTool** 0.9.2.0, with the CLI `UndertaleModCli`. Scripts:

- strings: the pairs `ExportAllStrings.csx` / `ImportAllStrings.csx` (TXT) and `ExportAllStringsJSON.csx` / `ImportAllStringsJSON.csx` (JSON);
- audio: `ExportAllSounds`; `ImportSounds` and `ImportSingleSound` (external or embedded OGG, audiogroup selection; "WAV cannot be external").

The scripts are interactive (`ScriptQuestion`, `PromptLoadFile`): to run them in batch from an agent through `UndertaleModCli` they would have to be adapted (not verified).

## Pitfalls

- YYC builds: the code is native and cannot be decompiled, but `STRG` and `AUDO` are accessible (per community guides).
- Bytecode version.
- Bitmap fonts without the target alphabet (for example, Cyrillic).

## Links

- UndertaleModTool — https://github.com/UnderminersTeam/UndertaleModTool
