# Конвейер BabelDub v0.1: стадии, команды, коды выхода, артефакты

Читай этот файл перед стадией 1 и каждый раз, когда вывод стадии непонятен. Команды и флаги — как в CLI `babeldub` 0.1.0; других не используй. Почти каждый вывод содержит поле `next` — подсказку CLI о следующем шаге. Если `next` расходится с этим файлом, верь `next`: он видит фактическое состояние проекта.

## 1. Общие правила вызова

- Формат: `babeldub <команда> [аргументы] [--project <папка|slug>] [--pretty]`.
- Без `--project` CLI берёт активный проект (последний `init`) или переменную `BABELDUB_PROJECT`.
- Флаги стадий там, где они осмыслены: `--limit N`, `--ids id1,id2`, `--force`, `--dry-run`.
- stdout — ровно один компактный JSON (обычно до 2 КБ). Поле `ok` равно `true` при кодах 0 и 3. Длинные данные лежат в файлах проекта, JSON указывает на них.
- Стадии идемпотентны. `--force` пересчитывает уже сделанное; без причины его не используй. У `install`, `uninstall`, `reapply` флаг `--force` отключает защиту (запущенная игра, античит) — его не используй никогда.
- Прогресс хранится в `state/progress.json`, журнал — в `state/events.jsonl`. Читай их только через `babeldub status`.

## 2. Коды выхода

| Код | Константа | Значение | Общая реакция |
|---|---|---|---|
| 0 | `EXIT_OK` | стадия завершена, внимания не нужно | следующая стадия |
| 1 | `EXIT_ERROR` | неожиданная ошибка: поля `error`, `where` | лог стадии в `logs/`, `troubleshooting.md`; одна повторная попытка, если сбой разовый; иначе остановка с отчётом |
| 2 | `EXIT_USAGE` | неверные аргументы, не пройдена предыдущая стадия, невалидный файл агента | прочитать `hint`; выполнить недостающую стадию или исправить файл агента; аргументы сверить с этим файлом |
| 3 | `EXIT_ATTENTION` | стадия завершена, но есть элементы на решение | передать элементы роли стадии; после её работы продолжать |
| 4 | `EXIT_ENV` | не хватает окружения | скилл `babeldub-doctor`: `babeldub doctor` → `babeldub setup` |

Стадия в состоянии `attention` считается пройденной: следующие стадии запускаются. Человеку коды не пересказывай: он получает итог в отчёте.

## 3. Стадии

Порядок стадий в CLI: `init → scan → extract → link → cast → translate → synth → post → qa → build → verify → install`. Стадия, запущенная раньше предыдущей, вернёт код 2 с подсказкой.

### 3.0. Окружение (вне проекта)

| Команда | Что делает | Ключевые поля |
|---|---|---|
| `babeldub doctor` | ОС, Python, утилиты, GPU (имя, VRAM, драйвер), место на диске, venv движков, модели; рекомендует тир | `tier`, `gpu`, `missing[]`, `optional_missing[]`, `todo[]`, `warnings[]`, `install{}`, `next` |
| `babeldub setup [--engines qwen3tts,qc] [--tools] [--models auto] [--dry-run]` | venv движков, пакеты, веса моделей, внешние утилиты (vgmstream) | `planned[]`, `download_gb`, `blocked[]`, `done[]`, `failed[]` |
| `babeldub models list` / `babeldub models fetch <name>` | каталог моделей с лицензиями и статусом | `models[]` |

`doctor`: код 4 — нет обязательных утилит (`missing[]`: uv, ffmpeg, ffprobe; команды установки — в `install{}`); код 3 — не хватает venv или моделей (`todo[]`) → `setup`. Подробности — в скилле `babeldub-doctor`.

### 3.1. `init`: проект

```text
babeldub init "<папка игры>" --lang <код> [--cast all|main|list] [--chars A,B] [--mode auto] [--slug <slug>]
```

- Создаёт `project.json`, регистрирует игру в реестре `games.json` (по нему хук-охранник защищает папку игры) и делает проект активным.
- Вывод: `project`, `slug`, `game_dir`, `target_lang`, `cast`, `mode`, `engine`, `next`. Код 3 — есть предупреждения (например, движок синтеза не поддерживает язык).
- `--mode`: `auto` (по умолчанию) | `translate` | `adapt` | `official`.
- Код 2: папка не найдена, неверный код языка (ISO 639-1: `ru`, `pl`, `pt-BR` …) или `--cast list` без `--chars`.

