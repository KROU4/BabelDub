# Wwise (Audiokinetic): `.bnk`, `.wem`, `.pck`

Level in v0.1: **B**. The `wwise` adapter finds and decodes voice media (the lines can be translated and voiced), but `babeldub build` refuses: only Wwise Authoring can encode WAV into `.wem`. Recon — the `babeldub-recon` skill, `references/audio/wwise.md`.

Markers: [verified] — a primary source; [per guides] — a secondary source, including working code of third-party projects; [not verified].

## 1. Structure

- `.bnk` — chunks `BKHD` (version, bank ID), `DIDX` (media ID, offset, size), `DATA` (embedded `.wem` or the beginning of streamed ones), `HIRC` (hierarchy of events, sounds, codec parameters) and others [per guides: vgmstream, wwiser].
- `.wem` — RIFF/RIFX `WAVE`; the codec is in `fmt`: Wwise Vorbis (`0xFFFF`, with its own codebooks), ADPCM, PCM, Opus and others [verified: vgmstream].
- `.pck` (`AKPK`) — a package with a language map and tables of banks and streams; localized games have separate packages or folders per language; the language is selected at runtime [per guides].
- IDs are 32-bit FNV-1 hashes of lower-case names; names are recovered from `SoundbanksInfo.xml/.json` (if the game ships it) or from wwiser name lists [per guides].

## 2. What CLI v0.1 does

- Extracts lines from `.bnk` (embedded media), `.pck` (streams, external sources, banks inside the package) and loose `.wem`; decodes PCM itself, everything else through `vgmstream-cli`.
- Synthesis, post-processing and QA work as usual: the dub in the hero's voice is ready in `post/audio/`.
- `build` refuses (exit code 2) with a hint pointing to this reference.

## 3. Manual rebuild path [per guides]

For a user who is ready to install Wwise:

1. **WAV → `.wem`** — only Wwise Authoring of the same version as the game, with the same Conversion Settings (codec, quality, sample rate). Downloading through the Audiokinetic Launcher requires an account. Command line: `WwiseConsole convert-external-source <project.wproj> --source-file <list.wsources> --output <folder>`; the syntax is confirmed only by the working code of the ZZAR project, not checked against the official documentation [not verified].
2. **Replacing `.wem` in `.bnk` or `.pck`** — open-source utilities: wwiseutil (allows replacements of any size and updates the metadata, but has not been updated since 2018), BNKReplacer, PckTool, mhws-sound-tool, RingingBloom (Capcom games) [per guides].
3. Change game files only with a backup. Since the CLI does not drive this path, `babeldub uninstall` will not roll such replacements back: backup and rollback are on the user, or through the launcher's integrity check.

## 4. Pitfalls [per guides]

- The replacement codec must match the original one: the source and codec type are recorded in `HIRC`.
- Streamed sounds with prefetch: the beginning sits both in the bank's `DATA` and in the `.pck` — update both.
- The same IDs appear in several packages (in some games — in `SoundBank_*` and `Streamed_*`).
- Markers and cues inside `.wem` sometimes drive subtitles or lip sync.
- Duration: ordinary Play actions do not limit it, but music segments and cutscene timings do.

## 5. What to tell the user

- The dub is ready, but BabelDub v0.1 cannot install it into the game automatically: Wwise Authoring of the right version is needed.
- The alternative without Wwise is runtime substitution with a mod loader, if one exists for the game's engine ([runtime-mods.md](runtime-mods.md)).
- The dub files are in `post/audio/<uid>.wav`; the mapping of lines to media is in `inventory/lines.jsonl` (the `entry` field), view it with `babeldub query`.
