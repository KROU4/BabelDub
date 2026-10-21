---
name: babeldub-doctor
description: "Check and prepare the machine for BabelDub game localization and dubbing (localize game, dub, revoice): install or update the babeldub CLI with uv, check GPU, VRAM, ffmpeg, vgmstream and disk space, then set up local TTS voice cloning engines, models and tools. Use on first run, when a babeldub command exits with code 4 or is missing: проверь окружение, установи BabelDub, не хватает ffmpeg, переозвучь игру, сделай русскую озвучку."
license: MIT
compatibility: "Local agent with shell access; Windows 10/11 or Linux; Python 3.10+ and uv; network access to PyPI, GitHub and Hugging Face for setup; NVIDIA GPU with 8+ GB VRAM recommended."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: окружение и установка

Скилл готовит машину к дубляжу игры голосами оригинальных героев: ставит CLI `babeldub` нужной версии, проверяет железо и утилиты, ставит движки синтеза, модели и инструменты. Вопросов пользователю не задаёт: план установки печатается, модели с разрешительными лицензиями (Apache-2.0, MIT) ставятся без вопроса.

Применяй его:

- при первом запуске BabelDub;
- если команды `babeldub` нет или её версия не равна `metadata.babeldub-cli` (`0.1.0`);
- если команда `babeldub` вернула код 4 (или `doctor` — код 3 с непустым `todo[]`);
- по просьбе «проверь окружение».

## 1. CLI нужной версии

1. Выполни `babeldub --version`. Ожидается `babeldub 0.1.0`. Совпало — переходи к шагу 2.
2. Иначе запусти `scripts/bootstrap.py` из папки этого скилла (в Claude Code папка скилла — `${CLAUDE_SKILL_DIR}`):
   - в Claude Code: `python "${CLAUDE_PLUGIN_ROOT}/skills/babeldub-doctor/scripts/bootstrap.py" --plugin-root "${CLAUDE_PLUGIN_ROOT}"`;
   - на других хостах: `python <папка скилла>/scripts/bootstrap.py` или `uv run --no-project --script <папка скилла>/scripts/bootstrap.py`.

   Скрипт ставит CLI командой `uv tool install` из папки `runtime/` плагина или репозитория, а если её нет — из git-тега релиза `v0.1.0`. Он печатает один JSON:

   | Поле | Смысл |
   |---|---|
   | `action` | `none`, `installed`, `reinstalled`, `planned` |
   | `installed`, `expected` | установленная и ожидаемая версии |
   | `exe`, `on_path` | путь к `babeldub` и есть ли он в `PATH` |
   | `source`, `source_kind` | откуда ставили: `plugin`, `repo`, `git`, `explicit`, `env` |
   | `cli_error` | хвост ошибки, если CLI не запускается |
   | `hint`, `path_hint` | что сделать при проблеме |

   Флаги: `--check` (только проверить), `--force` (переустановить), `--dry-run` (показать команду), `--source <папка runtime или git+https-требование>`.

3. Коды `bootstrap.py`:
   - 0 — готово;
   - 4 без uv — поставь uv официальной командой из `hint` (Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` или `winget install astral-sh.uv`; Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`), открой новый терминал и повтори;
   - 4 при ошибке установки — смотри `detail` и `hint` (сеть, нет git для git-источника, неверный `--source`);
   - 1 — пакет установлен, но `babeldub --version` падает: покажи `cli_error` в отчёте, дальше не идти.
4. `on_path` = false: вызывай CLI по полному пути из `exe` до конца сессии или выполни `uv tool update-shell` и открой новый терминал.

## 2. Диагностика

```text
babeldub doctor
```

Проверяет ОС, Python, утилиты, GPU (имя, VRAM, драйвер через `nvidia-smi`), свободное место, venv движков и скачанные модели; рекомендует тир. Полный отчёт — в файле из поля `report`.

| Поле вывода | Смысл | Что делать |
|---|---|---|
| `missing[]` (код 4) | нет обязательной утилиты: `uv`, `ffmpeg`, `ffprobe` | команды установки — в `install{}` (Windows: `winget install Gyan.FFmpeg`; Linux: `sudo apt install ffmpeg`); затем новый терминал и снова `doctor` |
| `todo[]` (код 3) | нет venv движка (`env:<имя>`) или модели (`model:<имя>`) | шаг 3 |
| `optional_missing[]` | нет необязательной утилиты: `vgmstream-cli`, `rubberband`, `uvx`, `git` | `vgmstream-cli` нужен для извлечения большинства форматов: `babeldub setup --tools`. `rubberband` (GPL) ставит пользователь по желанию; без него CLI растягивает звук через `ffmpeg atempo` |
| `gpu`, `tier` | видеокарта и рекомендованный тир | без NVIDIA GPU синтез идёт на CPU и займёт часы: продолжай, но предупреди пользователя |
| `warnings[]` | мало места, мало VRAM, сломанный venv | следуй тексту предупреждения (например, `babeldub setup --engines <имя> --force` для сломанного venv) |

