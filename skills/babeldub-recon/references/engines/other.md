# Custom or unrecognized engine

Read this if no sign from the table in `SKILL.md` matched, or `scan` returned an unknown engine.

## Level in BabelDub v0.1

It depends on the audio, not on the engine:

- FMOD banks (`*.bank`) → adapter `fmod` (see `audio/fmod.md`; not tested on non-Unity games);
- Wwise (`*.bnk`, `*.wem`, `*.pck`) → adapter `wwise` (see `audio/wwise.md`);
- separate audio files → adapter `loose` (see `audio/loose.md`);
- custom archives → **C**: description in the report only.

## Recon order

1. List the root and the first levels of the game folder: names, extensions, sizes. Do not read whole binaries.
2. Look for middleware: `fmod*.dll`, `fmodstudio.dll`, `AkSoundEngine*.dll`, `cri_*.dll`, and files `*.bank`, `*.fsb`, `*.bnk`, `*.wem`, `*.pck`, `*.acb`, `*.awb`. Audio middleware is often identified before the engine.
3. Look for the voice by folder and file names: `voice`, `vo`, `dialog`, `speech`, `dub`, language codes (`en`, `en-US`, `english`, `ru`) in the names.
4. Look for texts: `*.csv`, `*.json`, `*.xml`, `*.po`, `*.txt`, `*.loc*`, `*.lang` with language codes in the name; see `text/generic.md`.
5. Video: `audio/video.md`.
6. Write into the report: the containers found, their sizes, languages, and format hypotheses marked "not verified".

## Runtime path

If the native container cannot be rebuilt (custom format, encryption), what remains is runtime replacement with a mod loader (BepInEx, MelonLoader, UE4SS, depending on the engine) or an audio-replacement plugin. This is level C: the plugin is written for a specific game. Downsides: the mod loader, fragility on game updates, anti-cheat, antivirus reactions.

## What not to do

- Do not run the game exe for "behavioral recon".
- Do not load the game's DLLs.
- Do not unpack game archives inside the game folder.
- Do not guess the format: report the unknown as unknown.
