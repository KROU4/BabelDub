# RPG Maker: `data/*.json` and `*.rvdata2` texts

Read this if the game is on RPG Maker (see `engines/rpgmaker.md`).

## Level in BabelDub v0.1

**C**: CLI v0.1 does not read RPG Maker texts; instructions only. Planned later at level A via rvpacker-txt-rs.

## Where the text is

- **MV/MZ:** `data/*.json`: maps (`Map*.json`), common events (`CommonEvents.json`), troops (`Troops.json`) and the database. Dialogue lines are event commands with code **401 "Show Text"** (per community guides).
- **XP/VX/VX Ace:** `Data/*.rvdata2` (Ruby Marshal), usually inside the `Game.rgss3a`/`.rgss2a`/`.rgssad` archive.

## Extraction and rebuild tools

| Tool | Purpose |
|---|---|
| rvpacker-txt-rs | RPG Maker 2000/2003/XP/VX/VX Ace/MV/MZ text ↔ `.txt`: `read` and `write`, encodings |
| rpgmtranslate | GUI on top of rvpacker |
| RPGMakerDecrypter | unpacks archives of older versions, `--recreate-project` |

Native path: `rvpacker-txt-rs write`; for older engines, an unpacked `Data/` with the archive removed (per community guides).

## Link to the voice

There is no built-in voice system: a line's sound is usually played by an SE command before the message, or by a plugin. The link is found from the order of commands in the event (SE → Show Text).

## Pitfalls

- Text encodings in older versions.
- The game's plugins change how text and sound are output.
- Fonts without Cyrillic.

## Links

- rvpacker-txt-rs — https://github.com/RPG-Maker-Translation-Tools/rvpacker-txt-rs
- rpgmtranslate — https://github.com/RPG-Maker-Translation-Tools/rpgmtranslate
- RPGMakerDecrypter — https://github.com/uuksu/RPGMakerDecrypter
