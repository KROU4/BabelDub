# Source и Source 2 (Valve)

Читай, если в папке игры есть `*_dir.vpk`, `gameinfo.txt` и `bin/` (Source 1) или `game/*/pak01_dir.vpk`, `gameinfo.gi`, ресурсы `*_c` (Source 2).

## Уровень в BabelDub v0.1

- **Source 1: C** в v0.1 (план — позже, B: loose-файлы и субтитры). Если голос лежит loose-файлами, `babeldub scan` может показать их через адаптер `loose`.
- **Source 2: C** — нужны Workshop Tools игры.

## Source 1

### Где голос

- Архивы VPK (`pak01_dir.vpk` + части); loose-файлы в `custom/` или в папке игры перекрывают VPK (по гайдам сообщества).
- Голос: `sound/vo/...` (`.wav`, `.mp3`). В WAV бывают фонемы для липсинка (FacePoser).
- Soundscripts `scripts/game_sounds*.txt` связывают имя звука с файлом; `scenes.image` хранит скомпилированную хореографию.

### Где текст

Субтитры: `resource/closecaption_<язык>.txt` (UTF-16) → `.dat` через `captioncompiler`. **Токены субтитров — это имена звуков**, связь «реплика ↔ субтитр» прямая (по гайдам сообщества).

### Чем извлечь и собрать

| Инструмент | Назначение |
|---|---|
| VPKEdit | создание, чтение и запись VPK; CLI `vpkeditcli` |
| ValvePython/vpk | Python-библиотека VPK |
| valve_captioncompiler | переписанный `captioncompiler` на Python/C |

Нативный путь: loose-файлы в `custom/` или новый VPK; субтитры — `closecaption_<язык>.txt` → `.dat` через captioncompiler.

## Source 2

- VPK v2, скомпилированные ресурсы `*_c` (звук — `.vsnd_c`).
- Чтение: Source 2 Viewer (ValveResourceFormat), релиз 20.1.
- Перекомпиляция — Workshop Tools (`resourcecompiler`), если они есть у игры (не проверено).

## Подводные камни

- `sv_pure` и VAC в мультиплеере → только одиночная игра, по умолчанию не модифицировать онлайн.
- Фонемы липсинка в WAV теряются при замене файла.
- Developer wiki Valve (VDC) по Closed Captions 2026-10-21 была на обслуживании; сведения о субтитрах — по вторичным источникам.

## Ссылки

- VPKEdit — https://github.com/craftablescience/VPKEdit ; vpk — https://github.com/ValvePython/vpk
- Source 2 Viewer — https://github.com/ValveResourceFormat/ValveResourceFormat
- captioncompiler — https://github.com/agentOfChaos/valve_captioncompiler
- VDC Closed Captions — https://developer.valvesoftware.com/wiki/Closed_Captions
