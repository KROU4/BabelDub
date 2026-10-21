# Как участвовать в BabelDub

Спасибо за интерес к проекту. Больше всего BabelDub нужны новые движки и форматы, проверки на реальных играх и улучшения качества дубляжа. Ниже — как устроена работа и какие правила обязательны.

## Главное правило: никаких файлов игр

В репозиторий, issues и PR **нельзя** добавлять файлы игр: банки, архивы, аудио реплик, извлечённые тексты, скриншоты с текстами диалогов целиком, а также синтезированные голоса актёров. Их распространение нарушает права правообладателей. Тесты работают только на синтетических данных, которые генерируются на лету. `scripts/validate_skills.py` в CI отклоняет `*.bank`, `*.wem`, `*.bnk`, `*.pak`, `*.fsb`, `*.pck` и любые файлы больше 1 МБ (кроме `assets/*.png`).

Если для разбора формата нужен образец, опишите структуру: размеры, заголовки, смещения, вывод `babeldub scan`. Шаблон «Поддержка новой игры или движка» подскажет, что собрать.

## Окружение

Нужны Python 3.10+ (CI использует 3.12), [uv](https://docs.astral.sh/uv/), git. Для полного прогона на игре — ffmpeg и видеокарта NVIDIA с 8+ ГБ VRAM, но для разработки и тестов они не нужны.

```bash
git clone https://github.com/KROU4/BabelDub
cd BabelDub
uv sync --project runtime --extra dev
uv run --project runtime babeldub --version
```

Плагин целиком на одну сессию Claude Code: `claude --plugin-dir .`. Скиллы для других хостов — см. [docs/HOSTS.md](docs/HOSTS.md).

## Проверки перед PR

```bash
uv run --project runtime ruff check runtime
uv run --project runtime ruff check --config runtime/pyproject.toml scripts
uv run --project runtime pytest runtime/tests -q
uv run --project runtime pytest scripts -q
uv run --project runtime python scripts/validate_skills.py
uv run --project runtime python scripts/bump_version.py --check
uv run --project runtime python scripts/gen_agents.py --check
claude plugin validate . --strict
```

CI запускает то же самое на Ubuntu и Windows, а `claude plugin validate` и официальный валидатор `skills-ref` — на Ubuntu.

## Контракт CLI

[docs/dev/CONTRACT.md](docs/dev/CONTRACT.md) — источник правды для команд, файлов проекта и протокола воркеров. Коротко:

- команда печатает в stdout **ровно один** компактный JSON через `core.output.emit` и возвращает код 0 (готово), 1 (ошибка), 2 (аргументы или не пройдена стадия), 3 (нужно решение), 4 (не хватает окружения);
- ожидаемые ошибки — `BabelDubError(message, code=..., hint=...)` с понятной подсказкой для агента;
- канонические манифесты пишет только CLI; агенты пишут `*.agent.jsonl` и `*.out.jsonl`, CLI их проверяет и сливает;
- повторный запуск стадии не ломает результат и не переделывает готовое без `--force`;
- никаких заглушек: если возможность вне объёма версии, команда возвращает `BabelDubError` с `hint`, а не делает вид, что работает.

Меняете поведение команды или формат файла — обновите контракт в том же PR.

## Как добавить движок или формат (адаптер)

Адаптер отвечает за один контейнер или middleware: находит ассеты, перечисляет реплики, декодирует их в WAV, читает таблицы текстов, пересобирает контейнер с новым звуком и проверяет результат. Команды `scan`, `extract`, `link`, `build`, `verify` работают с адаптерами только через интерфейс, поэтому новый движок не трогает конвейер.

1. **Код.** Создайте `runtime/src/babeldub/adapters/<name>/adapter.py` с классом-наследником `Adapter` из [`adapters/base.py`](runtime/src/babeldub/adapters/base.py):
   - `discover(game_dir)` — дешёвый поиск ассетов без декодирования, возвращает `Asset` с `role` и уровнем поддержки;
   - `list_lines(game_dir, asset)` — записи реплик по контракту `lines.jsonl` (`id` через `core.ids.line_id`, `uid`, `entry`, `kind`, `locale`, `audio`);
   - `extract_audio(...)` — WAV в `audio/orig/<uid>.wav`;
   - `read_texts(...)` — строки таблиц `{key, table, locale, lang, text, source}`;
   - `build(...)` — пересобранная копия в `build/out/<relpath>`; реплики без замены остаются как были;
   - `verify(...)` — проверки собранного контейнера без запуска игры.

   Чего формат не умеет, то метод честно отклоняет через `BabelDubError(EXIT_USAGE)` с подсказкой.
2. **Регистрация.** Добавьте строку в `REGISTRY` в `adapters/base.py` (`"<name>": "babeldub.adapters.<name>.adapter:<Class>"`) и сигнатуры движка или middleware в `runtime/src/babeldub/data/`, чтобы `scan` его узнавал.
3. **Уровень поддержки.** A — извлечение и нативная сборка без проприетарных инструментов; B — нужен инструмент пользователя или мод-лоадер; C — только чтение, пересборка по инструкции в скилле.
4. **Тесты.** `runtime/tests/<name>/` на синтетике: соберите минимальный контейнер в памяти или во временной папке (`tmp_path`), проверьте разбор, извлечение, пересборку и что неизменённые реплики байт-в-байт совпадают с оригиналом. Внешние инструменты (vgmstream, ffmpeg) в тестах заменяйте фикстурами или помечайте `pytest.mark.skipif`, если инструмента нет.
5. **Знания для агента.** Опишите формат в `skills/babeldub-recon/references/engines/<engine>.md` (как найти, что внутри, подводные камни) и путь сборки в `skills/babeldub-pack/references/formats/<format>.md`. Скилл загружает эти файлы только при обнаружении движка, поэтому пишите их самодостаточными.
6. **Проверка на реальной игре** — только локально. В PR опишите, на какой игре и версии проверяли, и приложите сводки команд, но не файлы.

## Скиллы, роли и агенты

- Скиллы лежат только в `skills/<name>/SKILL.md` и соблюдают стандарт Agent Skills: во frontmatter только `name`, `description`, `license`, `compatibility`, `metadata`; `name` совпадает с папкой; `description` 50–1024 символа в кавычках, с фразами-триггерами на русском и английском; тело до 500 строк, подробности в `references/`.
- Роли конвейера описаны в `skills/babeldub-localize/references/roles/*.md`. Субагенты Claude Code в `claude/agents/` **генерируются** командой `python scripts/gen_agents.py`; руками их не правьте. Новый файл агента перечислите в `agents` в `.claude-plugin/plugin.json`: `validate_skills.py` проверяет, что список и папка совпадают.
- Отличия хостов (Claude Code, Codex, Gemini и другие) описываются в `skills/babeldub-localize/references/hosts/`, а не в теле скиллов.
- После изменений прогоните `claude plugin validate . --strict` и `python scripts/validate_skills.py`.

## Модели и внешние инструменты

- Веса моделей и бинарники в репозиторий не кладутся. Каталог моделей — `runtime/src/babeldub/data/models.yaml` с репозиторием, ревизией, лицензией и пометкой о коммерческом использовании; `babeldub setup` скачивает их в `BABELDUB_HOME`.
- Новая модель с некоммерческой лицензией допустима только как опция, которую пользователь включает явно.
- Код воркеров (`runtime/src/babeldub/workers/`) работает в отдельном venv движка и не импортирует `babeldub`. Логи — только в stderr, протокол — JSONL по stdin/stdout (контракт, §6).
- GPL-инструменты (например, Rubber Band) не вендорятся: CLI вызывает их как внешние программы, установленные пользователем.

## Стиль

- Python ≥ 3.10, аннотации типов, `pathlib`, UTF-8 везде, `ruff` по конфигу `runtime/pyproject.toml`.
- Код, идентификаторы и комментарии — по-английски; документация и тексты для пользователей — по-русски.
- JSON и Markdown — UTF-8 без BOM, переводы строк LF (`.ps1`, `.cmd` — CRLF; см. `.gitattributes`, `.editorconfig`).
- Небольшие сфокусированные PR. Сообщения коммитов — в повелительном наклонении, по-английски (`Add Wwise BNK rebuild`).

## Версии и релизы

1. `python scripts/bump_version.py X.Y.Z` (или `patch` / `minor` / `major`) меняет версию во всех манифестах, `runtime/pyproject.toml`, `__init__.py`, `uv.lock`, `metadata.babeldub-cli` скиллов и запасную версию в `bootstrap.py`. Упоминания старой версии в тексте скиллов и документации скрипт не трогает, а выводит списком `файл:строка` — проверьте их вручную. Список мест — в `.version-bump.json`.
2. В `CHANGELOG.md` переименуйте «Не выпущено» в `## [X.Y.Z] - ГГГГ-ММ-ДД` и добавьте новый пустой раздел «Не выпущено».
3. Тег `vX.Y.Z` запускает `.github/workflows/release.yml`: тесты, проверки, `scripts/build_release.py` и публикацию релиза с тремя архивами и `SHA256SUMS`.

## Поведение

Будьте вежливы и конструктивны. Обсуждайте код и решения, а не людей. Об уязвимостях сообщайте приватно (см. [SECURITY.md](SECURITY.md)).
