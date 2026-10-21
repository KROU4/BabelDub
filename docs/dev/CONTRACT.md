# Контракт разработчика BabelDub (v0.1)

> **LANGUAGE POLICY (binding, from the project owner).** The shipped product is English-first, following the world standard for software: every instruction meant for an AI model (SKILL.md files and their references, agent roles, commands, AGENTS.md, GEMINI.md, hook messages, CLI hints and generated reports) and all repository documentation (README.md, docs/, CONTRIBUTING, SECURITY, CHANGELOG, templates) MUST be written in English. A Russian translation of the README is shipped as README.ru.md. Skill descriptions may list example user phrases in several languages (including Russian) so the skill triggers for non-English users; the agent always talks to the user in the user's language. This contract itself will be translated to English in the English pass.

Этот документ — единственный источник правды для кода в `runtime/` и для скиллов, которые этот код вызывают. Если код и документ расходятся, исправляется то, что неправо, и обновляется документ. Сверено с реализацией v0.1 (все команды, флаги и поля ниже есть в `runtime/src/babeldub`); сквозной прогон всех стадий — `runtime/tests/e2e/test_pipeline_synthetic.py`.

## 1. Принципы

1. **Скиллы задают процедуру, CLI делает массовую работу, агенты выносят суждения.** Всё детерминированное, массовое или опасное (разбор бинарных форматов, декодирование, синтез, метрики, сборка, установка) — команды `babeldub`. Перевод, разметка персонажей, разбор брака — агенты.
2. **Один писатель канона.** Канонические манифесты (`inventory/*.jsonl`, `translate/translations.jsonl`, `translate/glossary.jsonl`, `cast/characters.jsonl`, `qa/decisions.jsonl` …) пишет только CLI. Агенты пишут `*.agent.jsonl` / `*.out.jsonl`, CLI проверяет их и сливает (`submit`, `apply`) по принципу «всё или ничего».
3. **Папка игры только для чтения** до `babeldub install`. Установка — с бэкапом и sha1, откат — `babeldub uninstall`. Исполняемые файлы игры CLI никогда не запускает (проверка рантаймом FMOD в `verify --game-runtime` грузит только аудиобиблиотеку игры в дочернем процессе).
4. **Тяжёлое — вне репозитория и вне CLI.** CLI зависит только от `numpy`, `soundfile`, `PyYAML`, `UnityPy`. Всё, что требует `torch`, работает в отдельных venv (`BABELDUB_HOME/envs/<engine>`) как подпроцесс-воркер с JSONL-протоколом (§6).
5. **Один JSON на выходе.** Каждая команда печатает в stdout ровно один компактный JSON-объект (цель — до 2 КБ) и возвращает код выхода (§2). Подробности — в файлах проекта, сводка указывает на них. Даже если модуль команды не импортируется, `cli.main` печатает JSON с ошибкой, а не трейсбек.
6. **GPU в приоритете (требование заказчика).** Если на машине есть NVIDIA GPU, ВСЕ нейросетевые вычисления (TTS, ASR, эмбеддинги голоса, кластеризация на эмбеддингах, сепарация) идут на GPU. Настройка проекта `compute.device`: `auto` (по умолчанию: GPU, если есть) | `cuda` | `cpu`. Устройство для воркера определяет `core.compute.resolve_device(project.compute.device)` и передаёт его в `{"op":"load","device":...}` (`synth` берёт `--device`, иначе `tts.device`, иначе `compute.device`; `cast`/`qa` — `--device`, иначе `compute.device`); воркер в ответе на `load` сообщает фактическое устройство (`"device": "cuda:0"`), а команда, если GPU есть, но воркер оказался на CPU, добавляет в сводку предупреждение `core.compute.device_warning(...)`. `setup` ставит CUDA-колёса torch, когда `core.compute.detect_gpu()` видит GPU, и CPU-колёса только без GPU. Тяжёлые numpy-циклы (например, кодирование FADPCM) — векторизованы; лёгкая DSP-обработка (ресемплинг, громкость) остаётся на CPU.
7. **Идемпотентность.** Повторный запуск стадии не ломает результат и не повторяет сделанную работу без `--force`.
8. **Дубляж голосом оригинального героя.** Голосовой промпт по умолчанию — сама оригинальная реплика с её исходным текстом (`tts.ref_strategy`, §4.1); QA сравнивает голос синтеза с оригиналом персонажа.

## 2. Коды выхода

| Код | Константа | Значение |
|---|---|---|
| 0 | `EXIT_OK` | стадия завершена, внимания не нужно |
| 1 | `EXIT_ERROR` | неожиданная ошибка (`error`, `where` в JSON) |
| 2 | `EXIT_USAGE` | неверные аргументы, не пройдена предыдущая стадия, невалидный файл агента |
| 3 | `EXIT_ATTENTION` | стадия завершена, но есть элементы, требующие решения (агента или человека) |
| 4 | `EXIT_ENV` | не хватает окружения: `babeldub doctor` → `babeldub setup` |

Ошибки бросаются как `BabelDubError(message, code=..., hint=..., **extra)` — `cli.main` превращает их в JSON. Поле `ok` в JSON: `true` для кодов 0 и 3.

Статусы стадий в `state/progress.json` (`project.mark_stage`): `running | done | attention | failed`. Стадия в `attention` считается пройденной (конвейер идёт дальше), её элементы ждут решения. `uninstall` записывает стадию `install` со статусом `attention` и `state: "uninstalled"`.

## 3. Расположение данных

