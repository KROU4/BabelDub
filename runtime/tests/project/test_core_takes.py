"""Alt takes / duplicates: leader choice (core/takes.py) and how status/query report followers."""

from babeldub.core import layout
from babeldub.core.jsonl import write_jsonl
from babeldub.core.takes import follower_map

from .conftest import make_line


def take(index, key, text_key="dlg_Intro_i05", flags=(), kind="dialogue", asset="fmod:Dialogue.bank", order=None):
    line = make_line(index, kind=kind, src="Hello there.")
    line["asset_id"] = asset
    line["entry"]["key"] = key
    line["text"]["key"] = text_key
    line["flags"] = list(flags)
    line["order"] = index if order is None else order
    return line


def test_leader_is_the_line_named_like_the_text_key():
    lines = [
        take(1, "dlg_Intro_i05_v1", flags=["alt_take"]),
        take(2, "dlg_Intro_i05_v4_BEST", flags=["alt_take"]),
        take(3, "dlg_Intro_i05"),
        take(4, "dlg_Other", "dlg_Other"),  # another text key: not part of the group
    ]
    follows = follower_map(lines)
    leader = lines[2]["id"]
    assert follows == {lines[0]["id"]: leader, lines[1]["id"]: leader}


def test_leader_fallbacks_unflagged_then_best_then_order():
    # no member is named like the text key: the unflagged one leads
    lines = [take(1, "x_v1", flags=["alt_take"]), take(2, "x_v2"), take(3, "x_v3", flags=["alt_take"])]
    assert set(follower_map(lines).values()) == {lines[1]["id"]}
    # all flagged: the BEST take leads (any case)
    lines = [take(1, "x_v1", flags=["alt_take"]), take(2, "x_v2_best", flags=["alt_take"])]
    assert follower_map(lines) == {lines[0]["id"]: lines[1]["id"]}
    # nothing else decides: the lowest order
    lines = [take(1, "x_v1", flags=["alt_take"], order=9), take(2, "x_v2", flags=["duplicate"], order=3)]
    assert follower_map(lines) == {lines[0]["id"]: lines[1]["id"]}


def test_a_take_without_audio_never_leads():
    empty = take(1, "dlg_Intro_i05", flags=["no_audio"])
    empty["audio"]["dur_s"] = 0.0
    real = take(2, "dlg_Intro_i05", flags=["duplicate"])
    assert follower_map([empty, real]) == {empty["id"]: real["id"]}


def test_groups_need_a_flag_one_asset_and_voiced_kinds():
    # same key without alt_take/duplicate flags: two distinct lines that share a subtitle
    assert follower_map([take(1, "a"), take(2, "b")]) == {}
    # different containers are different groups
    assert follower_map([take(1, "a", flags=["alt_take"], asset="x"), take(2, "b", asset="y")]) == {}
    # non-voiced kinds are ignored; lines without a text key too
    assert follower_map([take(1, "a", flags=["alt_take"], kind="sfx"), take(2, "b")]) == {}
    no_key = take(1, "a", flags=["duplicate"])
    no_key["text"] = None
    assert follower_map([no_key, take(2, "b")]) == {}


def test_status_and_query_count_followers(bd, project_with_lines):
    lines = [take(1, "dlg_Intro_i05"), take(2, "dlg_Intro_i05_v1", flags=["alt_take"]), take(3, "dlg_Other", "dlg_Other")]
    project = project_with_lines(lines)
    write_jsonl(project.path(layout.TRANSLATIONS), [{"id": ln["id"], "dub_text": "Привет.", "by": "agent", "rev": 1}
                                                     for ln in lines])
    code, out = bd("status")
    assert out["counts"]["voiced"] == 3 and out["counts"]["follows"] == 1
    code, out = bd("query", "--where", "follows!=null", "--fields", "id,follows")
    assert code == 0 and out["total"] == 1
    assert out["rows"] == [{"id": lines[1]["id"], "follows": lines[0]["id"]}]
