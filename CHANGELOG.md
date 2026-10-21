# Журнал изменений

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/).
Версия меняется только через `scripts/bump_version.py`; заголовок раздела релиза должен совпадать с тегом без `v`.

## [Не выпущено]

## [0.1.0] - 2026-10-21

Первый публичный выпуск: полный конвейер локализации и дубляжа игры голосами оригинальных героев под управлением ИИ-агента.

### Добавлено

- **Дубляж голосом оригинального героя.** Голосовым промптом для каждой реплики служит сама оригинальная реплика с её исходным текстом (`tts.ref_strategy=line`), поэтому переносятся тембр, эмоция и подача именно этой фразы. Для коротких, шумных и нерасшифрованных реплик берётся лучший референс персонажа (`char`), по умолчанию стратегия выбирается автоматически (`auto`).
- **Скиллы по стандарту Agent Skills:** `babeldub-localize` (оркестратор), `babeldub-doctor`, `babeldub-recon`, `babeldub-cast`, `babeldub-translate`, `babeldub-voice`, `babeldub-qa`, `babeldub-pack`. Одни и те же файлы работают в Claude Code, Codex, Gemini CLI, Antigravity, Cursor, Copilot, OpenCode и при загрузке в claude.ai.
- **Субагенты Claude Code** для ролей конвейера (разведка, кастинг, глоссарий, перевод, озвучка, QA, сборка). Они генерируются из `skills/babeldub-localize/references/roles/`; на других хостах те же роли выполняются в основной сессии.
- **Хуки Claude Code:** защита папки зарегистрированной игры от записи в обход `babeldub install` и сводка активного проекта при старте сессии.
- **CLI `babeldub`** с единым контрактом (один JSON на выходе, коды 0–4): окружение (`doctor`, `setup`, `models`), проект (`init`, `status`, `set`, `query`, `sample`, `games`), разбор игры (`scan`, `extract`, `link`), персонажи (`cast`), перевод (`translate`, `batch`, `glossary`), озвучка (`synth`, `post`), контроль качества (`qa`, `decide`, `report`), сборка и установка (`build`, `verify`, `install`, `uninstall`, `reapply`).
- **Адаптеры форматов:** FMOD Studio (`.bank`/FSB5), таблицы Unity Localization, Wwise, отдельные аудиофайлы. Уровень поддержки (A/B/C) для конкретной игры показывает `babeldub scan`.
- **Контроль качества:** обратное распознавание речи (CER к переводу), сходство голоса синтеза с оригиналом персонажа, длительность и пики; автоматические решения `regen`/`reref`/`shorten` по порогам.
- **Установка с откатом:** бэкап оригиналов с проверкой sha1, атомарная замена, `uninstall` и `reapply` после обновления игры.
- **Упаковка под хосты:** маркетплейс и плагин Claude Code, `.codex-plugin`, Agent Plugins 1.0 (`plugin.json`), расширение Gemini CLI (`gemini-extension.json`, `GEMINI.md`).
- **Релизы:** `babeldub-plugin`, `babeldub-skills` (по одному zip на скилл для claude.ai) и `babeldub-kit` («рабочая папка рядом с игрой»), `SHA256SUMS`.
- **Служебные скрипты:** `bump_version.py`, `validate_skills.py`, `build_release.py`, `install.ps1`, `install.sh`; CI на Ubuntu и Windows.
- **Документация:** установка по хостам (`docs/HOSTS.md`), ответственное использование (`docs/LEGAL.md`), контракт CLI (`docs/dev/CONTRACT.md`).

[Не выпущено]: https://github.com/KROU4/BabelDub/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/KROU4/BabelDub/releases/tag/v0.1.0