### 3.2. `scan`: инвентаризация

```text
babeldub scan
```

- Определяет движок, middleware, текстовые системы и античит по сигнатурам, спрашивает все адаптеры, пишет `inventory/recon.json`, `inventory/assets.jsonl`, `inventory/report.md`.
- Вывод: `engine{id, version, backend}`, `middleware[]`, `text_systems[]`, `voice_assets[]`, `text_assets[]`, `counts{}`, `level`, `warnings[]`, `report`, `next`.
- `level`: A — автоматически; B — нужен пользовательский инструмент или мод-лоадер; C — только чтение и инструкции.
- `inventory/report.md`, раздел «Что читать дальше» — справочники для найденного (по ним работает скилл `babeldub-recon`).
- Код 3: есть предупреждения (неопознанный движок, античит, ошибки адаптеров). Роль scout разбирает их по справочникам `babeldub-recon`. Античит → стоп.

### 3.3. `extract`: реплики и оригинальный звук

```text
babeldub extract [--kinds dialogue,cutscene] [--limit N]
```

- `list_lines()` → `inventory/lines.jsonl` (канон реплик); `extract_audio()` → `audio/orig/<uid>.wav`; `read_texts()` → `inventory/texts.jsonl`.
- Вывод: `lines`, `by_kind{}`, `minutes`, `texts`, `locales[]`, `next`.
- `kind`: `dialogue | cutscene | bark | sfx | music | ambience | ui | unknown`. Озвучиваются только `dialogue`, `cutscene`, `bark`.
- Код 3: есть реплики `unknown` или ошибки декодирования отдельных записей. Scout смотрит их через `babeldub query --where kind=unknown --fields id,entry.key,audio.dur_s --limit 30`.
- Код 4: нет декодера (обычно `vgmstream-cli`) → `babeldub setup --tools`.

### 3.4. `link`: текст ↔ аудио

```text
babeldub link
```

- Связывает реплики с текстами: точный ключ, затем нормализованный ключ, затем имя файла. Заполняет `text` в `lines.jsonl` и `source_lang` в проекте.
- Вывод: `linked`, `unlinked`, `with_official_target`, `by_match{}`, `src_lang`, `ambiguous`, `no_src_text`, `unlinked_examples[]`, `next`.
- Реплики без текста нельзя перевести: они остаются с оригинальным голосом и попадают в отчёт. Распознавания речи оригинала для таких строк в v0.1 нет.
- Код 3: есть несвязанные реплики. Scout проверяет выборку и пишет гипотезу в отчёт; канон вручную не правится.

### 3.5. `cast`: персонажи и референсы

```text
babeldub cast [--refs 1..3] [--min-ref-s 3] [--max-ref-s 12]
babeldub cast apply-labels
babeldub cast select --chars A,B | --policy main|all
```

- Персонажи — из метаданных (`speaker_hint`); реплики без них группируются по голосу (эмбеддинги через воркер `qc`).
- Референсы персонажа — самые чистые и типичные оригинальные реплики (`cast/refs.jsonl`, `cast/refs/<char_id>/*.wav`); лист кастинга — `cast/casting_sheet.md`.
- Вывод `cast`: `characters`, `selected`, `main[]`, `uncertain[]`, `no_ref[]`, `clusters`, `refs`, `sheet`, `next`.
- Код 3: есть кластеры голосов или `uncertain[]`. Роль caster размечает их в `cast/labels.agent.jsonl` → `babeldub cast apply-labels` (вывод `updated`, `labeled`, `uncertain[]`; код 2 — невалидный файл, исправить).
- `no_ref[]` — у персонажа нет отдельного референса: его реплики пойдут с промптом-оригиналом (`line`); короткие реплики такого персонажа могут остаться без синтеза.
- После вопроса 2 пользователю: `babeldub cast select ...` (вывод `policy`, `selected[]`).

### 3.6. `translate`: текст для дубляжа

```text
babeldub translate plan
babeldub translate use-official
babeldub glossary apply
babeldub batch make [--size 60] [--ids id1,id2] [--from-decisions]
babeldub batch next
babeldub batch submit <bid>
babeldub glossary check
```

