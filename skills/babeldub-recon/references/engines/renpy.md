# Ren'Py

Читай, если в папке игры есть `game/`, `renpy/`, `*.rpa`, `*.rpyc`.

## Уровень в BabelDub v0.1

**C** — отдельного адаптера Ren'Py в v0.1 нет. Если файлы голоса лежат в `game/` отдельными файлами (не в `.rpa`), `babeldub scan` может показать их через адаптер `loose` — ориентируйся на `level` из вывода scan. План: кандидат на уровень A в v0.2, потому что у Ren'Py есть родной механизм перевода голоса.

## Как опознать

`game/`, `renpy/`, архивы `game/*.rpa`, скрипты `*.rpy` и скомпилированные `*.rpyc`.

## Где голос

- Обычно `game/voice/*.ogg` или внутри `.rpa`.
- Оператор `voice "file.ogg"` в скриптах; `config.voice_filename_format`; **`config.auto_voice`** — имя файла строится из идентификатора реплики (`{id}` заменяется на dialogue identifier).

## Где текст

Скрипты `*.rpy`/`*.rpyc` и переводы `game/tl/<язык>/` — см. `text/renpy.md`.

## Чем извлечь

| Инструмент | Назначение | Состояние на 2026-10 |
|---|---|---|
| unrpa | распаковка `.rpa` | 2.3.0 (2019), устаревает |
| rpatool | распаковка и упаковка `.rpa` | переехал на Codeberg |
| unrpyc | декомпиляция `.rpyc` | v2.0.4 |
| Ren'Py SDK | Launcher → Generate Translations и Extract Dialogue (идентификаторы реплик для `auto_voice`) | официальный |

Формат RPA (заголовок с XOR-ключом и индекс zlib-pickle) — детали не проверялись.

## Нативный путь пересборки (по документации Ren'Py, проверено по первоисточнику)

- Перевод: `game/tl/<язык>/*.rpy` с блоками `translate <язык> <id>:` и `translate <язык> strings:`.
- **Перевод файлов:** файл `game/tl/<язык>/<путь>` используется вместо `game/<путь>`, когда этот язык активен. Пример из документации: `game/tl/french/omelette.ogg` вместо `game/omelette.ogg`.
- Автоголос работает так же, если путь под `tl/<язык>` повторяет оригинальный. Значит, дубляж кладётся рядом, не трогая оригинальные файлы.

## Подводные камни

- Совместимость unrpyc с версией Ren'Py.
- Модифицированные RPA.
- Шрифты без кириллицы: стиль шрифта задают в блоке `translate <язык> python:`.

## Ссылки

- Ren'Py Voice — https://www.renpy.org/doc/html/voice.html
- Ren'Py Translation — https://www.renpy.org/doc/html/translation.html
- unrpa — https://github.com/Lattyware/unrpa ; rpatool — https://codeberg.org/shiz/rpatool ; unrpyc — https://github.com/CensoredUsername/unrpyc
