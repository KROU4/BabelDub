# RPG Maker (MV, MZ, XP, VX, VX Ace)

Read this if the game folder has `www/js/rpg_core.js`, `js/rmmz_core.js`, `data/System.json`, `Game.rgss3a`/`.rgss2a`/`.rgssad` or `RGSS*.dll`.

## Level in BabelDub v0.1

**C**: CLI v0.1 does not parse RPG Maker; instructions only. Plan: later; text at level A (via rvpacker-txt-rs), voice at level B (depends on the game's plugins). If unencrypted audio files sit separately, `babeldub scan` may show them through the `loose` adapter.

## How to identify

| Sign | Version |
|---|---|
| `www/js/rpg_core.js`, `www/data/*.json` | MV |
| `js/rmmz_core.js` at the root, `data/*.json` | MZ |
| `Game.rgss3a`, `RGSS3*.dll` | VX Ace |
| `Game.rgss2a` | VX |
| `Game.rgssad` | XP |

## Where the voice is

- MV/MZ: `audio/{bgm,bgs,me,se}/`. Encrypted variants: `.rpgmvo`/`.rpgmvm` (MV), `.ogg_`/`.m4a_` (MZ). The key is in `data/System.json` (`encryptionKey`, flags `hasEncryptedAudio`/`hasEncryptedImages`); the cipher is a 16-byte `RPGMV…` header plus XOR of the first 16 bytes (per community guides).
- **There is no built-in voice system.** The voice is usually played as an SE before the message, or by a plugin; the "sound ↔ line" link is found from the order of event commands.
- XP/VX/VX Ace: `Audio/*` (voice is rare).

## Where the text is

MV/MZ: `data/*.json` (event commands; code 401 is "Show Text"); XP/VX/VX Ace: `Data/*.rvdata2` (Ruby Marshal) inside the archive. Details: `text/rpgmaker.md`.

## Extraction tools

| Tool | Purpose | Status as of 2026-10 |
|---|---|---|
| RPGMakerDecrypter | decrypts XP/VX/VX Ace/MV/MZ, `--recreate-project` | active |
| rvpacker-txt-rs | text 2000/2003/XP/VX/VX Ace/MV/MZ ↔ `.txt`, `read`/`write`, encodings | active |
| rpgmtranslate | GUI on top of rvpacker | active |
| rpgm-asset-decrypter-rs | decrypts assets | active |

## Native rebuild path

- Text: JSON or rvdata2 via `rvpacker-txt-rs write`.
- Audio: encrypt the new OGG with the same key, or drop in an unencrypted file and turn off the flag; depends on the engine and plugins (not verified).
- Older engines: an unpacked `Data/` folder with the archive removed (per community guides).

## Pitfalls

- Asset encryption (the key is in `System.json`).
- Voice depends on the specific game's plugins.
- Text encodings in older versions.

## Links

- RPGMakerDecrypter — https://github.com/uuksu/RPGMakerDecrypter
- rvpacker-txt-rs — https://github.com/RPG-Maker-Translation-Tools/rvpacker-txt-rs
- rpgmtranslate — https://github.com/RPG-Maker-Translation-Tools/rpgmtranslate
- rpgm-asset-decrypter-rs — https://github.com/RPG-Maker-Translation-Tools/rpgm-asset-decrypter-rs
