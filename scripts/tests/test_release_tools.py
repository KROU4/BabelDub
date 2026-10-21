"""Tests for scripts/bump_version.py, scripts/validate_skills.py and scripts/build_release.py.

Every test builds a small synthetic repository in tmp_path; nothing reads the real repo.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import build_release  # noqa: E402
import bump_version  # noqa: E402
import validate_skills  # noqa: E402

VERSION_BUMP = {
    "files": [
        {"path": ".claude-plugin/plugin.json", "kind": "json", "key": "version"},
        {"path": ".codex-plugin/plugin.json", "kind": "json", "key": "version"},
        {"path": "plugin.json", "kind": "json", "key": "version"},
        {"path": "gemini-extension.json", "kind": "json", "key": "version"},
        {
            "path": "runtime/pyproject.toml",
            "kind": "regex",
            "pattern": '(?ms)^\\[project\\]\\s*$.*?^version\\s*=\\s*"(?P<version>[^"]+)"',
        },
        {
            "path": "runtime/src/babeldub/__init__.py",
            "kind": "regex",
            "pattern": '(?m)^__version__\\s*=\\s*"(?P<version>[^"]+)"',
        },
        {
            "path": "runtime/uv.lock",
            "kind": "regex",
            "pattern": '(?m)^name = "babeldub"\\r?\\nversion = "(?P<version>[^"]+)"',
            "optional": True,
        },
        {"path": "skills/*/SKILL.md", "kind": "frontmatter", "key": "metadata.babeldub-cli", "optional": True},
    ]
}

SKILL_MD = """---
name: {name}
description: "Synthetic test skill {name} used to check the BabelDub packaging scripts end to end."
license: MIT
compatibility: "Any host with shell access."
metadata:
  babeldub-cli: "{version}"
---

# {name}