```text
BABELDUB_HOME (Windows: %LOCALAPPDATA%\BabelDub; Linux: ~/.local/share/babeldub; env BABELDUB_HOME)
├── active.json        активный проект {"project": "<dir>"}
├── games.json         реестр папок игр (для хука-охранника)
├── envs/<engine>/     venv движков: qwen3tts, chatterbox, qc (маркер babeldub-env.json)
├── models/<name>/     веса (hf download --local-dir; маркер .babeldub-model.json)
├── hf-cache/          HF_HOME для воркеров (speechbrain/<model> — рабочая копия ECAPA)
├── tools/<tool>/      внешние утилиты (vgmstream/INSTALLED.json и т.п.)
├── cache/downloads/   загрузки setup
├── logs/              doctor.json, setup-plan.json, setup-<name>.log
└── projects/<slug>/   рабочие папки проектов
```

Папка проекта (`core/layout.py` — константы путей):

```text
projects/<slug>/
├─ project.json                         настройки и ответы пользователя
├─ state/ progress.json events.jsonl locks/gpu.lock
├─ inventory/ recon.json assets.jsonl lines.jsonl texts.jsonl report.md
├─ audio/orig/<uid>.wav                 декодированные оригиналы
├─ cast/ characters.jsonl assign.jsonl refs.jsonl refs/<char_id>/<uid>.wav casting_sheet.md labels.agent.jsonl
│        embeddings.npz                 кеш ECAPA-эмбеддингов оригиналов (по size+mtime wav)
├─ translate/ glossary.jsonl glossary.agent.jsonl style_bible.md translations.jsonl
│             batches/index.json batches/<bid>.in.jsonl batches/<bid>.out.jsonl batches/<bid>.errors.json
│             missing_official.txt glossary_violations.jsonl
├─ synth/ results.jsonl raw/<uid>/a<attempt>.wav refcache/<sha>.wav (обрезанные длинные референсы)
├─ post/ post.jsonl audio/<uid>.wav
├─ qa/ qa.jsonl decisions.jsonl decisions.agent.jsonl baseline.json report.md
├─ build/ out/<relpath> build_manifest.json backup/<relpath> install.json (staging/ — временно во время build)
└─ logs/ synth-<stamp>.log qc_worker.log samples/<label>.jsonl
```

## 4. Манифесты

Все JSONL — UTF-8 без BOM, по объекту на строку; первая строка канонических файлов — заголовок `{"_schema": "...", "_v": 1, "_created": "..."}` (читатели его пропускают, см. `core/jsonl.py`). Повтор того же `id` — новая ревизия, побеждает последняя. Поля сверх перечисленных допустимы; читатели их игнорируют.

### 4.1 `project.json`
```json
{
  "_schema": "babeldub.project/1", "cli_version": "0.1.0", "created": "...",
  "slug": "low-budget-repairs", "game_name": "Low-Budget Repairs", "game_dir": "G:/Low-Budget Repairs",
  "target_lang": "ru", "source_lang": null,
  "compute": {"device": "auto"},
  "cast": {"policy": "main", "list": [], "main_share": 0.7},
  "translation": {"mode": "auto"},
  "tts": {"engine": "qwen3tts", "model": null, "tier": "auto", "ref_strategy": "auto", "line_ref_min_s": 1.6, "best_of": 1, "max_attempts": 3},
  "post": {"max_stretch": 0.12, "fixed_length_tolerance": 0.03, "true_peak_db": -1.0},
  "qa": {"max_cer": 0.15, "min_sim_ratio": 0.75, "max_duration_ratio": 1.35},
  "build": {"codec": "auto"},
  "install": {"backup": true}
}
```
- `cast.policy`: `all` | `main` (минимальный набор персонажей, дающий `main_share` всей речи) | `list` (имена/ID в `cast.list`).
- `tts.ref_strategy` — **главный принцип BabelDub: дубляж голосом оригинального героя.** `line` — голосовым промптом служит сама оригинальная реплика с её исходным текстом: модель переносит тембр *и* эмоцию, темп, подачу именно этой фразы (крик остаётся криком). `char` — лучший чистый референс персонажа (для коротких реплик, междометий, реплик без транскрипта или с музыкой). `auto` — `line`, если у оригинала есть исходный текст, `line_ref_min_s ≤ dur ≤ 15 с` и нет флагов `has_bed`/`nonverbal`, иначе `char`; если референса персонажа нет, крайний вариант — короткий оригинал. Референс длиннее 15 с режется по самой тихой точке и подаётся в режиме `x_vector_only` (только тембр). QA сравнивает голос синтеза с оригиналом персонажа (speaker similarity) и отправляет «уплывшие» голоса на перегенерацию.
- `translation.mode`: `auto` (official, если официальный перевод есть у ≥ 95 % связанных озвучиваемых реплик; иначе translate; `adapt` только явно) | `translate` | `adapt` (официальный текст есть, агент адаптирует его под длительность) | `official` (озвучивать официальный текст как есть). Решённый режим `translate plan` пишет в `translation.resolved_mode`.
- Необязательные ключи, которые читает CLI: `target_locale` (`pt-BR` и т.п., пишет `init`), `tts.device` (`cuda|cpu`), `tts.dtype` (`bf16`), `tts.batch_size` (реплик на один вызов генерации, по умолчанию 4), `tts.params` (kwargs генерации движка), `tts.options` (опции загрузки модели, например `attn_implementation`), `tts.yo` (`keep|strip|restore` для `prepare_for_tts`), `post.tolerance` (0,05 — без растяжения, если дубль длиннее не больше чем на 5 %), `qa.silence_db` (−50), `qa.asr_model`, `qa.sv_model` (папки моделей qc).
- `tts.best_of` в v0.1 не используется (`synth` пишет об этом в `notes`): слабые реплики переозвучивает цикл QA (`regen`/`reref` → `synth --attempt-next`).
- `set` проверяет перечисления: `compute.device`, `cast.policy`, `translation.mode`, `tts.ref_strategy`, `tts.tier`, `tts.engine` (`qwen3tts|chatterbox|dummy`); защищены `_schema`, `cli_version`, `created`, `slug`, `game_dir`, `target_lang` (другой язык — новый проект).

