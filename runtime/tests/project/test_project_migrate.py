"""v0.1 projects carry the old post.tempo_min/tempo_max defaults in project.json: they are migrated on load."""

from babeldub.core import layout
from babeldub.core.jsonl import read_json, read_jsonl, write_json
from babeldub.core.project import Project


def _v01(tmp_path, post):
    write_json(tmp_path / layout.PROJECT_JSON, {"slug": "g-ru", "cli_version": "0.1.0", "post": post})
    return tmp_path


def test_old_tempo_defaults_are_migrated_once(tmp_path):
    root = _v01(tmp_path, {"tempo_min": 0.93, "tempo_max": 1.2, "tolerance": 0.03})
    project = Project.load(str(root))
    assert project.setting("post", "tempo_min") == 1.0 and project.setting("post", "tempo_max") == 1.15
    stored = read_json(root / layout.PROJECT_JSON)
    assert stored["post"]["tempo_min"] == 1.0 and stored["post"]["tolerance"] == 0.03
    assert [m["setting"] for m in stored["migrations"]] == ["post.tempo_min", "post.tempo_max"]
    events = read_jsonl(root / layout.EVENTS)
    assert events[-1]["stage"] == "project" and events[-1]["status"] == "migrated"
    # a value set again after the migration is the user's choice: it stays
    stored["post"]["tempo_min"] = 0.93
    write_json(root / layout.PROJECT_JSON, stored)
    assert Project.load(str(root)).setting("post", "tempo_min") == 0.93


def test_user_values_are_not_touched(tmp_path):
    root = _v01(tmp_path, {"tempo_min": 0.9, "tempo_max": 1.3})
    project = Project.load(str(root))
    assert project.setting("post", "tempo_min") == 0.9 and project.setting("post", "tempo_max") == 1.3
    assert "migrations" not in read_json(root / layout.PROJECT_JSON)
    assert not (root / layout.EVENTS).exists()