| Команда | Что делает | Вывод |
|---|---|---|
| `translate plan` | решает режим; при `auto` — `official`, если официальный перевод есть у ≥ 95 % связанных озвучиваемых реплик, иначе `translate` | `mode`, `need_agent`, `translated`, `glossary`, `style_bible`, `next` |
| `translate use-official` | копирует официальный перевод в `translate/translations.jsonl` (`by=official`) | `copied`, `missing`, `next` |
| `glossary apply` | проверяет `translate/glossary.agent.jsonl` и сливает его в `translate/glossary.jsonl` | `added`, `updated`, `total` |
| `batch make` | нарезает пакеты по сценам для реплик, которым нужен агент; `--ids` и `--from-decisions` — пакеты на повторный перевод (решения QA `shorten`, `retranslate`) | `mode`, `batches[]`, `lines`, `open`, `dir`, `next` |
| `batch next` | первый незакрытый пакет | `batch`, `state`, `in`, `out`, `remaining`, `next` |
| `batch submit <bid>` | проверяет `.out.jsonl` (все `id`, непустой `dub_text`, плейсхолдеры как в `src`) и сливает в `translations.jsonl`; подбирает влезающий в бюджет вариант | `merged`, `errors[]`, `over_budget[]`, `over_fixed[]`, `picked_variants`, `open_batches`, `next` |
| `glossary check` | сверяет термины глоссария в переводах | `violations[]`, `next` |

Порядок:

1. `translate plan`. Режим `official` → `translate use-official`. Если после этого `need_agent` = 0, переходи к голосу.
2. Роль lexicon: `translate/glossary.agent.jsonl` и `translate/style_bible.md`, затем `babeldub glossary apply`. Это нужно **до** `batch make`: в заголовок пакета попадают только термины из `translate/glossary.jsonl` и текст style bible.
3. `batch make`, затем цикл: `batch next` → translator → `batch submit <bid>`. `batch next` всегда отдаёт первый незакрытый пакет; для параллельной работы бери разные `bid` из `batches[]` вывода `batch make` (или по именам файлов `translate/batches/<bid>.in.jsonl` без готового `.out.jsonl`).
4. `batch submit`: код 2 — ошибки в `errors[]` (полный список в `errors_file`). Переводчик исправляет свой `.out.jsonl` и сдаёт снова, до 3 итераций. Код 3 — `over_budget[]`: строки длиннее 115 % бюджета; для `over_fixed[]` (фиксированная длина) нужны более короткие `variants`, остальное подгонит `post`.
5. Конец цикла: `batch next` возвращает `batch: null`, `done: true`.
6. `glossary check`: код 3 — `violations[]` (файл `translate/glossary_violations.jsonl`). Исправления: `babeldub batch make --ids <id,...>` → переводчик → `batch submit`.

### 3.7. `synth`: синтез голосом героя

```text
babeldub synth [--chars A,B] [--limit N] [--attempt-next] [--engine <name>]
```

- Для каждой выбранной реплики с переводом готовит голосовой промпт по `tts.ref_strategy` (`line` — оригинал этой строки с её исходным текстом; `char` — лучший референс персонажа; `auto` — `line`, если оригинал ≥ `tts.line_ref_min_s` и есть текст, иначе `char`).
- Модель грузится один раз на прогон; реплики идут группами по персонажам. Результаты — `synth/raw/<uid>/a<attempt>.wav` и `synth/results.jsonl`. Занимает `state/locks/gpu.lock`.
- Вывод: `done`, `failed`, `rtf`, `remaining`, `next`; пропуски по причинам (не выбран персонаж, нет перевода, исчерпаны попытки).
- `--attempt-next`: новая попытка для реплик с решением `regen` (новый seed) или `reref` (смена промпта: оригинал → референс персонажа или наоборот).
- Реплики с изменённым переводом (после `shorten`/`retranslate`) подхватывает обычный `synth`.
- Код 3: `failed` > 0 — повтори для них; при повторяющейся ошибке смотри `troubleshooting.md`.
- Код 4: нет venv движка или весов модели → `babeldub-doctor`.
- Долгие прогоны запускай чанками (`--chars`, `--limit`) и сверяйся с `remaining`.

### 3.8. `post`: подгонка под оригинал

```text
babeldub post
```

- Обрезка тишины, подгонка длительности к речи оригинала с сохранением высоты голоса (в пределах `post.max_stretch` = 0,12), восстановление краевой тишины оригинала, громкость под оригинальную реплику (BS.1770 LUFS; RMS для клипов короче 0,4 с), потолок пика `post.true_peak_db` = −1 дБ, частота и каналы как у оригинала.
- Для `fixed_length: true` (катсцены, таймлайны, видео) реплика должна уложиться в исходную длительность с допуском `post.fixed_length_tolerance` (0,03).
- Вывод: `done`, `over_length[]`, `next`.
- Код 3: `over_length[]` — реплики не влезли даже после растяжения. После `qa` они получат `shorten`.

