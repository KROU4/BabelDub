"""Tests for scripts/gen_agents.py on temporary role fixtures (and a dry run on the real roles)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_agents  # noqa: E402

TRANSLATOR = """---
name: babeldub-translator
description: "Dub translator: translates one batch of lines — submits via babeldub batch submit."
tools: "Bash, Read, Write"
model: opus
---

# Role: translator

Role text.
"""

PACKER = """---
name: packer
description: BabelDub packer
tools: [Bash, Read]
---
# Role: packer
"""


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "translator.md").write_text(TRANSLATOR, encoding="utf-8")
    (roles / "packer.md").write_text(PACKER, encoding="utf-8")
    out = tmp_path / "claude" / "agents"
    plugin = tmp_path / ".claude-plugin" / "plugin.json"
    plugin.parent.mkdir()
    plugin.write_text(
        json.dumps(
            {
                "name": "x",
                "agents": ["./claude/agents/babeldub-packer.md", "./claude/agents/babeldub-translator.md"],
            }
        ),
        encoding="utf-8",
    )
    return roles, out, plugin


def _run(roles: Path, out: Path, plugin: Path, *extra: str) -> int:
    return gen_agents.main(["--roles", str(roles), "--out", str(out), "--plugin-json", str(plugin), *extra])


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = gen_agents.FRONTMATTER_RE.match(text)
    assert match, text[:200]
    return yaml.safe_load(match.group("yaml"))


def test_generates_claude_subagents(tmp_path: Path) -> None:
    roles, out, plugin = _fixture(tmp_path)
    assert _run(roles, out, plugin) == 0
    assert sorted(p.name for p in out.iterdir()) == ["babeldub-packer.md", "babeldub-translator.md"]

    meta = _frontmatter(out / "babeldub-translator.md")
    assert meta == {
        "name": "babeldub-translator",
        "description": "Dub translator: translates one batch of lines — submits via babeldub batch submit.",
        "tools": "Bash, Read, Write",
        "model": "opus",
    }
    body = (out / "babeldub-translator.md").read_text(encoding="utf-8")
    assert "`babeldub-localize` skill" in body
    assert "`babeldub-translate` skill" in body
    assert "original hero's voice" in body
    assert "user's language" in body
    assert body.rstrip().endswith("Role text.")
    assert "# Role: translator" in body
    assert not (out / "babeldub-translator.md").read_bytes().startswith(b"\xef\xbb\xbf")

    packer = _frontmatter(out / "babeldub-packer.md")
    assert packer["name"] == "babeldub-packer"
    assert packer["tools"] == "Bash, Read"
    assert packer["model"] == "inherit"


def test_check_passes_after_generation_and_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roles, out, plugin = _fixture(tmp_path)
    assert _run(roles, out, plugin, "--check") == 1  # nothing generated yet
    assert _run(roles, out, plugin) == 0
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    assert _run(roles, out, plugin) == 0
    assert "written 0" in capsys.readouterr().out
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before
    assert _run(roles, out, plugin, "--check") == 0


def test_check_detects_hand_edits_and_role_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roles, out, plugin = _fixture(tmp_path)
    _run(roles, out, plugin)
    target = out / "babeldub-translator.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    assert _run(roles, out, plugin, "--check") == 1
    assert "babeldub-translator.md" in capsys.readouterr().err
    _run(roles, out, plugin)
    (roles / "packer.md").write_text(PACKER.replace("BabelDub packer", "BabelDub repacker"), encoding="utf-8")
    assert _run(roles, out, plugin, "--check") == 1


def test_stale_generated_agents_are_removed_but_foreign_files_kept(tmp_path: Path) -> None:
    roles, out, plugin = _fixture(tmp_path)
    _run(roles, out, plugin)
    (roles / "packer.md").unlink()
    (out / "babeldub-custom.md").write_text("---\nname: babeldub-custom\n---\nhand made\n", encoding="utf-8")
    assert _run(roles, out, plugin, "--check") == 1
    assert _run(roles, out, plugin) == 0
    assert not (out / "babeldub-packer.md").exists()
    assert (out / "babeldub-custom.md").exists()


def test_plugin_manifest_must_list_every_agent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    roles, out, plugin = _fixture(tmp_path)
    plugin.write_text(
        json.dumps({"name": "x", "agents": ["./claude/agents/babeldub-packer.md"]}), encoding="utf-8"
    )
    assert _run(roles, out, plugin) == 0
    assert "babeldub-translator.md" in capsys.readouterr().err
    assert _run(roles, out, plugin, "--check") == 1


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("# no frontmatter\n", "frontmatter"),
        ("---\nname: babeldub-translator\ntools: Bash\n---\nbody\n", "description"),
        ("---\nname: Bad Name\ndescription: x\n---\nbody\n", "name"),
        ("---\nname: babeldub-other\ndescription: x\n---\nbody\n", "does not match"),
        ("---\nname: babeldub-translator\ndescription: x\ntools: 5\n---\nbody\n", "tools"),
        ("---\nname: [unclosed\n---\nbody\n", "YAML"),
    ],
)
def test_invalid_roles_exit_2(
    tmp_path: Path, content: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    roles, out, plugin = _fixture(tmp_path)
    (roles / "translator.md").write_text(content, encoding="utf-8")
    assert _run(roles, out, plugin) == 2
    assert message in capsys.readouterr().err
    assert not out.exists()


def test_missing_roles_folder_exits_2(tmp_path: Path) -> None:
    assert _run(tmp_path / "nope", tmp_path / "out", tmp_path / "plugin.json") == 2


def test_real_roles_generate_cleanly(tmp_path: Path) -> None:
    """The repository role files must parse and produce one agent each (written to a temp folder)."""
    agents = gen_agents.collect(gen_agents.ROLES_DIR)
    assert agents
    out = tmp_path / "agents"
    args = [
        "--roles",
        str(gen_agents.ROLES_DIR),
        "--out",
        str(out),
        "--plugin-json",
        str(tmp_path / "none.json"),
    ]
    assert gen_agents.main(args) == 0
    for agent in agents:
        meta = _frontmatter(out / agent.filename)
        assert meta["name"] == f"babeldub-{agent.role}"
        assert meta["description"] and meta["model"]
