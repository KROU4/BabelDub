## Что изменено

<!-- Кратко: что и зачем. Ссылка на issue, если есть: Closes #123 -->

## Как проверено

<!-- Команды и результат. Для адаптера: на какой синтетике и (локально) на какой игре проверено. -->

## Чек-лист

- [ ] `uv run --project runtime ruff check runtime` и `ruff check --config runtime/pyproject.toml scripts` без ошибок
- [ ] `uv run --project runtime pytest runtime/tests -q` и `pytest scripts -q` проходят
- [ ] `python scripts/validate_skills.py` без ошибок
- [ ] `python scripts/bump_version.py --check` и `python scripts/gen_agents.py --check` проходят
- [ ] `claude plugin validate . --strict` проходит (если менялись скиллы, агенты, команды, хуки или манифесты)
- [ ] Тесты только на синтетических данных; **в PR нет файлов игр**, аудио реплик и извлечённых текстов
- [ ] Если менялся CLI или формат файлов проекта, обновлён `docs/dev/CONTRACT.md`
- [ ] Если менялись роли агентов, правки сделаны в `skills/babeldub-localize/references/roles/`, а `claude/agents/` перегенерирован
- [ ] В `CHANGELOG.md` добавлена запись в раздел «Не выпущено»