### 3.9. `qa`: проверка качества и сходства голоса

```text
babeldub qa
babeldub decide apply
babeldub report
```

- Для последней попытки каждой реплики: распознавание синтеза → CER к `dub_text`; сходство голоса синтеза с оригинальной репликой героя и с его референсом (`sim`) против базовой линии персонажа (`sim_ref`); отношение длительностей; пики и тишина. Пишет `qa/qa.jsonl`; авто-решения — в `qa/decisions.jsonl`.
- Пороги: `qa.max_cer` = 0,15; `qa.min_sim_ratio` = 0,75 (`sim` ≥ 0,75 × `sim_ref`); `qa.max_duration_ratio` = 1,35.
- Авто-решения: прошло → `accept`; разборчивость, голос, пики → `regen`, пока есть попытки, потом `keep_original`; слишком длинно → `shorten` (до 2 раз), потом `keep_original`; нет файла, нет перевода, сбой воркера → без решения (внимание).
- Вывод `qa`: `checked`, `pass`, `fail`, `regen`, `shorten`, `keep_original`, `accept`, `attention[]`, `pending`, `cer_median`, `sim_median`, `next`.
- Код 3: `attention[]` не пуст. Роль qa пишет `qa/decisions.agent.jsonl` → `babeldub decide apply` (вывод `applied`, `skipped`, `by_decision`; код 2 — невалидный файл, исправить).
- Решения: `accept | regen | reref | shorten | retranslate | keep_original | skip`.
- `report` пишет `qa/report.md` с итогами по персонажам (вывод `file`).

### 3.10. Цикл качества

```text
synth → post → qa ─┬─ accept ─────────────────────────────────────────────→ готово к сборке
                   ├─ regen / reref ──→ synth --attempt-next → post → qa
                   ├─ shorten / retranslate ──→ batch make --from-decisions → batch next → translator
                   │                             → batch submit → synth → post → qa
                   └─ keep_original / skip ─────────────────────────────────→ звучит оригинал
```

- Подсказка `next` у `qa` говорит, какая ветка нужна сейчас. Цикл закончен, когда `next` советует `babeldub report`.
- После `tts.max_attempts` (3) попыток на один и тот же текст реплика получает `keep_original`.
- Если у одного персонажа доля брака в чанке больше 20 %, роль voice останавливает его синтез и разбирается (референсы, стратегия), а не жжёт GPU повторами.

### 3.11. `build`: сборка нативных контейнеров

```text
babeldub build [--codec auto|pcm16|fadpcm]
```

- Для каждого изменённого контейнера адаптер пишет `build/out/<relpath>` и манифест `build/build_manifest.json` (контейнеры, sha1, кодек, модели и их лицензии).
- Вывод: `containers[]`, `replaced`. Предпросмотр — `--dry-run`.
- Код 2 с подсказкой про уровень B/C: адаптер не умеет собирать этот формат. Опиши ручной путь из справочника `babeldub-recon` в отчёте; в папку игры ничего не пиши.
- По умолчанию кодек — `build.codec` проекта (`auto`). Если игра не приняла звук, после отката пересобери с `--codec pcm16`.

### 3.12. `verify`: проверка до установки

```text
babeldub verify [--game-runtime]
```

- Проверка каждого собранного контейнера: структура, число и порядок записей, декодирование.
- Вывод: `ok`, `problems[]`.
- Код 3 или `ok` = false: не устанавливать. Причины — в отчёт; при необходимости пересборка.
- `--game-runtime` загружает библиотеку звука самой игры. Только по явной просьбе пользователя.

### 3.13. `install`, `uninstall`, `reapply`

| Команда | Что делает | Вывод |
|---|---|---|
| `babeldub install` | бэкап оригиналов в `build/backup`, атомарная замена, `build/install.json` | `installed[]`, `files`, `backup`, `rollback` |
| `babeldub uninstall` | возврат оригиналов по бэкапу с проверкой sha1 | `restored[]` |
| `babeldub reapply` | повторная установка после обновления игры (сверка sha1 исходников) | `reapplied[]`, `changed[]` |

- CLI отказывается писать в папку игры, пока игра запущена или найден античит, и если файлы игры — не те оригиналы, из которых шла сборка. Все три команды поддерживают `--dry-run` (план без записи).
- «the game is running» → попросить закрыть игру и лаунчер (в отчёте), не обходить флагами.
- «not the originals this build was made from» или `reapply` с непустым `changed[]`: игра обновилась. Пересобери из новых оригиналов: `build` → `verify` → `install`.

