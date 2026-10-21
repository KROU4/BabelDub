# AGENTS.md — контекст для агентов-разработчиков BabelDub

Этот файл читают Codex, Copilot, Cursor, Gemini CLI, OpenCode и Claude Code (2.1.277+), когда работают **над кодом BabelDub**. Пользователям плагина он не нужен: их ведёт скилл `babeldub-localize`.

## Что это за репозиторий

BabelDub — публичный набор скиллов, агентов и хуков для ИИ-агентов (Claude Code, Codex, Gemini CLI и других) плюс Python-CLI `babeldub`. Вместе они локализуют и переозвучивают **любые игры**: находят озвучку и тексты, переводят, клонируют голоса оригинальных героев локальными TTS-моделями и собирают файлы обратно в нативные форматы движка.

**Главный принцип озвучки — дубляж, а не синтез речи.** Каждая реплика на целевом языке звучит голосом оригинального героя: тот же тембр, те же эмоции и подача. Поэтому голосовой промпт по умолчанию — сама оригинальная реплика с её исходным текстом (`tts.ref_strategy=line`), для коротких и шумных реплик — лучший референс персонажа (`char`), а QA сравнивает голос синтеза с оригиналом. Любое изменение, которое ослабляет этот принцип, требует обсуждения в issue.

Один репозиторий одновременно служит маркетплейсом и плагином Claude Code, плагином Codex, расширением Gemini CLI и пакетом Agent Plugins 1.0.

## Структура

```text
.claude-plugin/plugin.json, marketplace.json   Claude Code (их же читают Codex и Copilot)
.codex-plugin/plugin.json                       Codex / ChatGPT: interface (имя, промпты, логотип)
plugin.json                                     Agent Plugins 1.0 (закрытая схема, только skills)
gemini-extension.json, GEMINI.md                Gemini CLI
skills/<name>/SKILL.md                          ЕДИНСТВЕННЫЙ источник скиллов (стандарт Agent Skills)
  babeldub-localize/references/roles/*.md       роли конвейера (из них генерируются агенты)
  babeldub-localize/references/hosts/*.md       отличия хостов
commands/*.md                                   слэш-команды Claude Code (тонкие шимы к скиллам)
claude/agents/*.md                              субагенты Claude Code — генерируются, руками не править
claude/hooks.json                               хуки Claude Code (обработчики в scripts/hooks/)
runtime/                                        Python-пакет и CLI `babeldub` (src-layout, uv)
  src/babeldub/core/                            ядро: вывод, пути, проект, манифесты
  src/babeldub/adapters/                        форматы и middleware (base.py — интерфейс)
  src/babeldub/commands/                        команды CLI
  src/babeldub/workers/                         воркеры моделей (свои venv, JSONL-протокол)
  tests/                                        pytest, только синтетические данные
scripts/                                        gen_agents, bump_version, validate_skills, build_release, install.*
docs/dev/CONTRACT.md                            контракт CLI: команды, файлы проекта, коды выхода
docs/HOSTS.md, docs/LEGAL.md                    установка по хостам, ответственное использование
.github/                                        CI, релизы, шаблоны issue и PR
```

## Команды разработчика

Все команды — из корня репозитория. Python ≥ 3.10, uv.

```bash
uv sync --project runtime --extra dev                              # окружение runtime/.venv
uv run --project runtime ruff check runtime                        # линтер (конфиг в runtime/pyproject.toml)
uv run --project runtime ruff check --config runtime/pyproject.toml scripts
uv run --project runtime pytest runtime/tests -q                   # тесты CLI
uv run --project runtime pytest runtime/tests/<область> -q         # одна область
uv run --project runtime pytest scripts -q                         # тесты хуков и служебных скриптов
uv run --project runtime python scripts/validate_skills.py         # скиллы, манифесты, гигиена репо
uv run --project runtime python scripts/bump_version.py --check    # версии во всех манифестах совпадают
uv run --project runtime python scripts/gen_agents.py --check      # claude/agents совпадает с ролями
claude plugin validate . --strict                                  # строгая проверка плагина и маркетплейса
uv run --project runtime python scripts/build_release.py --out <временная папка>
```

