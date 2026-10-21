# Install, rollback and reapply

Read this before the first install and on any failure of `install`, `uninstall` or `reapply`.

## 1. Where things live

| Path (in the project folder) | What it is |
|---|---|
| `build/out/<relpath>` | built files; they mirror the path inside the game folder |
| `build/build_manifest.json` | what was built: containers, sha1 of the source (`src_sha1`) and the result (`out_sha1`), codec, replaced/kept, models and licenses, `build_id` |
| `build/backup/<relpath>` | copies of the originals made before replacement |
| `build/install.json` | install log: for each file `relpath`, `orig_sha1`, `new_sha1`, backup path; `state` (`installing`, `installed`, `uninstalled`), `build_id` |

The project folder is `BABELDUB_HOME/projects/<slug>`; `BABELDUB_HOME` defaults to `~/.babeldub` (Windows: `C:\Users\<user>\.babeldub`) and can be overridden with the `BABELDUB_HOME` environment variable. The backup lives there too, not in the game folder: a Steam integrity check will not touch it, but **deleting the project folder deletes the backup as well**.

## 2. What `install` does

1. Refuses if the current build has not passed `verify` (checked by `build_id`).
2. Refuses if a process from the game folder is running (the game, the launcher, the crash handler): on Windows an open file cannot be replaced, and a running game keeps the old data. Processes are only listed; nothing is started.
3. Refuses if anti-cheat is found (EasyAntiCheat, BattlEye, XIGNCODE3, nProtect GameGuard, PunkBuster, EQU8 and others): modified files can get the player banned. `--force` lifts this ban and the running-game ban — only for a game known to be offline single-player, and only by the user's decision.
4. For each build file — a plan based on sha1:
   - the game file is the original the build was made from → back it up and replace it;
   - the game already has our build of the same original → replace it with the new build;
   - the game already has what is needed → nothing;
   - the game file is neither the original nor our build (a game update, another mod) → **the whole install is refused**, with a hint to rebuild.
5. Checks disk space, then writes: first the `install.json` log (state `installing`), then the backup with a sha1 check, then the atomic replacement (a temporary file next to the target plus a rename). A failure in the middle of the install restores the files already replaced.
6. Container files that were installed earlier but dropped out of the new build are restored to the originals.

Output: `installed[]`, `unchanged`, `files`, `backup`, `backed_up`, `rollback`; on problems — `attention[]` (for example, "no valid backup" — the file's backup could not be confirmed).

`--dry-run` shows the plan (`plan`, `write`, `stale`) without writing anything — useful before the first install.

## 3. What `uninstall` does

For each file in the log:

| State of the file in the game | Action |
|---|---|
| our build | restore the original from the backup; the backup is checked by sha1 first, a damaged or missing backup goes to `failed[]` |
| already the original | nothing (`already_original`) |
| a file the game did not have, added by the build | delete it |
| such an added file is already gone | nothing |
| neither the original nor our build (the game updated on top) | **leave it as is** (`left_changed`): this is a new version of the game file and must not be overwritten with an old backup. `--force` restores the verified backup anyway |

Output: `restored[]`, `already_original`, `state`; on problems — `left_changed[]`, `failed[]` and `hint`. Exit code 3 if anything is left.

If `uninstall` cannot restore a file (the backup is damaged or deleted, the file is locked): close the game and retry; if there is no backup, restore the originals with the launcher's tools ("Verify integrity of game files" in Steam and its equivalents).

## 4. What `reapply` does

Needed when the originals came back on their own: a game update, "Verify integrity of game files" in Steam, a reinstall.

- For each build container: if the game again has the original the build was made from, install the build again (with a backup); if it already has our build, do nothing (`current`).
- If the game shipped a **new version** of a container (its sha1 matches neither the original nor the build) — `changed[]` and exit code 3. The dub for it must be rebuilt from the new originals: follow the hint from the output — `build` (changed containers only) → `verify` → `install`. If the update changed lines, you may need to run `extract` and the following stages again for the new lines.
- Refuses if the dub is not installed (`nothing to reapply`): then `install` is needed.

## 5. Scenarios

| Situation | What to do |
|---|---|
| the user wants the original voices back | `babeldub uninstall` |
| the game updated and the dub is gone | `babeldub reapply`; on `changed[]` — rebuild those containers |
| Steam "Verify integrity" restored the originals | `babeldub reapply` |
| lines have no sound in the game, or the game crashes after install | `babeldub uninstall`; for FMOD — rebuild with a different codec (`babeldub build --codec pcm16`), `verify`, `install` |
| the install was interrupted (the PC shut down) | rerun `babeldub install`: the `installing` log and sha1 show which files were already replaced |
| an antivirus blocks the replacement | tell the user which file was not replaced (`failed[]` or the `install` error); the user decides on antivirus exclusions; then rerun the command |
| the project folder was deleted | the backup is gone: restore the originals only through the game's launcher |

## 6. Guard hook (Claude Code)

In Claude Code, the BabelDub plugin installs a `PreToolUse` hook: it blocks writing, deleting and moving files inside game folders from the `BABELDUB_HOME/games.json` registry (`babeldub init` adds the game there) by any agent tool — except the `babeldub install`, `babeldub uninstall` and `babeldub reapply` commands. The check is heuristic (it parses the command line without executing it) and lets the action through on any error of its own. It can be disabled for the session with the environment variable `BABELDUB_GUARD=off` — only at the user's explicit request.

Other hosts have no hooks; protection comes from the CLI itself: it writes to the game folder only with these three commands.

## 7. What not to do

- Do not copy files from `build/out` into the game by hand: there will be no backup and no log, and `uninstall` will not be able to roll back.
- Do not delete `build/backup` or the project folder while the dub is installed.
- Do not "fix" `install.json` by hand.
- Do not distribute `build/out` or the backups: they contain game assets.
