# Installing BabelDub in different AI hosts

One BabelDub repository is at the same time a Claude Code marketplace and plugin, a Codex plugin, a Gemini CLI extension and an Agent Plugins 1.0 package. The skills (`skills/`) are the same for every host. Only the install method and what the host offers beyond skills differ.

Markers in the tables:

- **verified**: the command was checked locally (Claude Code 2.1.302, codex-cli 0.158.0, Gemini CLI 0.65.0);
- **per docs**: taken from the host's official documentation, not run locally;
- **not verified**: a reasonable assumption without confirmation.

## What you need in any case

The skills drive the `babeldub` CLI. The full pipeline (extraction, voice cloning, build) works only with a **local** agent that has access to the game files:

- Windows 10/11 or Linux, Python 3.10+ and [uv](https://docs.astral.sh/uv/), git, ffmpeg;
- for the voice work, an NVIDIA GPU with 8+ GB of VRAM (GPU-first: when a GPU is present, all neural compute runs on it; the CPU works, but slowly);
- tens of gigabytes of disk space for models and projects in `BABELDUB_HOME` (default `~/.babeldub`, which is `C:\Users\<user>\.babeldub` on Windows; override it with the `BABELDUB_HOME` environment variable).

The `babeldub-doctor` skill installs the CLI on first run. You can also install it in advance:

```powershell
# Windows, from a repository clone or an unpacked kit
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

```bash
# Linux
bash scripts/install.sh
```

Without a local `runtime/`, the scripts install the CLI from GitHub (`-Ref v0.1.0` / `--ref v0.1.0` for a specific release). If uv is not installed, the script shows the official install command; the `-InstallUv` / `--install-uv` flag runs it for you.

## Summary

| Host | What it gets | Install | Status |
|---|---|---|---|
| Claude Code | skills, subagents, hooks, `/babeldub:*` commands | marketplace `KROU4/BabelDub` | verified |
| claude.ai, Claude Desktop | skills (chat), plus agents and hooks in Cowork | zip upload | per docs |
| Codex CLI, Codex desktop | skills | marketplace `KROU4/BabelDub` | verified |
| ChatGPT (web, desktop) | skills in a cloud sandbox | plugin directory, marketplace import | not verified |
| Gemini CLI | skills and the `GEMINI.md` context | `gemini extensions install` | verified (local path) |
| Antigravity | skills | `.agents/skills`, `npx skills` | per docs |
| GitHub Copilot (CLI, VS Code) | skills | `copilot plugin install` or `.agents/skills` | per docs |
| Cursor | skills | Agent Plugins plugin or `.agents/skills` | per docs |
| OpenCode and other Agent Skills clients | skills | `npx skills add` | per docs |

BabelDub subagents exist only in Claude Code (and Cowork). On the other hosts the same roles run in the main session from the `skills/babeldub-localize/references/roles/*.md` files; the orchestrator skill does this on its own.

## Claude Code

**Verified.**

In a session:

```text
/plugin marketplace add KROU4/BabelDub
/plugin install babeldub@babeldub
/reload-plugins
```

From a terminal (the one-command form needs Claude Code 2.1.292+):

```bash
claude plugin install babeldub --marketplace KROU4/BabelDub
# or in two steps
claude plugin marketplace add KROU4/BabelDub
claude plugin install babeldub@babeldub          # --scope project for the whole project team
```

A specific version: `claude plugin marketplace add KROU4/BabelDub#v0.1.0`.

What you get:

- the skills `babeldub:babeldub-localize`, `babeldub:babeldub-doctor` and the rest;
- the `babeldub:babeldub-*` subagents for the pipeline roles;
- hooks: protection of a registered game folder against writes that bypass `babeldub install`, and a summary of the active project at session start;
- the commands `/babeldub:localize <game path> [language]` and `/babeldub:rollback`.

Update: `claude plugin update babeldub@babeldub`. Auto-update of third-party marketplaces is off by default: `/plugin` → Marketplaces → Enable auto-update. Remove: `claude plugin uninstall babeldub@babeldub` (models in `BABELDUB_HOME` stay).

Without the marketplace, for one session:

```bash
claude --plugin-dir ./BabelDub                     # repository clone
claude --plugin-dir ./babeldub-plugin-0.1.0.zip    # release archive
```

To enable the plugin for everyone on a project, add this to the project's `.claude/settings.json` (per docs, not verified locally):

```json
{
  "extraKnownMarketplaces": {
    "babeldub": { "source": { "source": "github", "repo": "KROU4/BabelDub" } }
  },
  "enabledPlugins": { "babeldub@babeldub": true }
}
```

## claude.ai, Claude Desktop and Cowork

**Per docs.** A cloud chat has no access to the game folder or to a GPU, so only translation of exported line batches (`translate/batches/*.in.jsonl`) is available there. The full pipeline runs only in Claude Code or Cowork on your computer.

- **The whole plugin** (Cowork also gets the agents and hooks): Customize → Plugins → Add → Upload plugin → `babeldub-plugin-<version>.zip` from the release. claude.ai limits: up to 200 MB and 5000 files; the BabelDub archive fits.
- **Individual skills**: Customize → Skills → upload a zip. Each upload is exactly one skill, so `babeldub-skills-<version>.zip` contains a `claude-ai/` folder with a separate archive per skill. Start with `babeldub-localize.zip` and `babeldub-translate.zip`. Code execution must be enabled.

## Codex CLI and Codex desktop

**Verified** (codex-cli 0.158.0): Codex reads `.claude-plugin/marketplace.json` and installs the plugin; the skills show up as `babeldub:babeldub-*`.

```bash
codex plugin marketplace add KROU4/BabelDub            # a specific version: --ref v0.1.0
codex plugin add babeldub@babeldub
```

Then open a new session. In the TUI, plugins are managed with `/plugins`; invoke the skill as `$babeldub-localize` or through `/skills`. To enable it per project, use `.codex/config.toml`:

```toml
[plugins."babeldub@babeldub"]
enabled = true
```

Codex has no Claude subagents and no Claude hooks: the roles run in the main session, and the CLI itself protects the game folder (read-only until `babeldub install`).

## ChatGPT (web, desktop, Work)

**Not verified.** Plugins with skills are available in ChatGPT through the plugin directory, and teams can import a GitHub marketplace. The skills run in a cloud sandbox without access to the game files and without a GPU, so, as in claude.ai, they are only good for translating batches. Uploading a single zip skill to web ChatGPT is not officially documented; the OpenAI Skills API accepts zips up to 50 MB, so the archives from `claude-ai/` in `babeldub-skills-<version>.zip` fit.

## Gemini CLI

**Verified** on a local path (Gemini CLI 0.65.0): all skills and `GEMINI.md` are installed.

```bash
gemini extensions install https://github.com/KROU4/BabelDub --auto-update
# a specific version
gemini extensions install https://github.com/KROU4/BabelDub --ref v0.1.0
# development: link to a clone
gemini extensions link ./BabelDub
```

The installer asks for the optional **BabelDub home** setting (the `BABELDUB_HOME` variable). Leave it empty to use the default `~/.babeldub`; change it later with `gemini extensions config babeldub`. If the folder is not trusted, Gemini asks "Do you trust the files in this folder?".

Gemini looks at the GitHub Release first: BabelDub releases contain several archives without `win32.`/`linux.`/`darwin.` prefixes, so Gemini takes the tag's source archive with `gemini-extension.json` (verified against the `github.ts` source).

Separately from the extension you can install only the skills: `gemini skills install https://github.com/KROU4/BabelDub --path skills/babeldub-localize` (per docs). Gemini also looks for skills in `.agents/skills`.

> For the free tier and Google One, Gemini CLI was replaced by Antigravity CLI (since 18 June 2026). Gemini CLI itself keeps getting releases.

## Antigravity

**Per docs.** Antigravity reads skills from `<project>/.agents/skills/` and `~/.gemini/antigravity-cli/skills/`.

- The easiest way is the release kit (`babeldub-kit-<version>.zip`, see below): it already contains `.agents/skills/`.
- Or the universal installer: `npx skills add KROU4/BabelDub -a antigravity` (agent name per `npx skills --help`; not verified).
- BabelDub does not support the Antigravity plugin format (`agy plugin install`) yet (not studied).

## GitHub Copilot (CLI and VS Code)

**Per docs.** Copilot CLI reads the root `plugin.json` (Agent Plugins 1.0) and, if it is missing, `.claude-plugin/plugin.json`; it also looks for a marketplace in `.claude-plugin/marketplace.json`.

```bash
copilot plugin install KROU4/BabelDub
```

Copilot also takes skills from `.github/skills`, `.claude/skills`, `.agents/skills` and `~/.copilot/skills`. Copilot probably does not pick up the Claude Code agents (`claude/agents/*.md`): it has its own `*.agent.md` format (not verified).

## Cursor

**Per docs.** Cursor supports Agent Plugins 1.0 (the root `plugin.json`) and reads skills from `.agents/skills`, `.cursor/skills`, `.claude/skills` and their home-folder equivalents.

- Plugin: Customize → Plugins → import from the `KROU4/BabelDub` repository (for teams: Team; not verified).
- Skills: `npx skills add KROU4/BabelDub -a cursor` or the release kit.

## OpenCode and other Agent Skills clients

**Per docs.** The universal installer [`npx skills`](https://github.com/vercel-labs/skills) (Node 22.20+) copies `skills/*` into the folders of the chosen agent:

```bash
npx skills add KROU4/BabelDub -g                          # all detected agents, globally
npx skills add KROU4/BabelDub -a opencode                 # one specific agent
npx skills add KROU4/BabelDub --skill babeldub-localize   # only one skill
```

OpenCode also reads `.opencode/skills`, `.claude/skills` and `.agents/skills`. The installer copies only the skills; the CLI installs `babeldub-doctor` on first run.

## Kit: a workspace folder next to the game

`babeldub-kit-<version>.zip` from the release is a ready `BabelDub-Workspace/` folder for any agent:

- `AGENTS.md` (and copies `CLAUDE.md`, `GEMINI.md`) with instructions;
- `.agents/skills/` for Codex, Gemini CLI, Antigravity, Cursor, Copilot, OpenCode;
- `.claude/skills/` and `.claude/agents/` for Claude Code;
- `runtime/` and `install.ps1` / `install.sh` to install the CLI without access to GitHub;
- `README-KIT.md` with step-by-step instructions.

Unpack it **next to** the game folder (not inside it), open the agent in `BabelDub-Workspace` and ask, for example: "Dub the game in ..\MyGame into Russian" (any language works: "Переозвучь игру из ..\MyGame на русский"). Cursor, Copilot and OpenCode read both `.agents/skills` and `.claude/skills`, so the skills may appear twice; they are the same files. Claude Code loads `.claude/skills` and `.claude/agents` automatically only after you trust the folder.

## Common problems

| Symptom | Cause and fix |
|---|---|
| `babeldub: command not found` after install | the `uv tool dir --bin` folder is not on `PATH`: run `uv tool update-shell` and open a new terminal |
| Exit code 4 from commands | the environment is incomplete: `babeldub doctor`, then `babeldub setup` (or ask the agent) |
| Gemini: `Unexpected token` during install | a JSON manifest with a BOM; BabelDub releases have no BOM, so check your local edits |
| Claude Code does not see an update | the plugin has a pinned version: run `claude plugin update babeldub@babeldub` or enable marketplace auto-update |
| claude.ai rejects the plugin | the archive must have `.claude-plugin/plugin.json` at the root or in the single top-level folder; use `babeldub-plugin-<version>.zip` from the release without repacking |
| Models downloaded from the Claude desktop app are invisible in another terminal | an older default under `%LOCALAPPDATA%` was virtualised by the packaged app; the default is now `~/.babeldub`, and `babeldub doctor` reports data left in old locations so you can move it (or point `BABELDUB_HOME` at it) |
