# AGENTS.md — context for agents developing BabelDub

Codex, Copilot, Cursor, Gemini CLI, OpenCode and Claude Code (2.1.277+) read this file when they work **on the BabelDub code**. Plugin users do not need it: the `babeldub-localize` skill guides them.

## What this repository is

BabelDub is a public set of skills, agents and hooks for AI agents (Claude Code, Codex, Gemini CLI and others) plus the `babeldub` Python CLI. Together they localize and re-voice **any game**: they find the voice-over and the texts, translate them, clone the voices of the original characters with local TTS models and pack the files back into the engine's native formats.

**The core principle of the voice work is dubbing, not text-to-speech.** Every line in the target language sounds like the original character: same timbre, same emotion and delivery. That is why the voice prompt is always built from the character's own original recordings (the default `tts.ref_strategy=tags` for VoxCPM2: the character's long original reference plus a short delivery instruction measured on the original line or chosen by the translator agent; `bootstrap` adds a target-language prompt bank from the character's accepted dubs; `line` and `char` for the other engines), speech is never slowed down to fit the original's length, and QA compares the dub with the original's voice, words, naturalness and delivery. Any change that weakens this principle must be discussed in an issue first.

**GPU-first.** When an NVIDIA GPU is present, all neural compute (TTS, ASR, speaker embeddings, clustering, separation) runs on it (`compute.device=auto`). See `docs/dev/CONTRACT.md` §1.6.

One repository is at the same time a Claude Code marketplace and plugin, a Codex plugin, a Gemini CLI extension and an Agent Plugins 1.0 package.

## Layout

```text
.claude-plugin/plugin.json, marketplace.json   Claude Code (Codex and Copilot read them too)
.codex-plugin/plugin.json                       Codex / ChatGPT: interface (name, prompts, logo)
plugin.json                                     Agent Plugins 1.0 (closed schema, skills only)
gemini-extension.json, GEMINI.md                Gemini CLI
skills/<name>/SKILL.md                          the ONLY source of skills (Agent Skills standard)
  babeldub-localize/references/roles/*.md       pipeline roles (agents are generated from them)
  babeldub-localize/references/hosts/*.md       host differences
commands/*.md                                   Claude Code slash commands (thin shims to skills)
claude/agents/*.md                              Claude Code subagents: generated, never edit by hand
claude/hooks.json                               Claude Code hooks (handlers in scripts/hooks/)
runtime/                                        Python package and the `babeldub` CLI (src layout, uv)
  src/babeldub/core/                            core: output, paths, project, manifests
  src/babeldub/adapters/                        formats and middleware (base.py is the interface)
  src/babeldub/commands/                        CLI commands
  src/babeldub/workers/                         model workers (own venvs, JSONL protocol)
  tests/                                        pytest, synthetic data only
scripts/                                        gen_agents, bump_version, validate_skills, build_release, install.*
docs/dev/CONTRACT.md                            CLI contract: commands, project files, exit codes
docs/HOSTS.md, docs/LEGAL.md                    install per host, responsible use
docs/ENGINES.md, docs/MODELS.md                 engine support matrix, models and hardware tiers
.github/                                        CI, releases, issue and PR templates
```

## Developer commands

Run every command from the repository root. Python ≥ 3.10, uv.

```bash
uv sync --project runtime --extra dev                              # environment in runtime/.venv
uv run --project runtime ruff check runtime                        # linter (config in runtime/pyproject.toml)
uv run --project runtime ruff check --config runtime/pyproject.toml scripts
uv run --project runtime pytest runtime/tests -q                   # CLI tests
uv run --project runtime pytest runtime/tests/<area> -q            # one area
uv run --project runtime pytest scripts -q                         # tests of hooks and helper scripts
uv run --project runtime python scripts/validate_skills.py         # skills, manifests, repository hygiene
uv run --project runtime python scripts/bump_version.py --check    # versions match in every manifest
uv run --project runtime python scripts/gen_agents.py --check      # claude/agents matches the roles
claude plugin validate . --strict                                  # strict check of the plugin and marketplace
uv run --project runtime python scripts/build_release.py --out <temp folder>
```

