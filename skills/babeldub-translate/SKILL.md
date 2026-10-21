---
name: babeldub-translate
description: "Translate or adapt game dialogue for dubbing in the original characters' voices: keep the emotional shape of every line so it fits the cloned voice and delivery, fit the syllable budget of the original duration, preserve placeholders, tags and Smart Strings, follow the glossary, gender and formal or informal address. Use for the translate stage, glossary, style bible and shorten requests: переведи реплики, адаптируй перевод под озвучку, глоссарий, ты или вы, сократи реплику."
license: MIT
compatibility: "Any agent that can read and write files; babeldub CLI 0.1.0 for batch make, next and submit (install via the babeldub-doctor skill). Web chats without the CLI can translate an exported batch file by the same rules. No GPU needed."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: текст для дубляжа

Скилл стадии `translate`: глоссарий, style bible, перевод или адаптация пакетов реплик, сокращение по решениям QA. Текст, который ты пишешь, произнесёт **голос оригинального героя**, клонированный по его же реплике: тот же тембр, та же эмоция, тот же темп. Поэтому перевод должен звучать естественно на целевом языке, сохранить эмоциональную форму оригинала и уложиться в его длительность.

Скилл дополняет роли `lexicon` и `translator` из скилла `babeldub-localize` (`references/roles/`). CLI никогда не переводит сам: он решает режим, режет пакеты, проверяет то, что написал агент, и сливает это в канон.

## Главные правила перевода для дубляжа

1. **Эмоциональная форма как в оригинале.** Восклицание остаётся восклицанием, вопрос вопросом. Сохраняй запинки и повторы («Я… я не знаю»), обрывы, смех и междометия, регистр и уровень брани (без цензуры и без усиления). Голосовой промпт — оригинальная реплика: текст «спокойнее» оригинала будет спорить с её интонацией.
2. **Длина.** Целься в `budget_syll` — столько слогов влезает в длительность оригинала. Для `fixed_length: true` (катсцены, видео) бюджет обязателен. Не влезает — дай более короткие `variants`. Подробно — [references/length-budget.md](references/length-budget.md).
3. **Два текста.** `dub_text` — то, что произносится: числа словами, сокращения раскрыты, трудные слова по `pronounce` глоссария, без ремарок. `sub_text` — субтитр; пиши его, только если он должен отличаться от `dub_text` (в v0.1 субтитры в игре не меняются).
4. **Плейсхолдеры и теги** (`{0}`, `{name}`, `%s`, `<color=…>`, `[b]`, Unity Smart Strings) переносятся байт в байт **и в `dub_text`, и в `sub_text`, и в каждый вариант**: `batch submit` проверяет все три, а перед синтезом CLI сам удаляет разметку, чтобы она не прозвучала. Подробно — [references/placeholders.md](references/placeholders.md).
5. **Имена и термины — только из глоссария.** Новое не придумывай: переведи по смыслу и вынеси в `notes` или в `new_terms` ответа роли.
6. **Грамматика целевого языка:** род говорящего и адресата, «ты/вы», согласование. Для русского — [references/russian.md](references/russian.md).

## Ход стадии

### 1. Режим

```text
babeldub translate plan
```

Вывод: `mode`, `need_agent`, `lines`, `with_official`, `official_share`, `no_text`, `glossary`, `style_bible`. Режимы:

| `mode` | Что значит | Что делать |
|---|---|---|
| `official` | официальный перевод на целевой язык есть у ≥ 95 % реплик; озвучиваем его как есть | `babeldub translate use-official`; оставшиеся без официального текста (`missing`) переводит агент |
| `translate` | официального текста нет или его мало; переводим с исходного | глоссарий → пакеты |
| `adapt` | официальный текст есть, но адаптируем его под озвучку (длина, произносимость) | глоссарий → пакеты с `tgt_official` |

`auto` (по умолчанию) выбирает только между `official` и `translate`. Режим `adapt` включается явно: `babeldub set translation.mode=adapt` (или `init --mode adapt`). Когда что выбирать — [references/adapt-vs-translate.md](references/adapt-vs-translate.md).

### 2. Глоссарий и style bible (роль lexicon)

