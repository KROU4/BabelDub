# BabelDub extension

BabelDub localizes and dubs video games **in the voices of their original characters**: every
translated line keeps the character's timbre, emotion and delivery, only the language changes.

When the user asks to translate, localize, dub, re-voice or roll back a game ("переведи игру",
"переозвучь", "сделай русскую озвучку"), activate the `babeldub-localize` skill and follow it.
Stage skills: `babeldub-doctor`, `babeldub-recon`, `babeldub-cast`, `babeldub-translate`,
`babeldub-voice`, `babeldub-qa`, `babeldub-pack`. Ask the user only what the skill asks (target
language, which characters to re-voice) and reply in the user's language.

Gemini-specific notes:

- There are no Claude subagents here. Perform each pipeline role yourself in the main session,
  following `skills/babeldub-localize/references/roles/<role>.md` of this extension, and read
  `skills/babeldub-localize/references/hosts/gemini-antigravity.md` once per session.
- The work is done by the `babeldub` CLI. If `babeldub --version` fails, activate
  `babeldub-doctor`: it installs the CLI with uv and checks the GPU, ffmpeg and models.
- Every CLI command prints one JSON object. Exit codes: 0 done, 1 error, 2 bad arguments or missing
  previous stage, 3 done but needs a decision, 4 environment missing (`babeldub doctor`, then
  `babeldub setup`).
- Heavy data lives in `BABELDUB_HOME` (extension setting; default `%LOCALAPPDATA%\BabelDub` on
  Windows, `~/.local/share/babeldub` on Linux).

Safety: the game folder is read-only until `babeldub install` (rollback: `babeldub uninstall`);
never edit or delete game files yourself and never run the game's executables; write only the agent
files a skill names (`*.agent.jsonl`, batch `*.out.jsonl`) and let the CLI merge them.
