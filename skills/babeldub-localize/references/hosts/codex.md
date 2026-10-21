# Host: OpenAI Codex (CLI and desktop)

Read once at the start of work if you run in Codex. The main difference from Claude Code: there are no BabelDub subagents here, **all roles run in the main session**.

## Installation

- `codex plugin marketplace add KROU4/BabelDub`, then `codex plugin add babeldub@babeldub`, then a new session. Codex reads the Claude marketplace manifest (`.claude-plugin/marketplace.json`); verified on Codex CLI 0.158.
- Skills appear as `babeldub:babeldub-*`; call one with `$babeldub-localize` or through `/skills`.
- Skills only, without the plugin: `npx skills add KROU4/BabelDub -a codex`.

The `babeldub-doctor` skill installs the `babeldub` CLI: its `skills/babeldub-doctor/scripts/bootstrap.py` finds `runtime/` next to the skills (if the whole plugin is present) or installs the CLI from the release git tag.

## Action → tool

| Action in the skill | Codex |
|---|---|
| run a `babeldub …` command | the shell command tool; one command per call; JSON from stdout |
| read a report section | a shell command that prints part of a file (for example, lines N to M); never read project files whole |
| write an agent file | Codex file editing or writing from the shell (UTF-8 without BOM) |
| ask the user a question | a plain message with options in the main session, in the user's language (only the 2 questions) |
| delegate a role | perform the role yourself (see below) |

## Roles in the main session

Codex does not load the Claude agents (`claude/agents/*`). For every role:

1. Read the role file `references/roles/<role>.md` (skip the frontmatter; the body is the instruction).
2. Perform the role's steps yourself, following its rules.
3. Build the role's JSON summary and use it as the result of the orchestrator step.
4. Return to `SKILL.md` of the `babeldub-localize` skill.

How to save context without subagent isolation:

- Translation batches strictly one at a time: read the batch → write `.out.jsonl` → `batch submit` → next. Do not keep the contents of earlier batches in your reasoning: everything is already in files.
- For data use only `babeldub status`, `query` with `--fields` and `--limit`, and `sample`.
- After a long loop (translation, synthesis) start the next step with `babeldub status`.
- There are no parallel translators: translation runs sequentially. If Codex is set up with its own subagents (`.codex/agents/*.toml`), BabelDub v0.1 does not ship any; the roles still run in the main session.

## Sandbox and approvals

- `babeldub setup` downloads packages and model weights (network) and writes to `BABELDUB_HOME` (default `~/.babeldub`, `C:\Users\<user>\.babeldub` on Windows) outside the working folder. If the Codex sandbox forbids this, the command fails with an access error: request permission to run it through the Codex approval mechanism (this is not a localization question but a permission for a command). How a specific sandbox mode behaves is not verified.
- GPU-first: the neural stages (`cast`, `synth`, `qa`) need the NVIDIA GPU. If `babeldub doctor` reports no GPU inside the sandbox although the machine has one, request permission to run the command outside the sandbox instead of accepting hours of CPU synthesis (not verified for each sandbox mode).
- `babeldub install` writes to the game folder. It is the only command allowed to do so.

## Hooks

Codex runs plugin hooks only when the plugin is installed manually in the desktop app. Without hooks the work does not change: the CLI provides game-folder protection and state (`games.json`, backup, `babeldub status`). After context compaction start with `babeldub status`.

## Paths

Do not rely on `${CLAUDE_PLUGIN_ROOT}` being substituted in skill text in Codex: according to its documentation Codex sets `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA` for compatibility with plugin hooks, and says nothing about skill text. If a skill contains a path with `${CLAUDE_PLUGIN_ROOT}`, use the path relative to the skill folder.
