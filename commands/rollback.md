---
description: Roll back a BabelDub dub — restore the game's original files from the backup
argument-hint: "[project slug, project folder or game folder]"
---

Roll back the installed BabelDub dub following the `babeldub-pack` skill (the rollback section and `references/install-rollback.md`). Talk to the user in the user's language. Argument: $ARGUMENTS

1. Identify the project:
   - empty argument — the active project (`babeldub status` shows which one);
   - a slug or a project folder — pass it as `--project <argument>`;
   - a game folder — find its project in `babeldub games` and take its slug.
   If there are several projects and it is unclear which one to roll back, show the list from `babeldub games` and ask the user.
2. `babeldub uninstall [--project <slug>]`. Do not copy or delete anything in the game folder by hand.
3. From the output:
   - `restored[]` — the files returned to the originals;
   - "the game is running" — ask the user to close the game and its launcher, then retry;
   - `left_changed[]` — files the game updated after the install: they are left as they are (they are the new originals); use `--force` only on the user's explicit request;
   - `failed[]` — the backup is unavailable or a file is locked: suggest restoring the originals with the file integrity check in the game's launcher.
4. Briefly report the result and how to bring the dub back: `babeldub install`.