Перед PR должны проходить все команды из списка. CI (`.github/workflows/ci.yml`) гоняет их на Ubuntu и Windows.

## Правила

1. **`docs/dev/CONTRACT.md` — источник правды** для CLI, файлов проекта и протокола воркеров. Меняете поведение команды — обновите контракт в том же PR.
2. **Один JSON на выходе.** Каждая команда печатает в stdout ровно один JSON через `core.output.emit` и возвращает код 0/1/2/3/4. Логи — только в stderr или в `logs/` проекта. Ожидаемые ошибки — `BabelDubError(message, code=..., hint=...)`.
3. **Никаких заглушек.** Без `TODO`, `NotImplementedError` и веток «потом». Если функция вне объёма версии, команда честно возвращает `BabelDubError` с `hint`.
4. **Никаких игровых ассетов в репозитории.** Ни банков, ни архивов, ни аудио реплик, ни извлечённых текстов. Тесты генерируют синтетические FSB5, банки и таблицы на лету. `validate_skills.py` блокирует `*.bank`, `*.wem`, `*.bnk`, `*.pak` и файлы больше 1 МБ.
5. **Папка игры только для чтения** до `babeldub install`. Исполняемые файлы игры не запускаются никогда, ни CLI, ни тестами.
6. **Тяжёлое — вне CLI.** CLI зависит только от лёгких пакетов из `runtime/pyproject.toml`. Всё, что требует `torch`, работает в воркерах (`runtime/src/babeldub/workers/*.py`) в отдельных venv; воркеры не импортируют `babeldub`.
7. **Скиллы портируемые.** Во frontmatter только `name`, `description`, `license`, `compatibility`, `metadata`; `name` совпадает с папкой; `description` 50–1024 символа и в кавычках; тело до 500 строк, детали — в `references/`. Специфика хостов — в `references/hosts/*.md`, а не в теле скилла.
8. **Агенты генерируются.** Роли правятся в `skills/babeldub-localize/references/roles/*.md`, затем `python scripts/gen_agents.py`. Новый файл в `claude/agents/` нужно перечислить в `agents` в `.claude-plugin/plugin.json` (каталог там указать нельзя, только `.md`-файлы). Папки `agents/` и `hooks/` в корне не создавать: Gemini пытается их загрузить.
9. **Версия меняется только через** `python scripts/bump_version.py X.Y.Z` (манифесты, `runtime/pyproject.toml`, `__init__.py`, `uv.lock`, `metadata.babeldub-cli` в скиллах) плюс раздел в `CHANGELOG.md`. В `marketplace.json` версии нет намеренно.
10. **Файлы:** UTF-8 без BOM, JSON без BOM (Gemini падает на BOM), пути в манифестах через `/` и с `./`. Нет каталога `bin/` в корне (claude.ai и Cowork не ставят такой плагин) и нет `CLAUDE.md` в корне (из плагина он не грузится).
11. **Код и идентификаторы — по-английски**, комментарии краткие и по-английски. Тексты для людей (README, docs, шаблоны GitHub, сообщения в отчётах) — по-русски.
12. **Секреты и модели** в репозиторий не попадают. Веса скачивает `babeldub setup` в `BABELDUB_HOME` с показом лицензии; некоммерческие модели — только с явного согласия пользователя.

## Как добавить движок или формат

Кратко (подробно — в `CONTRIBUTING.md`): адаптер в `runtime/src/babeldub/adapters/<name>/` по интерфейсу `adapters/base.py`, запись в `REGISTRY`, сигнатуры движка в `runtime/src/babeldub/data/`, тесты на синтетике в `runtime/tests/<name>/`, справка для агента в `skills/babeldub-recon/references/engines/` и `skills/babeldub-pack/references/formats/`.