Read [the guide](references/guide.md) and `references/guide.md` when needed.
"""


def dump(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def write(path: Path, text: str, newline: str = "\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline=newline)


def make_repo(root: Path, version: str = "0.1.0", skills: tuple[str, ...] = ("babeldub-alpha", "babeldub-beta")) -> Path:
    author = {"name": "KROU4", "url": "https://github.com/KROU4"}
    dump(root / ".version-bump.json", VERSION_BUMP)
    dump(
        root / ".claude-plugin/plugin.json",
        {
            "name": "babeldub",
            "version": version,
            "description": "test",
            "author": author,
            "homepage": "https://github.com/KROU4/BabelDub",
            "agents": ["./claude/agents/babeldub-scout.md"],
            "hooks": "./claude/hooks.json",
        },
    )
    dump(
        root / ".claude-plugin/marketplace.json",
        {
            "name": "babeldub",
            "owner": {"name": "KROU4"},
            "description": "test",
            "plugins": [{"name": "babeldub", "source": "./", "description": "test"}],
        },
    )
    dump(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "babeldub",
            "version": version,
            "author": author,
        },
    )
    dump(
        root / ".codex-plugin/plugin.json",
        {
            "name": "babeldub",
            "version": version,
            "skills": "./skills/",
            "interface": {"displayName": "BabelDub", "logo": "./assets/logo.png", "defaultPrompt": ["hi"]},
        },
    )
    dump(root / "gemini-extension.json", {"name": "babeldub", "version": version, "contextFileName": "GEMINI.md"})
    write(root / "GEMINI.md", "# BabelDub\n")
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "assets/logo.png").write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    write(root / "claude/agents/babeldub-scout.md", "---\nname: babeldub-scout\ndescription: Scout role.\n---\nBody\n")
    dump(root / "claude/hooks.json", {"hooks": {"PreToolUse": []}})
    for name in skills:
        write(root / f"skills/{name}/SKILL.md", SKILL_MD.format(name=name, version=version))
        write(root / f"skills/{name}/references/guide.md", "# Guide\n")
    write(
        root / "runtime/pyproject.toml",
        f'[build-system]\nrequires = ["hatchling"]\n\n[project]\nname = "babeldub"\nversion = "{version}"\n',
    )
    write(root / "runtime/src/babeldub/__init__.py", f'"""pkg"""\n\n__version__ = "{version}"\n')
    write(root / "runtime/uv.lock", f'version = 1\n\n[[package]]\nname = "babeldub"\nversion = "{version}"\n')
    write(root / "runtime/tests/test_x.py", "def test_x():\n    assert True\n")
    write(root / "scripts/install.ps1", "Write-Host 'install'\r\n", newline="")
    write(root / "scripts/install.sh", "#!/usr/bin/env bash\necho install\n")
    write(root / "LICENSE", "MIT License\n")
    write(root / "THIRD_PARTY_NOTICES.md", "# Notices\n")
    write(root / "docs/LEGAL.md", "# Legal\n")
    write(
        root / "CHANGELOG.md",
        f"# Changelog\n\n## [Unreleased]\n\n## [{version}] - 2026-10-21\n\n### Added\n\n- First release.\n\n"
        f"[{version}]: https://example.invalid/v{version}\n",
    )
    return root


# ---------------------------------------------------------------- bump_version


def test_bump_collects_every_place(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    report = bump_version.collect(root)
    assert report.versions == {"0.1.0"}
    assert len(report.found) == 9  # 4 manifests + pyproject + __init__ + uv.lock + 2 skills
    assert bump_version.check(report) == []
    assert bump_version.main(["--root", str(root), "--check", "--expect", "v0.1.0"]) == 0


def test_bump_rewrites_only_the_version(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    crlf = root / "runtime/src/babeldub/__init__.py"
    crlf.write_bytes(b'"""pkg"""\r\n\r\n__version__ = "0.1.0"\r\n')
    before = (root / ".claude-plugin/plugin.json").read_text(encoding="utf-8")

    assert bump_version.main(["--root", str(root), "minor"]) == 0

    after = (root / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    assert after == before.replace('"version": "0.1.0"', '"version": "0.2.0"')
    assert crlf.read_bytes() == b'"""pkg"""\r\n\r\n__version__ = "0.2.0"\r\n'
    assert 'babeldub-cli: "0.2.0"' in (root / "skills/babeldub-alpha/SKILL.md").read_text(encoding="utf-8")
    assert 'name = "babeldub"\nversion = "0.2.0"' in (root / "runtime/uv.lock").read_text(encoding="utf-8")
    assert bump_version.collect(root).versions == {"0.2.0"}


def test_bump_dry_run_and_explicit_version(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert bump_version.main(["--root", str(root), "1.0.0", "--dry-run"]) == 0
    assert bump_version.collect(root).versions == {"0.1.0"}
    assert bump_version.main(["--root", str(root), "v1.0.0rc1"]) == 0
    assert bump_version.collect(root).versions == {"1.0.0rc1"}


def test_bump_rejects_bad_version(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert bump_version.main(["--root", str(root), "1.0"]) == 1
    assert bump_version.collect(root).versions == {"0.1.0"}


def test_bump_check_finds_mismatch_and_missing(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    data = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    data["version"] = "0.1.1"
    dump(root / "plugin.json", data)
    errors = bump_version.check(bump_version.collect(root))
    assert any("versions differ" in e for e in errors)
    assert bump_version.main(["--root", str(root), "--check"]) == 1

    (root / "gemini-extension.json").unlink()
    errors = bump_version.check(bump_version.collect(root))
    assert any("gemini-extension.json: file not found" in e for e in errors)


def test_bump_lists_prose_mentions_for_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_repo(tmp_path)
    cfg = dict(VERSION_BUMP, review=["skills/**/*.md"])
    dump(root / ".version-bump.json", cfg)
    skill = root / "skills/babeldub-beta/SKILL.md"
    write(skill, skill.read_text(encoding="utf-8") + "Expect `babeldub 0.1.0` here; 10.1.0 is unrelated.\n")
    managed = bump_version.managed_lines(bump_version.collect(root))
    assert "skills/babeldub-beta/SKILL.md:7" in managed
    assert bump_version.stale_mentions(root, "0.1.0", managed) == ["skills/babeldub-beta/SKILL.md:13"]

    assert bump_version.main(["--root", str(root), "patch", "--dry-run"]) == 0
    assert "SKILL.md:7" not in capsys.readouterr().out
    assert bump_version.main(["--root", str(root), "patch"]) == 0
    out = capsys.readouterr().out
    assert "review 1 prose mention(s) of 0.1.0" in out
    assert "skills/babeldub-beta/SKILL.md:13" in out
    assert "babeldub 0.1.0" in skill.read_text(encoding="utf-8")  # prose is left for a human


def test_bump_optional_entries_are_skipped(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "runtime/uv.lock").unlink()
    write(root / "skills/babeldub-alpha/SKILL.md", SKILL_MD.format(name="babeldub-alpha", version="0.1.0")
          .replace('metadata:\n  babeldub-cli: "0.1.0"\n', ""))
    report = bump_version.collect(root)
    assert bump_version.check(report) == []
    assert sum(1 for loc in report.locations if loc.skipped) == 2


# ---------------------------------------------------------------- validate_skills


def errors_of(root: Path) -> list[str]:
    res, _ = validate_skills.validate(root)
    return res.errors


def test_validate_clean_repo(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    res, skills = validate_skills.validate(root)
    assert res.errors == []
    assert skills == ["babeldub-alpha", "babeldub-beta"]
    assert validate_skills.main(["--root", str(root), "--json"]) == 0


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        ('name: babeldub-alpha\ndescription: "%s"\nargument-hint: x\n', "outside the Agent Skills set"),
        ('name: other-name\ndescription: "%s"\n', "must equal the folder name"),
        ('name: babeldub-alpha\ndescription: "too short"\n', "must be 50..1024"),
        ("name: babeldub-alpha\ndescription: Use it: when the user asks to translate a game end to end ok\n",
         "not strict YAML"),
        ('name: babeldub-alpha\ndescription: "%s <b>bold</b>"\n', "XML/HTML tags"),
        ('name: babeldub-alpha\ndescription: "%s"\nmetadata:\n  babeldub-cli: 1.5\n', "must be a string"),
    ],
)
def test_validate_skill_frontmatter_errors(tmp_path: Path, frontmatter: str, expected: str) -> None:
    root = make_repo(tmp_path)
    desc = "A long enough description for a synthetic skill used only in these tests."
    body = frontmatter % desc if "%s" in frontmatter else frontmatter
    write(root / "skills/babeldub-alpha/SKILL.md", f"---\n{body}---\n\n# Body\n")
    assert any(expected in e for e in errors_of(root)), errors_of(root)


def test_validate_body_length_and_links(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    skill = root / "skills/babeldub-alpha/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    write(skill, text + "See [missing](references/missing.md) and `references/absent.yaml`.\n")
    errs = errors_of(root)
    assert any("broken link: references/missing.md" in e for e in errs)
    assert any("mentions missing file: references/absent.yaml" in e for e in errs)

    write(skill, text + "line\n" * 501)
    assert any("max 500" in e for e in errors_of(root))


def test_validate_mentions_of_a_sibling_skill(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write(root / "skills/babeldub-alpha/references/roles/voice.md", "# Voice\n")
    skill = root / "skills/babeldub-beta/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    write(skill, text + "Role from the `babeldub-alpha` skill (`references/roles/voice.md`).\n")
    assert errors_of(root) == []
    write(skill, text + "Role file `references/roles/voice.md` without naming its skill.\n")
    assert any("mentions missing file: references/roles/voice.md" in e for e in errors_of(root))


def test_validate_links_inside_code_blocks_are_ignored(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    skill = root / "skills/babeldub-alpha/SKILL.md"
    write(skill, skill.read_text(encoding="utf-8") + "```text\n[x](nowhere.md)\n```\n")
    assert errors_of(root) == []


def test_validate_manifest_and_tree_problems(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    raw = (root / "gemini-extension.json").read_bytes()
    (root / "gemini-extension.json").write_bytes(b"\xef\xbb\xbf" + raw)
    (root / "bin").mkdir()
    (root / "bin/tool").write_text("x", encoding="utf-8")
    write(root / "CLAUDE.md", "x\n")
    (root / "fixtures").mkdir()
    (root / "fixtures/voice.bank").write_bytes(b"RIFF")
    (root / "fixtures/big.dat").write_bytes(b"0" * (1024 * 1024 + 1))
    write(root / "claude/agents/babeldub-extra.md", "---\nname: babeldub-extra\ndescription: Extra.\n---\n")
    data = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    data["agents"] = []
    dump(root / "plugin.json", data)
    entry = json.loads((root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    entry["plugins"][0]["version"] = "0.1.0"
    dump(root / ".claude-plugin/marketplace.json", entry)

    errs = "\n".join(errors_of(root))
    assert "gemini-extension.json: UTF-8 BOM" in errs
    assert "bin/:" in errs
    assert "CLAUDE.md:" in errs
    assert "fixtures/voice.bank: game asset" in errs
    assert "fixtures/big.dat: 1.0 MB file" in errs
    assert "not listed in 'agents'" in errs
    assert "closed Agent Plugins schema: agents" in errs
    assert "must not set version" in errs


def test_validate_allows_big_asset_png(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "assets/banner.png").write_bytes(b"\x89PNG" + b"0" * (1024 * 1024 + 10))
    assert errors_of(root) == []


# ---------------------------------------------------------------- build_release


def build(root: Path, out: Path) -> dict[str, object]:
    return build_release.build(root, out, checks=True)


def test_build_release_archives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1792540800")
    root = make_repo(tmp_path / "repo")
    (root / "runtime/src/babeldub/__pycache__").mkdir()
    (root / "runtime/src/babeldub/__pycache__/x.cpython-312.pyc").write_bytes(b"junk")
    (root / "runtime/.venv/Lib").mkdir(parents=True)
    (root / "runtime/.venv/Lib/site.py").write_text("junk", encoding="utf-8")
    (root / "dist").mkdir()
    (root / "dist/old.txt").write_text("old", encoding="utf-8")
    out = tmp_path / "out"

    result = build(root, out)
    assert result["version"] == "0.1.0"
    assert result["skills"] == ["babeldub-alpha", "babeldub-beta"]

    with zipfile.ZipFile(out / "babeldub-plugin-0.1.0.zip") as zf:
        names = zf.namelist()
        assert all(n.startswith("babeldub/") for n in names)
        assert "babeldub/.claude-plugin/plugin.json" in names
        assert "babeldub/runtime/tests/test_x.py" in names
        assert not any("__pycache__" in n or ".venv" in n or "/dist/" in n for n in names)
        info = zf.getinfo("babeldub/scripts/install.sh")
        assert (info.external_attr >> 16) & 0o777 == 0o755
        assert zf.read("babeldub/scripts/install.ps1") == b"Write-Host 'install'\r\n"

    with zipfile.ZipFile(out / "babeldub-skills-0.1.0.zip") as zf:
        top = "babeldub-skills-0.1.0/"
        assert f"{top}skills/babeldub-alpha/references/guide.md" in zf.namelist()
        assert f"{top}README.md" in zf.namelist()
        inner = zipfile.ZipFile(io.BytesIO(zf.read(f"{top}claude-ai/babeldub-beta.zip")))
        assert sorted(inner.namelist()) == ["babeldub-beta/SKILL.md", "babeldub-beta/references/guide.md"]

    with zipfile.ZipFile(out / "babeldub-kit-0.1.0.zip") as zf:
        names = set(zf.namelist())
        k = "BabelDub-Workspace/"
        for required in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "README-KIT.md", "install.ps1", "install.sh",
                         "LEGAL.md", "LICENSE", "runtime/pyproject.toml",
                         ".agents/skills/babeldub-alpha/SKILL.md", ".claude/skills/babeldub-beta/SKILL.md",
                         ".claude/agents/babeldub-scout.md"):
            assert k + required in names, required
        assert not any(n.startswith(k + "runtime/tests/") for n in names)
        assert zf.read(k + "CLAUDE.md") == b"@AGENTS.md\n"
        assert "`babeldub-beta`" in zf.read(k + "AGENTS.md").decode("utf-8")

    sums = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(sums) == 3
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    notes = (out / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert "- First release." in notes
    assert "https://example.invalid" not in notes


def test_build_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1792540800")
    root = make_repo(tmp_path / "repo")
    build(root, tmp_path / "a")
    build(root, tmp_path / "b")
    assert (tmp_path / "a/SHA256SUMS").read_bytes() == (tmp_path / "b/SHA256SUMS").read_bytes()


def test_build_refuses_inconsistent_repo(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    data = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    data["version"] = "9.9.9"
    dump(root / "plugin.json", data)
    with pytest.raises(build_release.ReleaseError, match="version check failed"):
        build(root, tmp_path / "out")
    assert build_release.main(["--root", str(root), "--out", str(tmp_path / "out")]) == 1


def test_build_needs_changelog_section(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    write(root / "CHANGELOG.md", "# Changelog\n\n## [0.0.9] - 2026-01-01\n\n- old\n")
    with pytest.raises(build_release.ReleaseError, match=r"no '## \[0.1.0\]' section"):
        build(root, tmp_path / "out")


def test_build_skips_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_repo(tmp_path / "repo")
    link = root / "skills/babeldub-alpha/references/link.md"
    try:
        link.symlink_to(root / "skills/babeldub-alpha/references/guide.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this system")
    result = build_release.build(root, tmp_path / "out", checks=False)
    assert any("symlink" in w for w in result["warnings"])  # type: ignore[union-attr]
    with zipfile.ZipFile(tmp_path / "out/babeldub-plugin-0.1.0.zip") as zf:
        assert "babeldub/skills/babeldub-alpha/references/link.md" not in zf.namelist()
