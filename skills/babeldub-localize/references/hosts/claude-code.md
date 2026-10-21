# Host: Claude Code

Read once at the start of work if you run in Claude Code (CLI, desktop or IDE). This file maps the skill's actions to Claude Code tools.

## Installation

- In a session: `/plugin marketplace add KROU4/BabelDub`, then `/plugin install babeldub@babeldub`.
- From a shell: `claude plugin install babeldub --marketplace KROU4/BabelDub` (Claude Code 2.1.292 and later).
- Development: `claude --plugin-dir <BabelDub repository folder>`.

The plugin does not install Python dependencies by itself. The `babeldub-doctor` skill installs the `babeldub` CLI from the plugin folder: `${CLAUDE_PLUGIN_ROOT}/runtime`. The `${CLAUDE_PLUGIN_ROOT}` variable is substituted in the skill text when the skill loads, but it is not set in the Bash environment. So copy a command with this path from the skill text; do not reference `$CLAUDE_PLUGIN_ROOT` in the shell.

## Action → tool

| Action in the skill | Claude Code |
|---|---|
| run a `babeldub …` command | Bash (PowerShell also works on Windows); one command per call; read the JSON from stdout |
| long stage (`synth`, `qa`, `setup`, `extract` of a big game) | in chunks (`--chars`, `--limit`) or Bash with `run_in_background`; wait for the notification and check `babeldub status` |
| read a report section | Read with `offset`/`limit`; never read project files whole |
| write an agent file (`*.agent.jsonl`, `*.out.jsonl`, `style_bible.md`) | Write |
| ask the user a question (only the 2 questions) | AskUserQuestion from the main session, 2–4 options, in the user's language |
| delegate a role | Agent (subagent `babeldub-<role>`) |
| parallel translators | several Agent calls in one message, at most 4 at a time |

## Subagents

- The definitions live in the plugin's `claude/agents/babeldub-<role>.md` and are generated from `skills/babeldub-localize/references/roles/*.md`. In a session they appear with the plugin prefix: `babeldub:babeldub-scout`, `babeldub:babeldub-translator` and so on. Explicit call: `@agent-babeldub:babeldub-translator`.
- A subagent does not see the main session's history. Pass everything it needs in the delegation message: the project folder or slug, the stage; for a translator, the path to its batch `translate/batches/<bid>.in.jsonl`; for qa, what `babeldub qa` returned.
- Subagents cannot use AskUserQuestion. Only the main session asks questions; subagents return `human_pending`.
- A subagent answers with one JSON summary. Do not ask it to retell data: the details are in the project files.
- If subagents are unavailable (for example, the plugin is loaded only as a skill), perform the role yourself from its file.

Translation loop in Claude Code:

1. Take up to 4 different `bid` values from `batches[]` of the `babeldub batch make` output (if the list is truncated, use the file names `translate/batches/<bid>.in.jsonl` that have no `.out.jsonl` yet). `batch next` always returns the first open batch, so it is not suitable for handing out several batches.
2. In one message, start one `babeldub-translator` subagent per batch; pass the path `translate/batches/<bid>.in.jsonl` in the project folder.
3. From their summaries check `merged` and `errors`, then take the next batches. When `batch next` returns `done: true`, translation is finished.

## Plugin hooks

Hooks fire in all of the user's sessions, so they are narrow and fast. They only help the work: the CLI does everything critical.

| Event | What it does | What it means for you |
|---|---|---|
| `PreToolUse` (Write, Edit, Bash, PowerShell …) | blocks writes to a registered game folder that bypass `babeldub install`, `uninstall`, `reapply` | if the hook blocked an action, do not work around it: it means you tried to write to the game folder |
| `SessionStart` (startup, resume, compact) | with an active project prints 5–10 lines: game, language, stage, next step | after context compaction continue from the named step, after checking `babeldub status` |

## Commands

| Command | What it does |
|---|---|
| `/babeldub:localize <path to game> [language]` | runs the `babeldub-localize` skill with the arguments |
| `/babeldub:rollback [game]` | rollback: `babeldub uninstall` for the game's project |
| `/babeldub:babeldub-<skill>` | calls a skill directly, e.g. `/babeldub:babeldub-doctor` |

## Notes

- A single Bash call has a time limit (up to 10 minutes). Split synthesis of hundreds of lines into chunks or run it in the background.
- On Windows the substituted `${CLAUDE_PLUGIN_ROOT}` paths use forward slashes; that is fine.
- The Claude desktop app is a packaged (MSIX) app and virtualises `%LOCALAPPDATA%`. That is why `BABELDUB_HOME` defaults to `~/.babeldub` (`C:\Users\<user>\.babeldub` on Windows): models and projects created from the session stay visible to the user's other terminals and agents. Do not point `BABELDUB_HOME` into `%LOCALAPPDATA%`.
- Claude Code workflow scripts (a fan-out of translators) only with the user's explicit consent; by default use the `batch next` → subagents loop.
