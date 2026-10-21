---
name: babeldub-pack
description: "Put a finished dub back into the game as native files: rebuild engine containers (FMOD banks, loose audio and others) with the accepted lines in the original characters' voices, verify them before install, install with backup and sha1 checks, roll back and reapply after game updates or Steam integrity checks. Use for build, verify, install, uninstall, reapply and repack questions: собери банк, установи озвучку, откати озвучку, верни оригинал, пересобери после обновления."
license: MIT
compatibility: "Local agent with shell access to the game folder; babeldub CLI 0.1.0 (install via the babeldub-doctor skill); vgmstream-cli and ffmpeg for some formats. No GPU needed. Native rebuild in v0.1: FMOD Studio banks and loose audio files; other engines follow the format references."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: сборка, установка и откат

Скилл стадий `build`, `verify`, `install` и отката. Задача — превратить принятые реплики, озвученные **голосом оригинальных героев**, в файлы, которые игра примет как родные, и поставить их **обратимо**: с бэкапом оригиналов, проверкой sha1 и откатом одной командой.

Скилл дополняет роль `packer` из скилла `babeldub-localize` (`references/roles/packer.md`).

## Главные правила

1. **В папку игры пишут только** `babeldub install`, `babeldub uninstall` и `babeldub reapply`. Руками ничего не копировать, не переименовывать и не удалять. В Claude Code это дополнительно стережёт хук-охранник BabelDub.
2. **Не устанавливать без успешного `verify`** — CLI сам откажет, если текущая сборка не прошла проверку.
3. **Исполняемые файлы игры не запускать.** Проверка библиотекой звука самой игры (`verify --game-runtime`) — только по явной просьбе пользователя.
4. **Онлайн-игры и античит** — не модифицировать. `install` и `reapply` откажут сами, если найдут EasyAntiCheat, BattlEye и т. п.; обходить это (`--force`) можно только для заведомо офлайн-одиночной игры по явному решению пользователя.
5. **Не распространять собранные контейнеры**: в них чужие ассеты (SFX, музыка из того же файла). Делиться можно проектом и инструкцией, а собирает каждый у себя.

## Ход работы

### 1. Сборка

```text
babeldub build [--codec auto|pcm16|fadpcm]
```

- В сборку идут только реплики, у которых последнее решение QA — `accept`, и есть файл `post/audio/<uid>.wav`. Остальные (`keep_original`, `skip`, без решения) звучат в оригинале.
- Для каждого изменённого контейнера адаптер формата пишет `build/out/<relpath>`, манифест — `build/build_manifest.json` (контейнеры, sha1 исходника и результата, кодек, число заменённых и оставленных реплик, модели и их лицензии).
- Вывод: `containers[]`, `replaced` и списки на внимание (`missing_wav`, `stale_attempt`, `unknown_lines` …). `stale_attempt` — решение `accept` относится не к последней попытке: нужно новое решение QA.
- Код 2 «native rebuild is not supported (level B/C)» — формат в v0.1 не собирается. Ничего не собирай руками: опиши путь из справочника формата (ниже) в отчёте.
- `--codec auto` (по умолчанию) у FMOD сохраняет записываемый кодек оригинала (PCM16 или FADPCM), а Vorbis заменяет на FADPCM. Подробно — [references/formats/fmod.md](references/formats/fmod.md).

### 2. Проверка

```text
babeldub verify [--game-runtime]
```

- Адаптер проверяет каждый собранный контейнер без запуска игры: структуру, число, порядок и имена записей, что заменённые реплики декодируются в нужную длину и совпадают с нашим звуком, что остальное осталось как было.
- Вывод: `ok`, `problems[]`. `ok = false` или код 3 — не устанавливать: причины в отчёт; одна пересборка (`babeldub build --force`) и повторная проверка допустимы.
- `--game-runtime` (FMOD) загружает библиотеку FMOD Studio из папки игры в отдельном процессе, без звука и без запуска игры, и сверяет с ней каждую пробную реплику. Это единственное место, где BabelDub касается кода игры, поэтому только по явной просьбе пользователя.