## 4. Служебные команды

| Команда | Зачем |
|---|---|
| `babeldub status` | стадии, счётчики (`counts`), `next_stage`, `next` — первая стадия в состоянии `pending`, `running` или `failed` (стадии в `attention` считаются пройденными); код 3, если есть стадии в `attention` или `failed` (детали по стадиям — в `attention{стадия: …}`). Первый шаг после любого перерыва |
| `babeldub set <section.key>=<value> ...` | изменить настройки `project.json` (вывод `changed{}`), например `babeldub set qa.max_cer=0.2 compute.device=cuda` |
| `babeldub query --where <условия> --fields <поля> --limit N` | выборка реплик (вывод `rows[]`, `total`) |
| `babeldub sample --scene X --n 30 --out file.jsonl` | реплики сцены в файл для агента (вывод `file`, `n`) |
| `babeldub games` | реестр игр и их проектов (вывод `games[]`, `active`) |

`query` — основной способ смотреть данные. Всегда задавай `--fields` и `--limit` (до 50).

- Условия `--where`: `поле=значение`, `поле!=значение`, `поле~подстрока`, через запятую.
- Поля строки: `id`, `kind`, `scene`, `fixed_length`, `flags`, `speaker_hint`, `entry.key`, `audio.dur_s`, `text.src`, `text.tgt_official`; кастинг — `char` (id или имя), `char_name`; перевод — `translation.dub_text`, `translation.fit`; QA — `qa.attempt`, `qa.cer`, `qa.sim`, `qa.sim_ref`, `qa.ratio`, `qa.reasons`, `qa.asr_text`; вычисляемые — `has_text`, `translated`, `verdict`, `decision`, `voiced`.
- Пример: `babeldub query --where char=Zbyszek,verdict=fail --fields id,qa.reasons,qa.cer,qa.sim,qa.sim_ref --limit 30`.

## 5. Артефакты проекта

```text
BABELDUB_HOME/projects/<slug>/
├─ project.json                         настройки и ответы пользователя
├─ state/ progress.json events.jsonl locks/gpu.lock
├─ inventory/ recon.json assets.jsonl lines.jsonl texts.jsonl report.md
├─ audio/orig/<uid>.wav                 декодированные оригиналы
├─ cast/ characters.jsonl assign.jsonl refs.jsonl refs/<char_id>/<uid>.wav casting_sheet.md labels.agent.jsonl
├─ translate/ glossary.jsonl glossary.agent.jsonl style_bible.md batches/<bid>.in.jsonl batches/<bid>.out.jsonl translations.jsonl
├─ synth/ results.jsonl raw/<uid>/a<attempt>.wav
├─ post/ post.jsonl audio/<uid>.wav
├─ qa/ qa.jsonl decisions.jsonl decisions.agent.jsonl report.md
├─ build/ out/<relpath> build_manifest.json backup/<relpath> install.json
└─ logs/
```

| Кто пишет | Файлы |
|---|---|
| только CLI (канон) | `project.json`, `state/*`, `inventory/*`, `cast/characters.jsonl`, `cast/assign.jsonl`, `cast/refs.jsonl`, `cast/casting_sheet.md`, `translate/glossary.jsonl`, `translate/batches/*.in.jsonl`, `translate/translations.jsonl`, `synth/*`, `post/*`, `qa/qa.jsonl`, `qa/decisions.jsonl`, `qa/report.md`, `build/*` |
| роли (агенты) | `cast/labels.agent.jsonl` (caster), `translate/glossary.agent.jsonl` и `translate/style_bible.md` (lexicon), `translate/batches/<bid>.out.jsonl` (translator), `qa/decisions.agent.jsonl` (qa) |

Все JSONL — UTF-8 без BOM, по объекту на строку. Повтор того же `id` — новая ревизия, побеждает последняя.

## 6. Жизненный цикл реплики

```text
inventoried (extract) → linked (link; без текста → звучит оригинал)
→ cast (персонаж не выбран → звучит оригинал)
→ translated (translations.jsonl)
→ synth (attempt k) → post → qa
   ├─ accept → build → install
   ├─ regen / reref → synth (attempt k+1, не больше tts.max_attempts на текст)
   ├─ shorten / retranslate → новый перевод → synth
   └─ keep_original / skip → в сборку не идёт, звучит оригинал
```