1. Выборки сцен: `babeldub sample --scene <сцена> --n 30 --out translate/samples/<сцена>.jsonl`; сцены и говорящих найди через `babeldub query --where kind=dialogue --fields scene,speaker_hint --limit 50`.
2. Глоссарий — `translate/glossary.agent.jsonl` (формат — [references/glossary-format.md](references/glossary-format.md)), затем `babeldub glossary apply`: CLI проверит строки и сольёт их в канон `translate/glossary.jsonl`. Только канонический глоссарий попадает в заголовки пакетов.
3. Style bible — `translate/style_bible.md`, коротко и по разделам (там же, раздел 4). CLI вставляет её в заголовок каждого пакета (до 8000 символов).
4. Глоссарий и style bible делаются **до** `batch make`: пакеты, нарезанные раньше, их не получат (`batch make --force` перережет ещё не сданные пакеты).

### 3. Пакеты

```text
babeldub batch make [--size 60]
babeldub batch next
babeldub batch submit <bid>
```

- `batch make` режет реплики без нужного перевода на пакеты по сценам (вывод `batches[]`, `open`).
- `batch next` выдаёт первый несданный пакет: `batch`, `state` (`todo`, `ready`, `error`), `in`, `out`, `remaining`. Пустой `batch` — пакеты кончились.
- Переводчик читает **только свой** `<bid>.in.jsonl` целиком, пишет `<bid>.out.jsonl` рядом и выполняет `batch submit <bid>`. Формат обоих файлов — [references/batch-format.md](references/batch-format.md).
- На хостах с субагентами до 4 переводчиков параллельно, по пакету на каждого.

Реакция на `batch submit`:

| Код | Что значит | Действие |
|---|---|---|
| 0 | пакет слит | следующий `batch next` |
| 2 | `errors[]`: нет `id`, пустой `dub_text`, сломаны плейсхолдеры, невалидный JSON; подробности в `errors_file` | исправить свой `.out.jsonl` и сдать снова, до 3 итераций |
| 3 | `over_budget[]`: строки длиннее бюджета больше чем на 15 % | дать более короткие `variants` и сдать снова (1 итерация); если короче без потери смысла нельзя — оставить, подгонку довершит `post` |

Повторная сдача изменённого `.out.jsonl` создаёт новую ревизию перевода; неизменённый файл второй раз не сливается.

### 4. Проверка глоссария

```text
babeldub glossary check
```

Код 3 — `violations[]` (термин глоссария есть в исходнике, а в `dub_text` его перевода нет), полный список в `translate/glossary_violations.jsonl`. Исправление: `babeldub batch make --ids <id1>,<id2>` (новый пакет с этими репликами и их текущим текстом) → переводчик → `batch submit`. Ложное срабатывание (термин употреблён в другом смысле) — объясни в отчёте.

### 5. Сокращение и перевод заново после QA

Решения QA `shorten` и `retranslate` возвращают реплики переводчику:

```text
babeldub batch make --from-decisions
```

В пакете у таких реплик есть `task` (`shorten` или `retranslate`) и `current` — текущий текст. Для `shorten` сокращай с учётом фактического `ratio` из заметки QA; для `retranslate` убирай то, что мешает произношению (числа цифрами, латиница, аббревиатуры, длинные составные слова). Затем обычный `batch next` → `batch submit`; `babeldub synth` подхватит новый текст как новую попытку.

## Чего не делать

- Не переводить «литературно» в ущерб длине и интонации: это дубляж, а не субтитры.
- Не добавлять ремарки в скобках («(смеётся)»): их нельзя озвучить, а модель прочитает их вслух.
- Не править `translate/translations.jsonl`, `glossary.jsonl` и `.in.jsonl`: канон пишет CLI.
- Не читать другие пакеты и файлы проекта целиком; данные — через `query` и `sample`.
- Не менять имена, уже закреплённые глоссарием или официальным переводом.

## Справочники

- Перед первым пакетом и при ошибках плейсхолдеров — [references/placeholders.md](references/placeholders.md).
- При `over_budget[]`, `fixed_length`, `shorten` — [references/length-budget.md](references/length-budget.md).
- При переводе на русский (род, «ты/вы», «ё», ударения, числа, сокращения для дубляжа) — [references/russian.md](references/russian.md).
- Роль lexicon: формат глоссария и style bible — [references/glossary-format.md](references/glossary-format.md).
- Формат `.in.jsonl` и `.out.jsonl`, поля и примеры — [references/batch-format.md](references/batch-format.md).
- Выбор между `official`, `adapt` и `translate`, правила адаптации официального текста — [references/adapt-vs-translate.md](references/adapt-vs-translate.md).