### 3. Установка

```text
babeldub install [--dry-run]
```

- Перед установкой CLI проверяет: сборка прошла `verify`; не запущен ни один процесс из папки игры (сама игра, её лаунчер, обработчик сбоев); нет античита; файлы игры — те самые оригиналы, из которых делалась сборка (sha1).
- Оригиналы копируются в `build/backup/<relpath>` и сверяются по sha1, затем файл игры заменяется атомарно (временный файл в той же папке и переименование). Сбой посреди установки откатывает уже заменённые файлы. Журнал — `build/install.json`.
- Вывод: `installed[]`, `backup`, `rollback` = `babeldub uninstall`. `--dry-run` показывает план без записи.
- Ошибки: «the game is running» — закрыть игру и лаунчер; «not the originals this build was made from» — игра обновилась или изменена после сборки: `build` → `verify` → `install` заново.

### 4. Откат и повторная установка

```text
babeldub uninstall          вернуть оригиналы из бэкапа (с проверкой sha1)
babeldub reapply            поставить озвучку снова после обновления игры или проверки целостности в Steam
```

Подробно — что именно делают команды, что такое `left_changed` и `changed[]`, как поступать после обновления игры и при потере бэкапа — [references/install-rollback.md](references/install-rollback.md).

## Что сказать в отчёте

- Какие файлы игры заменены, где бэкап (`build/backup`), каким кодеком собрано.
- Что поведение пересобранных файлов в **запущенной** игре автоматически не проверяется: если звука нет или игра падает, `babeldub uninstall`, затем пересборка с другим кодеком (для FMOD — `--codec pcm16`).
- Команды отката (`babeldub uninstall`) и возврата озвучки после обновления (`babeldub reapply`).
- Для катсцен и видео — просьба проверить, что реплики не обрываются.
- Лицензии моделей из `build/build_manifest.json`; напоминание не распространять собранные контейнеры.

## Форматы

Уровень поддержки контейнера показывает `babeldub scan` (`level`) и `inventory/report.md`; там же ссылки на нужный справочник. Читай справочник **только своего** формата:

| Формат | Уровень в v0.1 | Справочник |
|---|---|---|
| FMOD Studio `.bank` / `.fsb` | A: своя пересборка, проверка, установка | [references/formats/fmod.md](references/formats/fmod.md) |
| свободные аудиофайлы (`voice/*.ogg`, `*.wav` …) | A: перекодирование в формат оригинала | [references/formats/loose.md](references/formats/loose.md) |
| Unity Localization и Addressables (тексты) | только чтение; запись и CRC — следующая версия | [references/formats/unity-addressables.md](references/formats/unity-addressables.md) |
| Wwise `.bnk` / `.wem` / `.pck` | B: извлечение есть, сборка требует Wwise Authoring | [references/formats/wwise.md](references/formats/wwise.md) |
| Unreal Engine 4/5 | C: инструкции | [references/formats/unreal.md](references/formats/unreal.md) |
| Ren'Py | C: инструкции (нативный путь `game/tl/`) | [references/formats/renpy.md](references/formats/renpy.md) |
| Godot | C: инструкции | [references/formats/godot.md](references/formats/godot.md) |
| подмена в рантайме (BepInEx, MelonLoader, UE4SS) | C: запасной путь | [references/formats/runtime-mods.md](references/formats/runtime-mods.md) |

Пометки в справочниках: **[проверено]** — первоисточник или эксперимент; **[по гайдам]** — вторичный источник; **[не проверено]** — гипотеза, перед действием её нужно подтвердить.

## Справочники

- Бэкап, установка, откат, `reapply`, Steam, антивирус, потеря бэкапа — [references/install-rollback.md](references/install-rollback.md). Читай перед первой установкой и при любом сбое `install`, `uninstall`, `reapply`.
- Справочник формата из таблицы выше — когда `build` отказал по уровню или нужно объяснить пользователю, что возможно для этого движка.
