# Security

## Supported versions

Security fixes are released for the latest BabelDub version (currently 0.1.x). Update through your host (`/plugin update`, `codex plugin`, `gemini extensions update`) or download the new release.

## How to report a vulnerability

Do not open a public issue. Report privately through [GitHub Security Advisories](https://github.com/KROU4/BabelDub/security/advisories/new) and include:

- the version (`babeldub --version`), your OS and agent host;
- what an attacker can do and under which conditions;
- reproduction steps or a minimal example. Do not attach game files.

We will acknowledge the report within 7 days, agree on a timeline for the fix and credit you in the release notes if you wish.

## What counts as a vulnerability

BabelDub runs on the user's machine with the rights of the user's account, so these matter:

- **writing into the game folder or outside it** while bypassing `babeldub install` / `uninstall` / `reapply`, damaging or losing the backup, paths escaping the project (`..`, symlinks, file names inside game containers);
- **bypassing the guard hook** in Claude Code that blocks writes into a registered game folder;
- **running code from game files**: the CLI must not run the game's executables or load its libraries (except the explicitly enabled `verify --game-runtime` check described in the contract);
- **loading untrusted code**: downloading models or tools from outside the catalog or without a pinned revision, package substitution during `babeldub setup`;
- **injection through game data**: table strings, file names and metadata that end up in shell commands or change the agent's behaviour beyond translation;
- leaking paths, keys or other user data into logs and reports that users are invited to publish.

## What does not count as a vulnerability

- Breaking a particular game's EULA, or an anti-cheat ban after installing a localization: that is the user's responsibility (see [docs/LEGAL.md](docs/LEGAL.md)).
- Translation or dubbing quality, voice similarity, ASR errors.
- Vulnerabilities in third-party models, PyTorch, ffmpeg, vgmstream and other tools: report them to their authors. If BabelDub installs a vulnerable version by default, tell us.

## How the protection works

- The game folder is read-only until `babeldub install`; installation makes a backup, checks sha1 and replaces files atomically, and `uninstall` restores the originals.
- The Claude Code hooks fail open for everything except an explicit write into a registered game folder, and they never go to the network.
- Models and tools are installed into `BABELDUB_HOME` (default `~/.babeldub`), not into the plugin or game folder; models with non-commercial licenses are enabled only with explicit consent.
- The repository and the releases contain no binaries, model weights or game files.
