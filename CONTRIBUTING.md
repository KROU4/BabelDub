# Contributing to BabelDub

Thank you for your interest in the project. What BabelDub needs most: new engines and formats, testing on real games and better dubbing quality. Below is how the work is organised and which rules are mandatory.

## The main rule: no game files

You **must not** add game files to the repository, issues or PRs: banks, archives, line audio, extracted texts, screenshots of whole dialogue texts, or synthesized actor voices. Distributing them violates the rights of the rights holders. Tests run only on synthetic data generated on the fly. In CI, `scripts/validate_skills.py` rejects `*.bank`, `*.wem`, `*.bnk`, `*.pak`, `*.fsb`, `*.pck` and any file over 1 MB (except `assets/*.png`).

If you need a sample to work out a format, describe its structure instead: sizes, headers, offsets, the output of `babeldub scan`. The "New game or engine support" issue template tells you what to collect.

## Environment

You need Python 3.10+ (CI uses 3.12), [uv](https://docs.astral.sh/uv/) and git. A full run on a game also needs ffmpeg and an NVIDIA GPU with 8+ GB of VRAM, but development and tests need neither.

```bash
git clone https://github.com/KROU4/BabelDub
cd BabelDub
uv sync --project runtime --extra dev
uv run --project runtime babeldub --version
```

The whole plugin for one Claude Code session: `claude --plugin-dir .`. Skills for other hosts: see [docs/HOSTS.md](docs/HOSTS.md).

## Checks before a PR

```bash
uv run --project runtime ruff check runtime
uv run --project runtime ruff check --config runtime/pyproject.toml scripts
uv run --project runtime pytest runtime/tests -q
uv run --project runtime pytest scripts -q
uv run --project runtime python scripts/validate_skills.py
uv run --project runtime python scripts/bump_version.py --check
uv run --project runtime python scripts/gen_agents.py --check
claude plugin validate . --strict
```

CI runs the same on Ubuntu and Windows, plus `claude plugin validate` and the official `skills-ref` validator on Ubuntu.

## The CLI contract

[docs/dev/CONTRACT.md](docs/dev/CONTRACT.md) is the source of truth for commands, project files and the worker protocol. In short:

- a command prints **exactly one** compact JSON object to stdout through `core.output.emit` and returns exit code 0 (done), 1 (error), 2 (bad arguments or a stage not passed), 3 (a decision is needed) or 4 (environment missing);
- expected errors are `BabelDubError(message, code=..., hint=...)` with a clear hint for the agent;
- only the CLI writes the canonical manifests; agents write `*.agent.jsonl` and `*.out.jsonl`, and the CLI validates and merges them;
- re-running a stage does not break the result and does not redo finished work without `--force`;
- no stubs: if a feature is out of scope for the version, the command returns a `BabelDubError` with a `hint` instead of pretending to work;
- GPU-first: when an NVIDIA GPU is present, all neural compute runs on it (`compute.device=auto`, contract §1.6).

If you change how a command behaves or a file format, update the contract in the same PR.

## How to add an engine or a format (an adapter)

An adapter owns one container or middleware: it finds the assets, lists the lines, decodes them to WAV, reads the text tables, rebuilds the container with new audio and verifies the result. The `scan`, `extract`, `link`, `build` and `verify` commands talk to adapters only through the interface, so a new engine does not touch the pipeline.

1. **Code.** Create `runtime/src/babeldub/adapters/<name>/adapter.py` with a subclass of `Adapter` from [`adapters/base.py`](runtime/src/babeldub/adapters/base.py):
   - `discover(game_dir)`: a cheap search for assets without decoding; returns `Asset` objects with a `role` and a support level;
   - `list_lines(game_dir, asset)`: line records per the `lines.jsonl` contract (`id` via `core.ids.line_id`, `uid`, `entry`, `kind`, `locale`, `audio`);
   - `extract_audio(...)`: WAV files in `audio/orig/<uid>.wav`;
   - `read_texts(...)`: table rows `{key, table, locale, lang, text, source}`;
   - `build(...)`: a rebuilt copy in `build/out/<relpath>`; lines without a replacement stay as they were;
   - `verify(...)`: checks of the built container without running the game.

   Whatever the format cannot do, the method rejects honestly with `BabelDubError(EXIT_USAGE)` and a hint.
2. **Registration.** Add a line to `REGISTRY` in `adapters/base.py` (`"<name>": "babeldub.adapters.<name>.adapter:<Class>"`) and the engine or middleware signatures to `runtime/src/babeldub/data/` so that `scan` recognises it.
3. **Support level.** A: extraction and native rebuild without proprietary tools; B: needs a tool the user installs or a mod loader; C: read-only, rebuild by following the instructions in the skill. Add or update the row in [docs/ENGINES.md](docs/ENGINES.md).
4. **Tests.** `runtime/tests/<name>/` on synthetic data: build a minimal container in memory or in a temporary folder (`tmp_path`), then check parsing, extraction, the rebuild, and that unchanged lines match the original byte for byte. In tests, replace external tools (vgmstream, ffmpeg) with fixtures or mark the tests `pytest.mark.skipif` when the tool is missing.
5. **Knowledge for the agent.** Describe the format in `skills/babeldub-recon/references/engines/<engine>.md` (how to find it, what is inside, pitfalls) and the build path in `skills/babeldub-pack/references/formats/<format>.md`. The skill loads these files only when the engine is detected, so make them self-contained.
6. **Testing on a real game** happens only locally. In the PR, state which game and version you tested on and attach command summaries, not files.

## Skills, roles and agents

- Skills live only in `skills/<name>/SKILL.md` and follow the Agent Skills standard: the frontmatter holds only `name`, `description`, `license`, `compatibility`, `metadata`; `name` equals the folder name; `description` is double-quoted, 50–1024 characters (aim for 300+), with example trigger phrases in several languages, including English and Russian; the body is at most 500 lines, details go to `references/`.
- Skills, references, roles and commands are written in English and tell the agent to talk to the user in the user's language.
- Pipeline roles are described in `skills/babeldub-localize/references/roles/*.md`. The Claude Code subagents in `claude/agents/` are **generated** by `python scripts/gen_agents.py`; do not edit them by hand. List a new agent file in `agents` in `.claude-plugin/plugin.json`: `validate_skills.py` checks that the list and the folder match.
- Host differences (Claude Code, Codex, Gemini and others) belong in `skills/babeldub-localize/references/hosts/`, not in the skill bodies.
- After changes, run `claude plugin validate . --strict` and `python scripts/validate_skills.py`.

## Models and external tools

- Model weights and binaries are never committed. The model catalog is `runtime/src/babeldub/data/models.yaml` with the repository, revision, license and a commercial-use flag; `babeldub setup` downloads the models into `BABELDUB_HOME` (default `~/.babeldub`). See [docs/MODELS.md](docs/MODELS.md).
- A new model with a non-commercial license is acceptable only as an option the user enables explicitly.
- Worker code (`runtime/src/babeldub/workers/`) runs in the engine's own venv and does not import `babeldub`. Logs go only to stderr; the protocol is JSONL over stdin/stdout (contract, §6).
- GPL tools (for example, Rubber Band) are not vendored: the CLI calls them as external programs the user installed.

## Style

- Python ≥ 3.10, type annotations, `pathlib`, UTF-8 everywhere, `ruff` with the `runtime/pyproject.toml` config.
- Everything is in English: code, identifiers, comments, documentation, skills and user-facing texts. The only exception is `README.ru.md`.
- JSON and Markdown are UTF-8 without BOM with LF line endings (`.ps1` and `.cmd` use CRLF; see `.gitattributes` and `.editorconfig`).
- Small, focused PRs. Commit messages are imperative and in English (`Add Wwise BNK rebuild`).

## Versions and releases

1. `python scripts/bump_version.py X.Y.Z` (or `patch` / `minor` / `major`) changes the version in every manifest, `runtime/pyproject.toml`, `__init__.py`, `uv.lock`, the skills' `metadata.babeldub-cli` and the fallback version in `bootstrap.py`. The script does not touch mentions of the old version in skill and documentation prose; it prints them as a `file:line` list for you to review by hand. The list of places is in `.version-bump.json`.
2. In `CHANGELOG.md`, rename "Unreleased" to `## [X.Y.Z] - YYYY-MM-DD` and add a new empty "Unreleased" section.
3. The `vX.Y.Z` tag starts `.github/workflows/release.yml`: tests, checks, `scripts/build_release.py` and publication of a release with three archives and `SHA256SUMS`.

## Conduct

Be polite and constructive. Discuss code and decisions, not people. Report vulnerabilities privately (see [SECURITY.md](SECURITY.md)).