### 4.2 `inventory/assets.jsonl` — контейнеры (`adapters.base.Asset`)
`asset_id`, `adapter`, `relpath`, `role` (`voice|text|audio|other`), `size`, `sha1`, `locale`, `level` (`A|B|C`), `info{}`.
- `scan` дописывает `info.mtime_ns` (sha1 переиспользуется, если не изменились size и mtime) и `info.sha1_of: "listing"` для ассета-папки (sha1 по списку файлов).
- FMOD: `info.kind`, `fsbs[{sub, codec, n, dur_s}]`, `audio_table`, `programmer_keys`, `localized_group`, `strings_banks` (relpath'ы `*.strings.bank`, адаптер берёт из них пути событий — при пересоздании `Asset` из строки поле сохраняется), `encrypted`, `fmt_version`, `bank_guid`.
- Unity Localization: `info.kind` (`string-tables|shared-table-data|mixed`), `tables`, `typetree`, `unity_version`, `addressables_crc`, `warning`.

### 4.3 `inventory/lines.jsonl` — КАНОН реплик
```json
{
  "id": "fmod:LowBudgetRepairs_Data/StreamingAssets/Dialogue_en-US.bank#fsb1:0042",
  "uid": "1f0c3a9e7b21",
  "adapter": "fmod",
  "asset_id": "fmod:LowBudgetRepairs_Data/StreamingAssets/Dialogue_en-US.bank",
  "entry": {"key": "dlg_a1_m01_d03_Zbyszek_01", "sub": "fsb1", "index": 42, "event_path": "event:/Dialogue/Act1/Zbyszek/dlg_..."},
  "kind": "dialogue",
  "locale": "en-US",
  "audio": {"sr": 48000, "ch": 1, "dur_s": 2.35, "codec": "vorbis", "wav": "audio/orig/1f0c3a9e7b21.wav"},
  "speaker_hint": "Zbyszek",
  "scene": "a1_m01",
  "order": 42,
  "fixed_length": false,
  "text": {"key": "dlg_...", "src": "Okay, let's see.", "src_lang": "en", "tgt_official": "Так, посмотрим.", "source": "unity-localization", "match": "exact", "table": "Dialogues_Act_1"},
  "flags": []
}
```
- `id` строит `core.ids.line_id(adapter, relpath, sub, index)`; `uid = core.ids.uid(id)`.
- `kind`: `dialogue | cutscene | bark | sfx | music | ambience | ui | unknown`. Озвучиваются только `dialogue`, `cutscene`, `bark`.
- `fixed_length: true` — реплика должна уложиться в исходную длительность (катсцены, таймлайны, видео).
- `audio.wav` появляется после `extract` (путь относительно проекта); `text` — после `link`.
- `entry` у FMOD дополнительно: `subsong` (номер vgmstream), `table_key` (ключ аудиотаблицы для `getSoundInfo`), `audio_table`, `event_src` (`strings|programmer`), `fsb_codec`; `audio.samples`. У `loose`: `entry.file` (путь файла в игре).
- `text`: `src` — реально произнесённый текст без разметки субтитров (метка говорящего `{ColorStart}{CharacterName}:{ColorEnd}`, теги `<i>`, `<color>`, теги Ren'Py); исходная строка — в `src_raw`/`tgt_raw`, если отличается. `match`: `exact | normalized | filename | null`. Источник `asr|agent|human|manual` повторный `link` не перезаписывает.
- `flags`: `duplicate`, `alt_take` (ставит `link` для нескольких записей одного ключа: основной считается запись с `table_key`, затем с точным ключом, затем `_best`, затем с наибольшим `_vN`), `nonverbal`, `has_bed` (речь смешана с музыкой/шумом), `no_text` (нет текста), `no_audio` (не декодировалось). Повторный `extract` сохраняет `text` и флаги стадий.

### 4.4 `inventory/texts.jsonl`
`{key, table, locale, lang, text, source}` — все строки всех языков из текстовых ассетов (`lang` — двухбуквенный код). Необязательно: `entry_id`, `key_missing` (ключ не найден в общей таблице), `audio`/`file`/`voice` (имя аудиофайла — `link` использует его на проходе по имени файла).

### 4.5 `cast/*`
- `characters.jsonl`: `{char_id, name, name_tgt, gender (m|f|u), age (child|adult|old|u), n_lines, speech_s, share, tier (main|secondary|minor), selected, source (metadata|folder|cluster|unknown), conf, labeled, notes}`. `char_id` — Unicode-slug имени (`cast.characters.char_id_for`), кластеры — `cluster_NN`, реплики без говорящего без воркера qc — `unknown`.
- `assign.jsonl`: `{id, char_id, conf}` (`conf` кластера — косинус к центроиду).
- `refs.jsonl`: `{ref_id, char_id, line_id, wav, text, lang, dur_s, score, chosen, centroid_cos?, active_rms_db?}`. У группы `unknown` референсов нет: её реплики озвучиваются стратегией `line`.
- `labels.agent.jsonl` (пишет агент-кастер): `{char_id, name, name_tgt, gender, age, notes}`; лишние поля и неизвестный `char_id` — отказ всего файла (код 2).
- `embeddings.npz` — кеш эмбеддингов по `line id`.

### 4.6 `translate/*`
- `batches/<bid>.in.jsonl` (CLI): первая строка `{"_batch": bid, "scene", "scenes", "target_lang", "source_lang", "mode", "n", "glossary": [...], "style": "...", "out": "<bid>.out.jsonl", "rules"}`, далее `{id, key, speaker, src, tgt_official, dur_s, budget_syll, fixed_length, placeholders, context_prev, context_next, task?, current?}` (`task`: `adapt | retranslate | shorten`, `current` — текущий `dub_text`).
- `batches/<bid>.out.jsonl` (агент): `{id, dub_text, sub_text?, variants?: [короче...], notes?}`. Плейсхолдеры и теги исходника обязаны быть в `dub_text`, `sub_text` и каждом варианте (`check_placeholders`; ремарки вида `[snoring]` и переносы — мягкие).
- `batches/index.json` — состояние пакетов (`open | error | merged`, ids, sha1 слитого `.out`); `<bid>.errors.json` — ошибки последней отправки.
- `translations.jsonl` (CLI): `{id, dub_text, sub_text, lang, by (official|agent|human), rev, ts, est_syll, budget_syll, fit (ok|over|under|null), batch?, variants?, picked_variant?, agent_text?, notes?}`. `fit = null`, если длительность неизвестна; при `over` CLI сам берёт первый укладывающийся вариант из `variants` (исходный текст агента — в `agent_text`). `dub_text` хранит строку с тегами как в `src`; перед синтезом её чистит `prepare_for_tts`.
- `glossary.jsonl` (CLI, канон): `{term, translation, pronounce?, note?}`; агент пишет `glossary.agent.jsonl`, CLI сливает (`glossary apply`).

### 4.7 `synth/results.jsonl`
`{id, attempt, engine, model, wav, sr, dur_s, seed, ref_id, text, status (ok|error), error?, rtf?, ts}` плюс `ref_mode (line|char)`, `ref_wav`, `ref_trimmed`, `lang`, `char_id`, `reason (new|retry|retranslated|regen|reref|force)`, `key` (хеш входов: движок, модель, язык, текст, референс), `batch?` (размер батча; сид у батча общий), `hit_token_limit?`, `vram_peak_gb?`. У ошибочных строк `status=error`, `wav=null`.

### 4.8 `post/post.jsonl`
`{id, attempt, wav, dur_s, orig_dur_s, target_s, ratio, ratio_raw, stretch, stretch_method, lufs_in, lufs_out, level_ref, measure (lufs|rms), peak_db, lead_s, trail_s, sr, ch, fixed_length, over_length, src_wav, src_key, ts}`.
- `dur_s` — длительность всего файла; `target_s` — речевой отрезок оригинала без краевой тишины; `ratio` = речевой отрезок дубля / `target_s` после подгонки, `ratio_raw` — до неё.
- Растяжение — если `ratio_raw > 1 + post.tolerance` (для `fixed_length` — `1 + fixed_length_tolerance`), не сильнее `post.max_stretch`. `over_length` — если после подгонки `ratio > 1 + fixed_length_tolerance` (fixed) или `> qa.max_duration_ratio`.
- Громкость по LUFS оригинала (по RMS, если речь короче 0,4 с), true peak ≤ `post.true_peak_db`, частота и каналы — как у оригинала.

### 4.9 `qa/*`
- `qa.jsonl`: `{id, attempt, char_id, wav, asr_text, cer, sim, sim_line, sim_char, sim_ref, ratio, dur_s, peak_db, verdict (pass|fail), reasons[], ts}`; `reasons ∈ missing_file | qc_error | no_dub_text | empty | silent | cer | sim | duration | peak`. `sim` — среднее из сходства с оригиналом этой реплики и с выбранным референсом персонажа; `sim_ref` — базовая линия персонажа (средний попарный косинус его оригиналов, `qa/baseline.json`), проверка `sim ≥ qa.min_sim_ratio · sim_ref`.
- `decisions.jsonl` (CLI): `{id, decision, attempt, by (auto|agent|human), note, attention, ts}`; `decision ∈ accept | regen | reref | shorten | retranslate | keep_original | skip`. Авто-решения: pass → `accept`; `cer|sim|peak|empty|silent` → `regen`, пока `attempt < tts.max_attempts`, затем `keep_original` + attention; `duration` → `shorten` + attention (до 2 раз, затем `keep_original`); `missing_file|no_dub_text|qc_error` → без решения, attention. Решения agent/human для текущей попытки авто-QA не перезаписывает.
- `decisions.agent.jsonl` (агент): `{id, decision, attempt?, note?}` (без `by`/`ts`); `attempt` по умолчанию — последняя попытка из `post`. `accept` относится к последней обработанной попытке: `post` хранит только её, решение для старой попытки сборка отбрасывает как `stale_attempt`.
- `synth --attempt-next` делает новую попытку для решений `regen`/`reref`, у которых `attempt` ≥ последней успешной попытки; `reref` меняет голосовой промпт (`line` → `char`, `char` → другой референс или `line`).

### 4.10 `build/build_manifest.json`, `build/install.json`
- manifest: `{_schema: "babeldub.build/1", created, build_id, game_fingerprint, target_lang, options{codec, sample_rate?}, containers: [...], models: [{name, revision, license, engine, lines}]}`.
  - `containers[]`: `{asset_id, adapter, relpath, src_sha1, out, out_sha1, replaced, kept, codec, lines, inputs_sha1, built, files: [{relpath, src_sha1 (null = новый файл), out, out_sha1}], replacements: {line_id: {wav, sha1}}}`. `relpath = ""` — ассет в корне игры. `inputs_sha1` (исходник + sha1 wav + опции) делает сборку идемпотентной (`status: up_to_date`).
  - `build_id` — sha1 содержимого; `verify` записывает его в `progress.verify.build_id`, `install`/`reapply` требуют совпадения.
- install: `{installed: [{relpath, asset_id, orig_sha1, new_sha1, backup, created}], build_id, ts, state (installing|installed|uninstalled), restored?}`. `installing` — журнал до записи (сбой откатывается); `created: true` — новый файл, `uninstall` его удаляет.

## 5. Команды CLI

Общие флаги: `--project <dir|slug>` (иначе `BABELDUB_PROJECT`, затем активный проект, затем текущая папка), `--pretty`. У подкоманд (`cast apply-labels`, `cast select`, `decide apply`) `--project`/`--pretty` можно ставить и до, и после действия.

| Модуль | Команда | Что делает | Вывод (ключевые поля) |
|---|---|---|---|
| `commands/env.py` | `doctor [--no-import-check]` | ОС, Python, uv, ffmpeg/ffprobe, vgmstream-cli, rubberband, git, GPU (nvidia-smi: имя, VRAM, драйвер), свободное место, какие envs (с проверкой импорта) и модели есть; рекомендует тир; полный отчёт — `BABELDUB_HOME/logs/doctor.json`. Код 4 — нет uv/ffmpeg/ffprobe, 3 — нужны envs/модели | `tier`, `gpu`, `tools{}`, `envs{}`, `models[]`, `missing[]`, `optional_missing[]`, `to_install[]`, `install{}`, `next` |
| | `setup [--engines qwen3tts,qc] [--models auto\|none\|a,b] [--tools] [--tier] [--lang] [--accept-license M] [--force] [--dry-run] [--no-import-check]` | Без флагов: TTS-движок проекта + qc, модели `auto` для тира и языка, vgmstream. Создаёт venv (`uv venv`, torch cu128/cu124/cpu), ставит пакеты по каталогу, скачивает веса (`hf download`) и инструменты (vgmstream с GitHub-релиза, sha256); существующий рабочий venv «усыновляет»; модели `opt_in` — только с `--accept-license`. План — `logs/setup-plan.json` | `planned[]`, `done[]`, `skipped[]`, `failed[]`, `blocked[]`, `download_gb`, `free_gb` |
| | `models list [--engine E]` / `models fetch a,b [--accept-license M] [--force] [--dry-run]` | Каталог `data/models.yaml` с лицензиями и статусом; загрузка весов | `models[]` / `done[]`, `skipped[]` |
| `commands/project.py` | `init <game_dir> --lang ru [--cast all\|main\|list] [--chars A,B] [--mode auto\|translate\|adapt\|official] [--slug] [--name] [--project-dir]` | Создаёт проект (движок TTS по языку), регистрирует игру в `games.json`, делает его активным; повтор идемпотентен; другая игра/язык в том же slug — код 2; язык без движка — код 3 | `project`, `slug`, `engine`, `next` |
| | `status` | Стадии, счётчики, следующий шаг: `next_stage` — первая стадия в `pending`, `running` или `failed`; стадии в `attention`/`failed` — в `attention{стадия: сводка}` и код 3 | `stages{}`, `counts{}`, `next_stage`, `next`, `attention{}`, `done` |
| | `set <section.key>=<value> ...` | Меняет настройки `project.json`; тип — по текущему значению, проверка перечислений (§4.1) | `changed{}`, `warnings[]` |
| | `query [--where k=v,k!=v,k~sub ...] [--fields a,b.c] [--limit 20] [--out file]` | Выборка реплик (сливает lines + assign + characters + translations + qa + decisions). Вычисляемые поля: `char` (id или имя), `has_text`, `translated`, `verdict`, `decision`, `voiced`; метрики QA — `qa.*` | `rows[]`, `total`, `file?` |
| | `sample --scene X \| --char X \| --ids a,b [--n 30] [--out file.jsonl]` | Контекст для агента: реплики в файл (по умолчанию `logs/samples/<label>.jsonl`) | `file`, `n` |
| | `games` | Реестр игр | `games[]`, `active` |
| `commands/recon.py` | `scan [--force]` | Определяет движок и middleware (`data/engines.yaml`), вызывает `discover()` всех адаптеров (сбой адаптера — предупреждение), пишет `recon.json` (с `refs.recon`/`refs.pack` — пути справочников скиллов), `assets.jsonl`, `report.md` | `engine`, `middleware[]`, `text_systems[]`, `voice_assets[]`, `text_assets[]`, `counts{}`, `level`, `warnings[]`, `anticheat?` |
| | `extract [--kinds dialogue,cutscene,bark\|all] [--limit N] [--ids a,b] [--locales auto\|all\|en-US,...] [--force] [--dry-run]` | `list_lines()` + `extract_audio()` → `lines.jsonl`, `audio/orig/*.wav`; `read_texts()` → `texts.jsonl`. `lines.jsonl` = только выбранные реплики (запуск с `--limit N` — пробный проект, без лимита — расширяет его); `auto` выбирает одну голосовую локаль (source_lang, затем en, затем самую большую); готовые WAV переиспользуются | `lines`, `by_kind{}`, `minutes`, `decoded`, `reused`, `failed`, `texts`, `locales[]`, `voice_locales[]` |
| | `link [--dry-run]` | Связывает реплики с текстами (ключ → нормализованный ключ → имя файла), заполняет `text`, `source_lang`, флаги `no_text`/`alt_take`/`duplicate`; код 3 при `unlinked > 0` | `linked`, `unlinked`, `with_official_target`, `by_match{}`, `src_lang`, `alt_takes` |
| `commands/cast.py` | `cast [--refs 1..3] [--min-ref-s 3] [--max-ref-s 12] [--threshold 0.35] [--device] [--wait-lock S] [--no-embed] [--force]` | Персонажи из `speaker_hint` (метаданные), остальные — кластеры по эмбеддингам воркера `qc` (если он есть; иначе группа `unknown` с предупреждением), тиры, `cast.policy`, выбор референсов, `casting_sheet.md`. Код 3 — есть неопознанные голоса или выбранный персонаж без референса | `characters`, `selected`, `main[]`, `uncertain[]`, `no_ref[]`, `clusters`, `refs` |
| | `cast apply-labels` | Проверяет и сливает `labels.agent.jsonl` (всё или ничего) | `updated`, `labeled`, `uncertain[]` |
| | `cast select --chars A,B \| --policy all\|main [--device] [--wait-lock] [--no-embed]` | Меняет выбор персонажей, подбирает референсы новым | `policy`, `selected[]`, `count`, `new_refs`, `no_ref[]` |
| `commands/translate.py` | `translate plan` | Решает режим (`official/adapt/translate`), считает покрытие | `mode`, `with_official`, `official_share`, `need_agent`, `no_text`, `next` |
| | `translate use-official [--force]` | Копирует официальный перевод в `translations.jsonl` (`by=official`); agent/human без `--force` не трогает; реплики без официального текста — в `missing_official.txt`, код 3 | `copied`, `unchanged`, `missing`, `over_budget` |
| | `batch make [--size 60] [--ids a,b] [--from-decisions] [--force]` | Нарезает пакеты по сценам для реплик без перевода (или для `adapt`); `--ids` — перевести заново, `--from-decisions` — реплики с решением `retranslate`/`shorten`; `--force` — пересоздать незакрытые | `batches[]`, `lines`, `open` |
| | `batch next` | Первый незакрытый пакет | `batch`, `state (new\|ready\|error)`, `in`, `out`, `errors_file?`; `batch: null, done: true` |
| | `batch submit <bid> [--force]` | Проверяет `.out.jsonl` (все id, лишние id, пустой `dub_text`, типы, плейсхолдеры/теги) и сливает в `translations.jsonl`; ошибки — код 2 и `<bid>.errors.json`; превышение бюджета слогов — код 3 | `merged`, `errors[]`, `over_budget[]`, `over_fixed[]`, `picked_variants` |
| | `glossary check` | Термин глоссария в исходнике без согласованного перевода в `dub_text` → нарушение (код 3, `glossary_violations.jsonl`) | `terms`, `checked`, `violations[]`, `total` |
| | `glossary apply` | Проверяет и сливает `glossary.agent.jsonl` в `glossary.jsonl` | `added`, `updated`, `total` |
| `commands/voice.py` | `synth [--chars A,B] [--ids a,b] [--limit N] [--attempt-next] [--engine qwen3tts\|chatterbox\|dummy] [--device cuda\|cpu] [--wait-lock S] [--batch N] [--force] [--dry-run]` | Готовит голосовые промпты (§4.1), запускает воркер движка в его venv (одна загрузка модели на запуск, батчи по персонажам), пишет `synth/raw`, `results.jsonl` после каждой реплики; занимает `gpu.lock`. Идемпотентно: тот же текст тем же движком/моделью не повторяется | `done`, `failed`, `remaining`, `rtf`, `batch`, `vram_peak_gb`, `by_ref{}`, `skipped{}`, `attention[]` |
| | `post [--ids] [--limit] [--force] [--dry-run] [--stretch-method rubberband\|ffmpeg-rubberband\|atempo]` | Обрезка тишины, подгонка длительности (в пределах `max_stretch`), края тишины и громкость как у оригинала, формат как у оригинала (§4.8) | `done`, `up_to_date`, `over_length[]`, `stretch{}` |
| `commands/qa.py` | `qa [--ids] [--limit] [--force] [--rebaseline] [--device] [--wait-lock S]` | Воркер `qc`: ASR (CER к `dub_text`), сходство голоса с оригиналом и референсом персонажа относительно его базовой линии, длительность, пики → `qa.jsonl`; авто-решения → `decisions.jsonl` (§4.9). Без env `qc` — код 4 | `checked`, `pass`, `fail`, `accept`, `regen`, `shorten`, `keep_original`, `attention[]`, `pending`, `cer_median`, `sim_median`, `device` |
| | `decide apply [--by agent\|human]` | Проверяет и сливает `decisions.agent.jsonl` (всё или ничего; повторы пропускаются) | `applied`, `skipped`, `by_decision{}` |
| | `report` | `qa/report.md` с итогами по персонажам и списком «требуют решения» | `file`, `lines`, `checked`, `pass`, `attention` |
| `commands/pack.py` | `build [--codec auto\|pcm16\|fadpcm] [--only ASSET_ID,...] [--sample-rate N] [--force] [--dry-run]` | Строки с последним решением `accept` группируются по контейнерам; `adapter.build()` в staging, после успеха — в `build/out/<relpath>`; манифест (§4.10). Неизменённые входы — `up_to_date`; сбой одного контейнера не трогает остальные | `containers[{asset_id, status, replaced, kept, codec}]`, `replaced`, `total_containers`, `build_id`, `failed?`, `attention?` |
| | `verify [--game-runtime]` | Выход существует и совпадает с `out_sha1`; затем `adapter.verify()` для каждого контейнера (§5.1) | `ok`, `verified`, `checks`, `problems[]`, `build_id` |
| | `install [--force] [--dry-run]` | Требует `verify` текущей сборки. Отказ, если игра запущена или найден античит (`--force` обходит только эти две защиты) или файл игры не совпадает с исходником сборки. Бэкап оригиналов в `build/backup`, атомарная замена, журнал `install.json` | `installed[]`, `unchanged`, `files`, `backup`, `backed_up` |
| | `uninstall [--force] [--dry-run]` | Возврат оригиналов по бэкапу с проверкой sha1; файл, изменённый и дубляжом, и игрой, не трогается без `--force` | `restored[]`, `already_original`, `state`, `left_changed?` |
| | `reapply [--force] [--dry-run]` | Повторная установка после обновления игры: оригинальные файлы ставятся заново, изменённые игрой контейнеры — в `changed[]` с подсказкой `build --only` | `reapplied[]`, `current`, `changed[]` |

### 5.1 Адаптеры и стадии (`adapters/base.py`)

- `list_lines(game_dir, asset)` — строки §4.3 без декодирования. `extract_audio(...)` → `[{id, wav, sr, ch, dur_s}]`, где `wav` — абсолютный путь или путь относительно проекта/`out_dir`; можно вернуть `{id, error}` для отдельной реплики; `BabelDubError(EXIT_ENV)` делает всю команду кодом 4.
- `build(game_dir, asset, replacements{line_id: wav}, out_path, options)`: `out_path = <staging>/<asset.relpath>`; `options = {codec: auto|pcm16|fadpcm, sample_rate?}`. Возврат `{out, replaced, kept, codec, files?}`: без `files` файл сопоставляется с `asset.relpath`, папка — с `asset.relpath/<подпуть>`; дополнительные файлы пишутся в `<staging>/<их relpath>` и перечисляются в `files`. Запись внутрь папки игры — `EXIT_USAGE`. Формат, который v0.1 не пересобирает, — `BabelDubError(EXIT_USAGE, hint=...)` со ссылкой на `skills/babeldub-pack/references/formats/*.md`.
- `verify(game_dir, asset, built, options)`: `options = {game_runtime, codec, files: [{relpath, path}], replaced_ids: [...], replacements: {line_id: wav}, sample_rate?}` — `replacements` только для wav, чьи sha1 не изменились после сборки. Возврат `{ok, checks[], problems[], details?}`.
- Адаптеры v0.1: `fmod` (A: банки `.bank` и `.fsb`, пересборка PCM16/FADPCM с сохранением числа, порядка и имён сабсаундов), `loose` (A: отдельные аудиофайлы, перекодирование в формат оригинала), `unity-localization` (только чтение текстов), `wwise` (B: чтение `.bnk/.pck/.wem`, сборка — вручную по справочнику).

## 6. Протокол воркеров (JSONL по stdin/stdout)

Воркеры — самодостаточные скрипты `runtime/src/babeldub/workers/*.py`. Они **не импортируют** `babeldub` (в venv движка его нет) и запускаются как `<env python> -u <worker.py>`. Логи — только в stderr (fd 1 воркер перенаправляет в stderr, протокол идёт по дублю исходного stdout, чтобы печать нативных библиотек его не портила). Каждая строка stdin — команда, на каждую — ровно одна строка ответа в stdout.

```text
→ {"op": "load", "model": "<path or repo>", "device": "cuda", "dtype": "bf16", "options": {...}}
← {"ok": true, "op": "load", "sr": 24000, "device": "cuda:0", "info": {"batch": true, ...}}
→ {"op": "synth", "id": "...", "text": "...", "lang": "ru", "ref_wav": "...", "ref_text": "...", "ref_lang": "en", "out": "...wav", "seed": 1234, "params": {"x_vector_only": false, "max_new_tokens": 400}}
← {"ok": true, "op": "synth", "id": "...", "wav": "...", "sr": 24000, "dur_s": 2.1, "rtf": 0.4, "gen_s": 0.8, "mode": "icl", "hit_token_limit": false, "vram_peak_gb": 4.3}
← {"ok": false, "op": "synth", "id": "...", "error": "CUDA out of memory"}
→ {"op": "synth_batch", "seed": 1234, "items": [<synth>, ...]}   ← {"ok": true, "results": [<ответ synth>, ...]}
→ {"op": "stats"} / {"op": "ping"} / {"op": "quit"}
```

Движки v0.1: `qwen3tts` (`tts_qwen3.py`, ICL-промпт ref_audio + ref_text), `chatterbox` (`tts_chatterbox.py`), `dummy` (`tts_dummy.py` — ТЕСТОВЫЙ движок без нейросети для CI и сквозных тестов, интерпретатор CLI, поддерживает `synth_batch`).

QC-воркер (`workers/qc_worker.py`, env `qc`):
```text
→ {"op": "load", "asr_model": "<dir>", "sv_model": "<dir>", "sv_savedir": "<dir>", "device": "cuda", "compute_type": null}
→ {"op": "asr", "id": "...", "wav": "...", "lang": "ru"}            ← {"ok": true, "id": "...", "text": "...", "lang": "ru", "dur_s": 2.1}
→ {"op": "embed", "id": "...", "wav": "..."}                        ← {"ok": true, "id": "...", "emb": [192 floats], "dur_s": 2.1}
→ {"op": "sim", "id": "...", "wav": "...", "refs": ["..."]}         ← {"ok": true, "id": "...", "sim": 0.71, "sims": [...]}
```

Клиент на стороне CLI — `babeldub/workers/client.py`: `WorkerClient(env|python_path, script, timeout, load_timeout, log_path).load()/request()/close()` (таймауты с убийством процесса, перезапуск после падения с повтором `load` и одним повтором запроса; ошибки `WorkerError`, `WorkerTimeout`, `WorkerCrashed`; нет env — `EXIT_ENV`). `GpuLock(project/state/locks/gpu.lock, owner, wait_s)` — эксклюзивный PID-лок GPU; лок мёртвого процесса снимается, занятый — `EXIT_USAGE` с подсказкой `--wait-lock`. Сессия qc для `cast`/`qa` — `qa/qc.py:QcSession`.

## 7. Аудио-API (`babeldub/audio`)

`io.py`: `read_audio(path) -> (np.ndarray float32 [frames, channels], sr)`, `write_wav(path, data, sr, subtype="PCM_16")` (атомарно), `probe(path) -> {sr, ch, dur_s, frames, subtype, format}`.
`dsp.py`: `to_mono` (1-D), `to_channels(data, ch)`, `resample(data, sr_in, sr_out, quality=16)` (полифазный sinc с окном Кайзера, без внешних зависимостей), `trim_silence(data, sr, threshold_db=-45, pad_s=0.04) -> (data, lead_s, trail_s)`, `voiced_bounds(data, sr) -> (start, end) | None`, `speech_seconds(data, sr)`, `loudness_lufs(data, sr)` (BS.1770-4, K-взвешивание, gating), `rms_db`, `level_db(data, sr, measure="auto")` (LUFS от 0,4 с, короче — RMS), `peak_db(data)`, `true_peak_db(data, sr)`, `match_loudness(data, sr, target, ceiling_db=-1.0, measure=...)`, `fade(data, sr, ms)`, `pad_silence(data, sr, lead_s, trail_s)`.
`stretch.py`: `time_stretch(in_wav, out_wav, ratio, method=None) -> "rubberband" | "ffmpeg-rubberband" | "atempo" | "copy"` (ratio = длительность выхода / входа; rubberband CLI `-3 -D`, затем фильтр rubberband в ffmpeg, затем цепочка `atempo`; ничего нет — `EXIT_ENV`), `available_methods()`, `atempo_chain(tempo)`.

## 8. Текст (`babeldub/text`)

`placeholders.py`: `extract_placeholders(text) -> list[str]` (`{0}`, `{name}`, `%s`, `%d`, `<tag>`, `[tag]`, `\n`, Unity Smart Strings `{Var:plural:...}`), `check_placeholders(src, tgt) -> [{severity: error|warn, kind: missing|extra, token, expected, found}]`, `has_errors(problems)`.
`syllables.py`: `count_syllables(text, lang)` (гласные для ru/uk/pl…; группы гласных для de/es/fr…; эвристика для en; CJK по символам), `budget_syllables(dur_s, lang) -> int | None` (слогов в секунду по языку), `fit_status(est, budget, over=1.15, under=0.5) -> ok|over|under|None`.
`normalize.py`: `prepare_for_tts(text, lang, numbers=True, yo="keep"|"strip"|"restore")` — удаление разметки (`strip_markup`, в том числе метки говорящего), числа словами (`numbers_to_words`, если есть `num2words`), `ё` (`apply_yo`).

## 9. Каталог моделей (`runtime/src/babeldub/data/models.yaml`)

```yaml
engines:
  qwen3tts:
    env: qwen3tts
    torch: {version: "2.8.0", index: "https://download.pytorch.org/whl/cu128"}
    packages: ["qwen-tts", "soundfile", "numpy"]
    worker: tts_qwen3.py
    languages: [zh, en, ja, ko, de, fr, ru, pt, es, it]
models:
  - name: Qwen3-TTS-12Hz-1.7B-Base
    repo: Qwen/Qwen3-TTS-12Hz-1.7B-Base
    engine: qwen3tts
    license: Apache-2.0
    commercial: true
    size_gb: 4.5
    vram_gb: 6
    tiers: [8gb, 12gb, 24gb]
```
- Движки: `qwen3tts`, `chatterbox`, `qc` (faster-whisper + SpeechBrain ECAPA), `dummy` (`env: null`, тестовый). API — `babeldub.envsetup.catalog` (`load_catalog`, `default_model`, `model_dir`, `is_model_downloaded`, `engine_for_language`, `tier_for_vram`).
- Модели с `commercial: false` получают `opt_in` и ставятся только с `--accept-license`.
- `synth` по умолчанию берёт скачанную общую (не языковую) модель движка, крупнее; языковые дообучения (`qwen3-tts-12hz-1.7b-ru-stress-cf`) — только явно через `tts.model`: на тестовых репликах они хуже сохраняли голос оригинального актёра.

## 10. Тесты и качество кода

- `pytest` в `runtime/tests/<область>/`; только синтетические данные (сгенерированные FSB5/банки/тексты). Никаких ассетов игр в репозитории.
- `runtime/tests/e2e/test_pipeline_synthetic.py` прогоняет все стадии через `cli.main` на синтетической Unity-игре с FMOD-банком (движок `dummy`, поддельный воркер qc, установка в копию игры и откат); в нём проверяется и честный `EXIT_ENV` у `qa` без env `qc`.
- Python ≥ 3.10, аннотации типов, `ruff` (конфиг в `pyproject.toml`).
- WAV/FLAC — через `soundfile`; пути — `pathlib`; UTF-8 везде; атомарная запись через `core.jsonl`.
- Каждая команда: обработчик `def cmd_x(args) -> int`, в конце `return emit({...}, code)`; прогресс стадии — `project.mark_stage(stage, status, **summary)`.