Every command in the list must pass before a PR. CI (`.github/workflows/ci.yml`) runs them on Ubuntu and Windows.

## Rules

1. **`docs/dev/CONTRACT.md` is the source of truth** for the CLI, project files and the worker protocol. If you change how a command behaves, update the contract in the same PR.
2. **One JSON on output.** Every command prints exactly one JSON object to stdout through `core.output.emit` and returns exit code 0/1/2/3/4. Logs go only to stderr or to the project's `logs/`. Expected errors are `BabelDubError(message, code=..., hint=...)`.
3. **No stubs.** No `TODO`, no `NotImplementedError`, no "later" branches. If a feature is out of scope for the version, the command honestly returns a `BabelDubError` with a `hint`.
4. **No game assets in the repository.** No banks, archives, line audio or extracted texts. Tests generate synthetic FSB5, banks and tables on the fly. `validate_skills.py` blocks `*.bank`, `*.wem`, `*.bnk`, `*.pak` and files over 1 MB.
5. **The game folder is read-only** until `babeldub install`. Neither the CLI nor the tests ever run the game's executables.
6. **Heavy work stays outside the CLI.** The CLI depends only on the light packages in `runtime/pyproject.toml`. Everything that needs `torch` runs in workers (`runtime/src/babeldub/workers/*.py`) in separate venvs; workers do not import `babeldub`.
7. **Skills are portable.** The frontmatter holds only `name`, `description`, `license`, `compatibility`, `metadata`; `name` equals the folder name; `description` is double-quoted and 50–1024 characters (the validator limit; aim for 300+ so it carries trigger phrases in several languages); the body is at most 500 lines, details go to `references/`. Host specifics go to `references/hosts/*.md`, not into the skill body.
8. **Agents are generated.** Edit roles in `skills/babeldub-localize/references/roles/*.md`, then run `python scripts/gen_agents.py`. A new file in `claude/agents/` must be listed in `agents` in `.claude-plugin/plugin.json` (a directory is not allowed there, only `.md` files). Do not create `agents/` or `hooks/` folders at the root: Gemini tries to load them.
9. **Change the version only with** `python scripts/bump_version.py X.Y.Z` (manifests, `runtime/pyproject.toml`, `__init__.py`, `uv.lock`, `metadata.babeldub-cli` in the skills) plus a section in `CHANGELOG.md`. `marketplace.json` has no version on purpose.
10. **Files:** UTF-8 without BOM, JSON without BOM (Gemini fails on a BOM), manifest paths with `/` and a leading `./`. No `bin/` folder at the root (claude.ai and Cowork refuse such a plugin) and no `CLAUDE.md` at the root (a plugin does not load it).
11. **English everywhere.** Code, identifiers and comments are in English (keep comments short), and so are all human-facing texts: docs, skills and their references, agent roles, commands, hook messages, CLI messages and generated reports, GitHub templates. `README.ru.md` is the only Russian document (plus its `assets/*-ru.*` illustrations). Skill descriptions may list example user phrases in several languages (including Russian) so the skills trigger for non-English users, and every instruction tells the agent to talk to the user in the user's language.
12. **No secrets and no models** in the repository. `babeldub setup` downloads weights into `BABELDUB_HOME` (default `~/.babeldub`) and shows each license; non-commercial models only with the user's explicit consent.

## How to add an engine or a format

In short (details in `CONTRIBUTING.md`): an adapter in `runtime/src/babeldub/adapters/<name>/` that implements the `adapters/base.py` interface, an entry in `REGISTRY`, engine signatures in `runtime/src/babeldub/data/`, synthetic tests in `runtime/tests/<name>/`, agent references in `skills/babeldub-recon/references/engines/` and `skills/babeldub-pack/references/formats/`, and a row in `docs/ENGINES.md`.