Что ставится по умолчанию (каталог CLI v0.1; актуальный список — `babeldub models list`):

| Назначение | Модели | Лицензия |
|---|---|---|
| синтез, тиры 8/12/24 ГБ | `Qwen3-TTS-12Hz-1.7B-Base` (для всех языков движка); `qwen3-tts-12hz-1.7b-ru-stress-cf` (точнее ударения, но хуже голос героя) — не ставится по умолчанию, только `babeldub models fetch` по решению пользователя | Apache-2.0 (у ru-stress-cf часть обучающих текстов без заявленной лицензии — предупреждение каталога) |
| синтез, тир CPU (и при VRAM меньше 8 ГБ) | `Qwen3-TTS-12Hz-0.6B-Base` | Apache-2.0 |
| синтез для языков вне Qwen3-TTS (например pl, tr) | `chatterbox-multilingual` (в каждом файле неслышимый водяной знак PerTh) | MIT |
| QA и кастинг (venv `qc`) | `faster-whisper-large-v3-turbo` (распознавание речи), `spkrec-ecapa-voxceleb` (сходство голоса) | MIT, Apache-2.0 |

Qwen3-TTS поддерживает zh, en, ja, ko, de, fr, ru, pt, es, it.

## 3. Движки, модели, утилиты

1. Покажи план: `babeldub setup --dry-run`. Вывод: `planned[]` (что ставится, размер, лицензия), `download_gb`, `blocked[]`, `plan` (файл с подробностями).
2. Лицензии: `babeldub models list` (вывод `models[]` с лицензиями и статусом).
   - По умолчанию ставятся только модели с разрешительными лицензиями (Apache-2.0, MIT).
   - Некоммерческие (NC) модели CLI блокирует (`blocked[]`) до явного согласия: их ставят только после того, как пользователь сам попросил такую модель и подтвердил лицензию; тогда — `babeldub setup --accept-license <модель>`. Без такой просьбы NC-модели не трогай.
   - Предупреждения каталога (`warning` у модели, например про происхождение обучающих данных) перенеси в отчёт.
3. Установи: `babeldub setup` (движки, модели и утилиты для рекомендованного тира и языка проекта) или явно `babeldub setup --engines qwen3tts,qc --tools --models auto`. Отдельная модель: `babeldub models fetch <name>`.
4. Вывод: `done[]`, `skipped[]`, `failed[]`, `blocked[]`.
   - Код 1 (`failed[]`): прочитай лог упавшего шага (путь в выводе или в файле плана), устрани причину, повтори `setup` один раз. Сетевые сбои обычно проходят со второго раза.
   - Код 3 (`blocked[]`): причина — в файле плана (`reason`): нет uv или git, некоммерческая лицензия, нет `uvx`/`huggingface_hub`.
   - Код 4 «not enough disk space»: освободи место или перенеси `BABELDUB_HOME` (раздел 4).
5. Снова `babeldub doctor`: код 0, `missing[]` и `todo[]` пусты.

Установка долгая: venv с torch и веса моделей — это гигабайты (CLI предупреждает, если свободно меньше ~20 ГБ). Перед запуском одной строкой скажи пользователю, что ставится и сколько примерно займёт (`download_gb` из плана); на хостах с фоновыми командами запускай `setup` в фоне.

## 4. Где лежат данные

- `BABELDUB_HOME`: Windows `%LOCALAPPDATA%\BabelDub`, Linux `~/.local/share/babeldub`; переопределяется переменной окружения `BABELDUB_HOME`.
- Внутри: `envs/<движок>` (venv движков), `models/<name>` (веса), `hf-cache/`, `tools/` (vgmstream и др.), `projects/<slug>` (проекты), `games.json` (реестр папок игр).
- Мало места на системном диске — задай `BABELDUB_HOME` на другой диск **до** `setup` и `init` и используй короткий путь (Windows `MAX_PATH`).
- Ничего не кладётся в папку игры и в папку плагина: плагин заменяется при обновлении.

## 5. Результат

Верни оркестратору (или пользователю, если скилл вызван напрямую) короткую сводку:

```json
{"cli": "0.1.0", "exe": "...", "tier": "8gb", "gpu": "NVIDIA GeForce RTX 3060 Ti 8 GB", "installed": ["qwen3tts", "qc", "vgmstream-cli"], "missing": [], "todo": [], "warnings": []}
```

Значения в примере условные: бери их из вывода `bootstrap.py`, `babeldub doctor` и `babeldub setup`.

## Правила

1. Не запускать исполняемые файлы игр и ничего не писать в папки игр.
2. Не ставить некоммерческие модели без явной просьбы и согласия пользователя.
3. GPL-утилиты (Rubber Band) не скачивать в BabelDub: их ставит пользователь, если хочет.
4. Не менять системные настройки и драйверы. `BABELDUB_HOME` переносить только если пользователь сам об этом попросил или места на диске по умолчанию явно не хватает; перенос и новый путь указать в отчёте.
