## What changed

<!-- In short: what and why. Link the issue if there is one: Closes #123 -->

## How it was tested

<!-- Commands and results. For an adapter: which synthetic data and (locally) which game it was tested on. -->

## Checklist

- [ ] `uv run --project runtime ruff check runtime` and `ruff check --config runtime/pyproject.toml scripts` report no errors
- [ ] `uv run --project runtime pytest runtime/tests -q` and `pytest scripts -q` pass
- [ ] `python scripts/validate_skills.py` reports no errors
- [ ] `python scripts/bump_version.py --check` and `python scripts/gen_agents.py --check` pass
- [ ] `claude plugin validate . --strict` passes (if skills, agents, commands, hooks or manifests changed)
- [ ] Tests use synthetic data only; **the PR contains no game files**, line audio or extracted texts
- [ ] If the CLI or the project file format changed, `docs/dev/CONTRACT.md` is updated
- [ ] If agent roles changed, the edits are in `skills/babeldub-localize/references/roles/` and `claude/agents/` is regenerated
- [ ] Docs, skills and messages are in English (only `README.ru.md` is Russian)
- [ ] `CHANGELOG.md` has an entry under "Unreleased"
