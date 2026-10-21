# Host: Gemini CLI and Antigravity

Read once at the start of work if you run in Gemini CLI or Antigravity. There are no BabelDub subagents here: **all roles run in the main session**, as in Codex.

## Installation

- **Gemini CLI** (extension): `gemini extensions install https://github.com/KROU4/BabelDub` (you can add `--ref v0.1.0`, `--auto-update`). The extension provides the `skills/babeldub-*` skills and the `GEMINI.md` context. Installation from a local path is verified on Gemini CLI 0.65.
- Skills only: `gemini skills install <git url or folder>` or `npx skills add KROU4/BabelDub -a gemini-cli`.
- **Antigravity CLI.** Since 2026-06-18 Gemini CLI for consumer accounts has been replaced by Antigravity CLI. Antigravity looks for skills in `<working folder>/.agents/skills/` and in `~/.gemini/antigravity-cli/skills/`; the universal path is `npx skills add KROU4/BabelDub`. The Antigravity plugin format (`agy plugin install`) has not been studied; not verified.

A skill is activated by the `activate_skill` tool with the user's confirmation. If the user asks to translate, localize or revoice a game, in any language, activate `babeldub-localize`.

The `babeldub-doctor` skill installs the `babeldub` CLI (`skills/babeldub-doctor/scripts/bootstrap.py`): from `runtime/` next to the skills or from the release git tag. The folder for models and projects is `BABELDUB_HOME` (default `~/.babeldub`, `C:\Users\<user>\.babeldub` on Windows); it can be changed with the `BABELDUB_HOME` extension setting.

## Action → tool

| Action in the skill | Gemini CLI / Antigravity |
|---|---|
| run a `babeldub …` command | the shell command tool; one command per call; JSON from stdout |
| read a report section | read a file with a line range limit; never read project files whole |
| write an agent file | the file write tool (UTF-8 without BOM) |
| ask the user a question | a plain message with options, in the user's language (only the 2 questions) |
| delegate a role | perform the role yourself |

## Roles in the main session

The BabelDub Claude agents do not load in Gemini: Gemini's agent format is different (`tools` is an array, `model` is a Gemini model) and it rejects extra fields. Therefore:

1. Read `references/roles/<role>.md` (skip the frontmatter).
2. Perform the role's steps yourself, following its rules.
3. Build the role's JSON summary and continue with `SKILL.md` of the `babeldub-localize` skill.

Translate strictly one batch at a time, without keeping earlier batches in context. After long loops start with `babeldub status`.

## Hooks

BabelDub hooks are written for Claude Code; Gemini does not use them. The CLI provides game-folder protection and state.

## Paths

`${CLAUDE_PLUGIN_ROOT}` is not substituted here. Treat paths in the skills as relative to the skill folder.
