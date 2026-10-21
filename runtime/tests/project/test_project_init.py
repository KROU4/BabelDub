from pathlib import Path

from babeldub.core import layout
from babeldub.core.games import active_project, load_registry
from babeldub.core.jsonl import read_json


def test_init_creates_project_registry_and_active(bd, game, home):
    code, out = bd("init", game, "--lang", "ru")
    assert code == 0, out
    root = Path(out["project"])
    assert root == home / "projects" / "low-budget-repairs"
    assert out["slug"] == "low-budget-repairs" and out["next"] == "babeldub scan" and out["created"]
    data = read_json(root / layout.PROJECT_JSON)
    assert data["_schema"] == "babeldub.project/1"
    assert data["target_lang"] == "ru" and data["game_name"] == "Low-Budget Repairs"
    assert data["cast"]["policy"] == "main" and data["cast"]["main_share"] == 0.7
    assert data["translation"]["mode"] == "auto"
    assert data["tts"]["engine"] == "qwen3tts-fast" and data["tts"]["ref_strategy"] == "auto"
    assert Path(data["game_dir"]) == game.resolve()
    progress = read_json(root / layout.PROGRESS)
    assert progress["init"]["status"] == "done"
    assert active_project() == root.resolve()
    games = load_registry()["games"]
    assert games[0]["slug"] == "low-budget-repairs" and Path(games[0]["game_dir"]) == game.resolve()


def test_init_is_idempotent_and_refuses_other_language(bd, game):
    first = bd("init", game, "--lang", "ru")[1]
    code, again = bd("init", game, "--lang", "ru")
    assert code == 0 and not again["created"] and again["project"] == first["project"]
    code, other = bd("init", game, "--lang", "pl")
    assert code == 2 and "--slug" in other["hint"]
    code, pl = bd("init", game, "--lang", "pl", "--slug", "lbr-pl")
    assert code == 0 and pl["engine"] == "chatterbox" and pl["slug"] == "lbr-pl"


def test_init_cast_list_and_locale(bd, game, tmp_path):
    code, out = bd("init", game, "--lang", "pt-BR", "--chars", "Zbyszek, Maciek", "--mode", "translate",
                   "--project-dir", tmp_path / "proj")
    assert code == 0
    data = read_json(tmp_path / "proj" / layout.PROJECT_JSON)
    assert data["cast"] == {"policy": "list", "list": ["Zbyszek", "Maciek"], "main_share": 0.7}
    assert data["target_lang"] == "pt" and data["target_locale"] == "pt-BR"
    assert data["translation"]["mode"] == "translate"


def test_init_errors(bd, game, tmp_path):
    assert bd("init", tmp_path / "missing", "--lang", "ru")[0] == 2
    assert bd("init", game, "--lang", "russian!")[0] == 2
    assert bd("init", game, "--lang", "ru", "--cast", "list")[0] == 2


def test_init_unsupported_language_warns(bd, game):
    code, out = bd("init", game, "--lang", "uk")
    assert code == 3 and out["ok"]
    assert "uk" in out["warnings"][0]


def test_games_lists_registry(bd, game):
    bd("init", game, "--lang", "ru")
    code, out = bd("games")
    assert code == 0
    assert out["games"][0]["active"] and out["games"][0]["game_exists"] and out["games"][0]["project_exists"]
