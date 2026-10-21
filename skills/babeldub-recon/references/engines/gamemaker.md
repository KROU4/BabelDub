# GameMaker

Читай, если в папке игры есть `data.win`, `audiogroup*.dat`, `options.ini`.

## Уровень в BabelDub v0.1

**C** — CLI v0.1 GameMaker не разбирает; только инструкции. План — позже, уровень B (скрипты UndertaleModTool нужно адаптировать к пакетному запуску).

## Как опознать

`data.win` — IFF-контейнер `FORM` с чанками GEN8, OPTN, LANG, EXTN, SOND, AGRP, SPRT, BGND, PATH, SCPT, GLOB, SHDR, FONT, TMLN, OBJT, ROOM, TPAG, CODE, VARI, FUNC, STRG, TXTR, **AUDO** и др. (по гайдам сообщества).

## Где голос

- Чанк `AUDO` в `data.win`.
- `audiogroupN.dat` — свой `FORM` с `AUDO`.
- Внешние `.ogg` для стримов.

## Где текст

Чанк `STRG` в `data.win` или включённые файлы (`datafiles/*.json`, `*.csv`, `*.ini`). Шрифты часто растровые: кириллицу нужно импортировать. См. `text/generic.md`.

## Чем извлечь и собрать

**UndertaleModTool** 0.9.2.0, есть CLI `UndertaleModCli`. Скрипты:

- строки: пары `ExportAllStrings.csx` / `ImportAllStrings.csx` (TXT) и `ExportAllStringsJSON.csx` / `ImportAllStringsJSON.csx` (JSON);
- звук: `ExportAllSounds`; `ImportSounds` и `ImportSingleSound` (OGG внешний или встроенный, выбор audiogroup; «WAV cannot be external»).

Скрипты интерактивные (`ScriptQuestion`, `PromptLoadFile`): для пакетного запуска агентом через `UndertaleModCli` их придётся адаптировать (не проверено).

## Подводные камни

- YYC-сборки: код нативный и не декомпилируется, но `STRG` и `AUDO` доступны (по гайдам сообщества).
- Версия байткода.
- Растровые шрифты без кириллицы.

## Ссылки

- UndertaleModTool — https://github.com/UnderminersTeam/UndertaleModTool
