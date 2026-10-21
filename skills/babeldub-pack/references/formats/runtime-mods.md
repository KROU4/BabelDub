# Runtime substitution: mod loaders

Level in v0.1: **C** — a fallback path when the native container cannot be built (Vorbis without FSBank, Wwise without Authoring, encrypted packs) or when the game will not load a new language file by itself. CLI v0.1 does not make such mods; this reference exists so you can describe the options to the user honestly.

Markers: [verified] — a primary source (README, releases); [per guides]; [not verified].

## 1. Tools

| Tool | Purpose | Status |
|---|---|---|
| BepInEx | Unity: 5.x for Mono (stable 5.4.23.5); **6.x for IL2CPP** — only pre-release `6.0.0-pre.2` (2024-08-27) and nightly builds | [verified: releases], LGPL-2.1 |
| MelonLoader | Unity Mono and IL2CPP, v0.7.3 | [verified: releases], Apache-2.0 |
| XUnity.AutoTranslator | intercepts Unity UI text, manual translation files, texture and font substitution; per the README, IL2CPP support is incomplete | [verified: README], MIT |
| RE-UE4SS | Unreal: Lua/C++ mods, function hooks | [verified: repository] |
| audio substitution plugins | Unity: a Harmony patch of `AudioSource` and OGG loading; FMOD: intercepting the programmer sound and `createSound(file)` | different for every game [per guides] |

## 2. Example: an FMOD game on Unity [not verified in a game]

A BepInEx 6 (IL2CPP) or MelonLoader plugin intercepts the programmer sound callback (or `getSoundInfo`) and returns `RuntimeManager.CoreSystem.createSound("BabelDub/<language>/<key>.ogg", CREATESTREAM)`; FMOD Core reads OGG, WAV and FLAC directly.

- Pros: the banks are not touched, the line files sit alongside, and a separate voice language is possible (for example, `ru-RU`, which the game would not load by itself).
- Cons: the user installs the mod loader; a hook into IL2CPP code breaks easily on game updates; BepInEx 6 is still a pre-release.

## 3. When to suggest it

- `babeldub build` refused because of level B/C, and the user does not want to install the required tool (Wwise Authoring, UE Editor).
- A **separate** voice language is needed, and the game picks the voice file in code (like `Dialogue_<language>.bank` in Low-Budget Repairs).
- For texts: on-the-fly translation without editing assets (XUnity), if the subtitles need to change and writing tables is not supported yet.

## 4. Risks — name them to the user

- **Anti-cheat** and online modes: code injection can get the player banned. Offline single-player games only.
- **Antivirus software** reacts to DLL injection.
- **Game updates** break hooks.
- A mod loader plugin is executable code: BabelDub does not write or run such code in v0.1; if the user takes a ready-made plugin, they install it themselves and at their own risk.

## 5. What BabelDub already provides for this path

- The dub in `post/audio/<uid>.wav` (WAV, sample rate and channels as in the original line, loudness matched to it).
- The mapping of lines to engine keys: `babeldub query --fields id,entry.key,entry.event_path,uid --limit 50` (the `entry` field in `inventory/lines.jsonl`).
