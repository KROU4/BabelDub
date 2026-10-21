# Godot: `.pck` and imported resources

Level in v0.1: **C** — CLI v0.1 has no Godot adapter. Recon — the `babeldub-recon` skill, `references/engines/godot.md` and `references/text/godot.md`.

Markers: [verified] — a primary source (tool READMEs); [per guides]; [not verified].

## 1. Structure [per guides]

- The `.pck` container (magic `GDPC`) sits next to the executable or inside it; it may be AES-256 encrypted with the key inside the executable.
- Audio after export consists of imported resources: `.oggvorbisstr` (`AudioStreamOggVorbis`), `.sample` (WAV), `.mp3str`; the source path leads to them through `.import`.
- Texts: CSV is imported into `.translation`; `OptimizedTranslation` stores hashes instead of keys; `.po/.mo` load as Translation; the list of translations is in the project settings (`project.binary`).

## 2. Tools [verified: README]

- **GDRE Tools (gdsdecomp)**: Godot 2–4; `--recover`, `--extract`, `--pck-create`, **`--pck-patch`**, **`--patch-translations=<CSV>=<resource path>`**, `--translation-hint` (key recovery), `--key` (AES).
- **GodotPckTool**: extract, list, add files to a `.pck`.
- The encryption key is extracted with a separate utility (gdke) [per guides] — the user does this.

## 3. Native build (manual path) [not verified]

1. **Audio:** import the new OGG files in an empty Godot project of **the same version** (`godot --headless --import`; the engine is open source), take the resulting imported resources and replace them in the `.pck` with `gdre --pck-patch`.
2. **Text:** `gdre --patch-translations`.
3. Install the modified `.pck` only with a backup of the original; `babeldub uninstall` will not roll back such manual replacements.

## 4. Pitfalls [per guides]

- The format of imported resources depends on the engine version.
- MD5 checksums in the `.pck` (GDRE has switches to ignore them).
- Encryption.
- C# (.NET) builds are structured differently.

## 5. What to tell the user

The dub is ready in `post/audio/`, but for Godot v0.1 does not build the `.pck` automatically: a Godot of the same version is needed to import the audio, and GDRE Tools to patch. If the game reads audio from loose files, check whether the `loose` adapter picked them up (`babeldub scan`).
