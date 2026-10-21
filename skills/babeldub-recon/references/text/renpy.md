# Ren'Py: скрипты и переводы `game/tl/`

Читай, если игра на Ren'Py (см. `engines/renpy.md`).

## Уровень в BabelDub v0.1

**C** — CLI v0.1 скрипты Ren'Py не читает; только инструкции. Ren'Py — кандидат на уровень A в v0.2: у движка родной механизм перевода текста и голоса.

## Как устроено (по документации Ren'Py)

- Реплики — в `game/*.rpy` (скомпилированные `*.rpyc`, иногда внутри `.rpa`).
- Перевод — `game/tl/<язык>/*.rpy`:
  - `translate <язык> <id>:` — перевод реплики диалога по её идентификатору;
  - `translate <язык> strings:` — перевод строк интерфейса;
  - `translate <язык> python:` — код для языка (например, свой шрифт).
- Ren'Py SDK: Launcher → **Generate Translations** (заготовки `tl/`) и **Extract Dialogue** (идентификаторы реплик).

## Связь с озвучкой

- `voice "file.ogg"` перед репликой — явная связь.
- `config.auto_voice` — имя файла голоса строится из идентификатора реплики (`{id}`). Тогда идентификатор = ключ и текста, и звука.
- Перевод файлов: файл под `game/tl/<язык>/` с тем же относительным путём используется вместо оригинала, когда активен этот язык. Озвучка дубляжа кладётся туда же, не трогая оригинал.

## Чем извлечь

- unrpa или rpatool — распаковать `.rpa`.
- unrpyc — декомпилировать `.rpyc`, если исходных `.rpy` нет (совместимость зависит от версии Ren'Py).

## Подводные камни

- Модифицированные RPA.
- Шрифты без кириллицы: задать стиль шрифта в `translate <язык> python:`.

## Ссылки

- Ren'Py Translation — https://www.renpy.org/doc/html/translation.html
- Ren'Py Voice — https://www.renpy.org/doc/html/voice.html
- unrpyc — https://github.com/CensoredUsername/unrpyc ; unrpa — https://github.com/Lattyware/unrpa ; rpatool — https://codeberg.org/shiz/rpatool
