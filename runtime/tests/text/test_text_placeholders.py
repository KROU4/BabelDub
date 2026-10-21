from babeldub.text.placeholders import check_placeholders, describe, extract_placeholders, has_errors


def test_extract_in_order_and_normalised():
    text = "{ColorStart}{CharacterName}:{ColorEnd} Press <b>{key}</b> to open %s menu\\nNow [29]!"
    assert extract_placeholders(text) == [
        "{ColorStart}",
        "{CharacterName}",
        "{ColorEnd}",
        "<b>",
        "{key}",
        "</b>",
        "%s",
        "\\n",
        "[29]",
    ]


def test_smart_strings_reduce_to_variable_and_formatter():
    src = "You have {count:plural:one {# coin}|other {# coins}} and {0:N2} gold"
    assert extract_placeholders(src) == ["{count:plural}", "{0:N2}"]
    tgt = "У тебя {count:plural:one {# монета}|few {# монеты}|other {# монет}} и {0:N2} золота"
    assert check_placeholders(src, tgt) == []


def test_literal_percent_and_escaped_braces_are_not_placeholders():
    assert extract_placeholders("Use 100% of it, 50% discount, %% sign, {{literal}}") == []


def test_printf_variants():
    assert extract_placeholders("%d items, %.2f%%, %1$s and %@") == ["%d", "%.2f", "%1$s", "%@"]


def test_tags_with_attributes_and_br():
    text = "<color=#efcf5f>gold</color><br/><sprite name=\"coin\">"
    assert extract_placeholders(text) == ["<color=#efcf5f>", "</color>", "<br>", '<sprite name="coin">']


def test_real_newline_is_a_soft_token():
    problems = check_placeholders("Line one\nLine two", "Строка один, строка два")
    assert problems == [{"severity": "warn", "kind": "missing", "token": "\n", "expected": 1, "found": 0}]
    assert not has_errors(problems)


def test_missing_and_extra_are_errors_reordering_is_not():
    src = "{0} hit {1} with <b>force</b>"
    assert check_placeholders(src, "<b>Сильно</b> {1} ударил {0}") == []
    problems = check_placeholders(src, "{0} ударил сильно")
    assert has_errors(problems)
    kinds = {(p["kind"], p["token"]) for p in problems}
    assert kinds == {("missing", "{1}"), ("missing", "<b>"), ("missing", "</b>")}
    extra = check_placeholders("Hello", "Привет {name}")
    assert extra[0]["kind"] == "extra" and extra[0]["severity"] == "error"
    assert "{name}" in describe(extra)


def test_stage_directions_are_soft_bbcode_is_strict():
    dropped = check_placeholders("[snoring] Zzz…", "Хррр…")
    assert dropped[0]["token"] == "[snoring]" and dropped[0]["severity"] == "warn"
    assert has_errors(check_placeholders("[b]Hi[/b] [29]", "Привет [29]"))
    assert has_errors(check_placeholders("Hi [player_name]", "Привет"))
    assert has_errors(check_placeholders("[color=red]Hot[/color]", "Горячо"))


def test_bracket_text_with_spaces_is_not_a_tag():
    assert extract_placeholders("[Press E] to talk, a < b > c") == []
