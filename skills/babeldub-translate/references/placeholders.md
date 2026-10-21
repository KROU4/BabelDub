# Placeholders, tags and Unity Smart Strings

Read this before the first batch and when `batch submit` fails with "placeholders differ from src".

## 1. What the CLI treats as a placeholder

`babeldub batch make` puts a `placeholders` field into every line of the batch: everything found in the source text. `batch submit` checks the same items in `dub_text`, `sub_text` and every variant in `variants`.

| Kind | Examples | Where it occurs |
|---|---|---|
| curly braces | `{0}`, `{name}`, `{ColorStart}`, `{b}`, `{/b}`, `{w=0.5}` | .NET formatting, Unity Localization, Ren'Py |
| Smart Strings and formatters | `{count:plural:…}`, `{0:choose(1\|2\|3):…}` | Unity Localization |
| printf | `%s`, `%d`, `%.2f`, `%1$s` (`%%` is just a percent sign) | C/C++, Unreal, many engines |
| angle-bracket tags | `<b>`, `</b>`, `<color=#fff>`, `<br>`, `<sprite name=x>`, `<link=id>` | TextMeshPro, uGUI, HTML-like markup |
| square-bracket tags without spaces | `[b]`, `[/color]`, `[player]`, `[29]` | BBCode, Ren'Py variables |
| line breaks | `\n` (two characters), a real newline, `<br>` | everywhere |

Check rules:

- **Error** (exit code 2): a placeholder is lost, an extra one is added, or one is changed (`{name}` → `{имя}`, `%s` → `% s`, `<color=red>` → `<цвет=red>`).
- **Warning** (does not block): the number of line breaks (`\n`, `<br>`) differs; a translation may break lines differently.
- Never change the order of positional `%s` without an index: the values would land in the wrong places. If the grammar needs a different order and the source uses `%1$s`/`{0}`, you may reorder: the index keeps the link.

## 2. Smart Strings (Unity Localization)

The CLI compares `{Var:formatter:…}` only by variable name and formatter (`{count:plural}`): the forms inside depend on the language, and you write them.

- **plural** for Russian needs three forms (one / few / many): `{count:plural:{} яблоко|{} яблока|{} яблок}`. How Smart Strings picks the form for a locale has not been verified on a specific game's strings ❓: keep the structure of the source and the number of forms the target language needs.
- **choose** (`{0:choose(1|2|3):a|b|c}`): do not translate the choice keys in parentheses, only the options.
- Do not touch nested scopes (`{Player.Name}`, `{0:{}}`).

## 3. `dub_text` versus `sub_text`

- `dub_text` is what gets spoken. **It keeps the placeholders and tags of the source**: `batch submit` checks them in both `dub_text` and `variants`. Before synthesis the CLI strips markup and placeholders itself so they are not spoken. So markup in `dub_text` does not harm the voice, while losing it is a validation error (exit code 2).
- `sub_text` is the subtitle. Write it only if it must differ from `dub_text`: the CLI uses `dub_text` when there is no `sub_text`. In v0.1 subtitles are not written into the game.

## 4. Common mistakes

| Mistake | Correct |
|---|---|
| translated a variable name: `{name}` → `{имя}` | never translate variable names |
| "fixed" the case: `{ColorStart}` → `{colorstart}` | byte for byte |
| swapped tags: `<b>…<i>…</b></i>` | nesting as in the source |
| added a space inside: `% s`, `{ 0 }` | unchanged |
| deleted a "redundant" `\n` | you may move it, but better keep it: the game UI may rely on it |
| translated text inside the `<link=id>` attribute | translate only the visible text between tags |

## 5. A placeholder inside a spoken phrase

Before synthesis all placeholders are removed from `dub_text`, so the phrase is spoken without the substituted value.

- **Pure markup** (`<b>`, `{ColorStart}{CharacterName}:{ColorEnd}` before a line): nothing to do.
- **A meaningful value** (`{0}` coins, `{item}`, `{name}`). There is one recording for every value, so the original voice-over usually does not speak the value; check against `src` and the context. Build the phrase so that it sounds natural without the value, and keep the placeholder in its place in the text. Bad: «У тебя {0} монет» ("You have {0} coins"), which is spoken as «У тебя монет» ("You have coins"). Better: «Вот твои монеты: {0}» ("Here are your coins: {0}"), spoken as «Вот твои монеты:». If no natural phrasing works, mark it in `notes`: QA checks pronunciation by CER, and the report shows which lines are questionable.
