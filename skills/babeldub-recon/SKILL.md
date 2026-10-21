---
name: babeldub-recon
description: "Find out what is inside a video game before localization and dubbing (localize game, dub, revoice): detect the engine, audio middleware (FMOD, Wwise, CRIWARE, loose files, video) and text tables, inventory voice lines, extract original audio and link subtitles to voice via babeldub scan, extract and link. Use for the recon stage or questions like: что внутри игры, какой движок, где озвучка и субтитры, переведи игру, переозвучь."
license: MIT
compatibility: "Local agent with shell access to the game folder (read-only); babeldub CLI 0.1.0 installed via the babeldub-doctor skill; vgmstream-cli for decoding. Works without GPU."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: разведка игры

Скилл отвечает на вопросы «на каком движке игра», «где лежит голос и тексты», «на каких языках», «можно ли это собрать обратно» и готовит оригинальные реплики. Эти реплики потом станут голосовыми промптами дубляжа: озвучка пойдёт голосом оригинального героя. Поэтому разведка должна найти **сам голос** каждой реплики и **её точный исходный текст**.

Скилл универсален. Low-Budget Repairs (Unity + FMOD + Unity Localization) в примерах только иллюстрация.

## Правила

1. **Папка игры только для чтения.** Не копировать в неё, не переименовывать, не распаковывать на месте. Все рабочие файлы — в проекте (`BABELDUB_HOME/projects/<slug>`).
2. **Ничего не запускать из папки игры:** ни exe, ни скрипты, ни DLL. В папках игр бывают посторонние исполняемые файлы. Проверка рантаймом игры (`babeldub verify --game-runtime`) — только по явной просьбе пользователя.
3. **Бинарные файлы игры целиком не читать.** Смотри листинг папок, имена и размеры файлов, вывод CLI.
4. **Неизвестное перечислять, а не угадывать.** Если формат не опознан, так и пиши.
5. **Онлайн-игры и античит** (EasyAntiCheat, BattlEye, VAC в мультиплеере): по умолчанию не модифицировать; сообщить пользователю.

## Шаги

1. Проект должен существовать (`babeldub init`, скилл `babeldub-localize`). Проверь: `babeldub status`.
2. `babeldub scan` → `engine{id, version, backend}`, `middleware[]`, `text_systems[]`, `voice_assets[]`, `text_assets[]`, `counts{}`, `level`, `warnings[]`, `next`. Подробный отчёт — `inventory/report.md` (читай по разделам; раздел «Что читать дальше» перечисляет справочники).
3. Прочитай справочники **только для найденного**:
   - движок — `references/engines/<движок>.md`;
   - звук — `references/audio/<формат>.md`;
   - тексты — `references/text/<система>.md`.

   CLI сам называет нужные файлы: `inventory/recon.json` → `refs.recon` и `refs.pack`, а в `report.md` — раздел «Что читать дальше» (пути вида `skills/babeldub-recon/references/audio/fmod.md`). Какие справочники соответствуют найденному:

   | Найдено (id в `recon.json`) | Файл |
   |---|---|
   | `unity` (backend `il2cpp` или `mono`) | `references/engines/unity.md` |
   | `unreal` | `references/engines/unreal.md` (тексты — `references/text/unreal-locres.md`) |
   | `godot` | `references/engines/godot.md`, `references/text/godot.md` |
   | `renpy` | `references/engines/renpy.md`, `references/text/renpy.md` |
   | `rpgmaker` | `references/engines/rpgmaker.md`, `references/text/rpgmaker.md` |
   | `gamemaker` | `references/engines/gamemaker.md` |
   | `source`, `source2` | `references/engines/source.md` |
   | `bethesda` | `references/engines/bethesda.md` |
   | `cryengine`, `xna`, `electron`, `love2d`, `telltale`, неопознанный движок | `references/engines/other.md` |
   | `fmod` | `references/audio/fmod.md` |
   | `wwise` | `references/audio/wwise.md` |
   | `criware` | `references/audio/criware.md` |
   | `bink` | `references/audio/video.md` |
   | отдельные аудиофайлы (адаптер `loose`) | `references/audio/loose.md` |
   | `unity-localization` | `references/text/unity-localization.md` |
   | `gettext` и прочие текстовые форматы | `references/text/generic.md` |
4. `babeldub extract` → `lines`, `by_kind{}`, `minutes`, `texts`, `locales[]`.
5. `babeldub link` → `linked`, `unlinked`, `with_official_target`.
6. Проверь результат выборками `babeldub query` (всегда с `--fields` и `--limit`):
   - нераспознанный звук: `--where kind=unknown --fields id,entry.key,audio.dur_s --limit 30`;
   - диалоги без текста: `--where kind=dialogue --fields id,entry.key,flags --limit 30` и ищи `no_text`.
7. Сводку верни оркестратору в формате роли scout (`babeldub-localize/references/roles/scout.md`).

Коды выхода: 0 — дальше; 2 — смотри `hint` (обычно не пройдена предыдущая стадия); 3 — есть предупреждения или неразобранные записи, разбери по справочникам; 4 — не хватает декодера или окружения, скилл `babeldub-doctor`; 1 — ошибка, смотри `logs/` проекта.

## Как опознать движок и middleware по файлам

| Признак в папке игры | Вывод | Справочник |
|---|---|---|
| `<Game>_Data/`, `UnityPlayer.dll`, `globalgamemanagers` | Unity | `engines/unity.md` |
| `GameAssembly.dll` + `<Game>_Data/il2cpp_data/Metadata/global-metadata.dat` | Unity IL2CPP | `engines/unity.md` |
| `<Game>_Data/Managed/Assembly-CSharp.dll` | Unity Mono | `engines/unity.md` |
| `StreamingAssets/aa/catalog.json` или `catalog.bin`, `settings.json` | Unity Addressables | `engines/unity.md` |
| бандлы `localization-string-tables-*`, `localization-assets-shared*` | Unity Localization | `text/unity-localization.md` |
| ассет `I2Languages` (обычно в `resources.assets`) | I2 Localization | `text/i2-localization.md` |
| `*.bank`, `*.strings.bank`, `fmodstudio.dll` / `fmod*.dll` | FMOD Studio | `audio/fmod.md` |
| `*.fsb` без `.bank` | FMOD Core (FSB) или FMOD Ex (FSB4, старше) | `audio/fmod.md` |
| `*.bnk`, `*.wem`, `*.pck`, `AkSoundEngine*.dll`, `GeneratedSoundBanks/` | Wwise | `audio/wwise.md` |
| `*.acb`, `*.awb`, `*.cpk`, `*.usm`, `cri_*.dll` | CRIWARE (ADX2, Sofdec) | `audio/criware.md`, `audio/video.md` |
| `<Game>/Content/Paks/*.pak` (+ `.utoc`/`.ucas`), `<Game>-Win64-Shipping.exe` | Unreal 4/5 (`.utoc` — IoStore) | `engines/unreal.md`, `text/unreal-locres.md` |
| `*.pck` с магией `GDPC` или exe с GDPC в хвосте | Godot | `engines/godot.md`, `text/godot.md` |
| `game/`, `renpy/`, `*.rpa`, `*.rpyc` | Ren'Py | `engines/renpy.md`, `text/renpy.md` |
| `www/js/rpg_core.js` или `js/rmmz_core.js`, `data/System.json` | RPG Maker MV или MZ | `engines/rpgmaker.md`, `text/rpgmaker.md` |
| `Game.rgss3a`, `RGSS3*.dll` (`.rgss2a` — VX, `.rgssad` — XP) | RPG Maker VX Ace / VX / XP | `engines/rpgmaker.md` |
| `data.win`, `audiogroup*.dat`, `options.ini` | GameMaker | `engines/gamemaker.md` |
| `*_dir.vpk`, `gameinfo.txt`, `bin/` | Source 1 | `engines/source.md` |
| `game/*/pak01_dir.vpk`, `gameinfo.gi`, ресурсы `*_c` | Source 2 | `engines/source.md` |
| `Data/*.bsa` или `*.ba2`, `*.esm`, `Data/Sound/Voice/` | Bethesda Creation Engine | `engines/bethesda.md` |
| отдельные `*.wav`, `*.ogg`, `*.mp3` в папках вроде `voice/`, `vo/`, `sound/` | свободные аудиофайлы | `audio/loose.md` |
| `*.bik`, `*.bk2`, `*.usm`, `*.mp4`, `*.webm` | видеоролики | `audio/video.md` |
| ничего из перечисленного | свой движок | `engines/other.md`, `text/generic.md` |

Middleware звука от движка не зависит: FMOD и Wwise встречаются и в Unity, и в Unreal, и в своих движках. Проверяй оба признака.

## Уровни поддержки в BabelDub v0.1

| Уровень | Что значит | Что делать |
|---|---|---|
| **A** | CLI сам извлекает, собирает и проверяет без проприетарного ПО | стадии идут автоматически |
| **B** | нужен инструмент, который ставит пользователь (WwiseConsole, UE Editor, Godot той же версии, `fsbankcl`), мод-лоадер или ручной шаг | честно описать в отчёте, что нужно; CLI v0.1 такой сборки не делает |
| **C** | в v0.1 только чтение и инструкции | описать путь из справочника; ничего не собирать руками |

Уровень конкретной игры показывает `babeldub scan` (поле `level` — лучший уровень среди голосовых ассетов, C — если голос не найден; уровень каждого ассета — в `voice_assets[]` и `inventory/assets.jsonl`). Адаптеры CLI v0.1:

| Адаптер | Что умеет | Уровень |
|---|---|---|
| `fmod` | банки FMOD Studio (`.bank`, FSB5): реплики, декодирование, сборка своего FSB5, проверка | A |
| `loose` | отдельные аудиофайлы (OGG, WAV, MP3, FLAC, Opus, M4A, AAC) в «голосовых» папках: реплики, декодирование, перекодирование замены в исходный формат | A |
| `unity-localization` | чтение таблиц Unity Localization для `link`; запись в игру — нет | A для чтения (C, если в бандлах нет typetree) |
| `wwise` | `.bnk`, `.pck`, `.wem`: реплики и декодирование для голосовых промптов; сборка — нет (нужен WwiseConsole) | B |

Остальные движки из таблицы выше CLI пока не разбирает: для них — только справочники.

## Как связываются реплика и субтитр

По частоте встречаемости:

1. **Общий ключ:** имя файла озвучки (имя сабсаунда FSB или Wwise, имя SoundWave) совпадает с ключом строки. В Low-Budget Repairs так связаны 1187 реплик.
2. **Ключ в метаданных события:** имя programmer instrument у FMOD, Dialogue Event у Wwise, `DialogueWave` у UE, токен caption у Source (это имя sound script).
3. **Данные игры:** диалоговые графы, ScriptableObject или JSON с полями `audio` и `text`. Их находят декомпиляцией или поиском строк; в v0.1 это ручная работа и материал для отчёта.
4. **Ren'Py:** идентификатор реплики и `config.auto_voice`.
5. **Запасной путь** — распознавание речи оригинала и нечёткое сопоставление. В CLI v0.1 его нет: реплики без текста остаются с оригинальным голосом.

`babeldub link` реализует пункт 1 (точный ключ → нормализованный ключ → имя файла). Остальное — гипотезы scout в отчёте.

## Что важно сохранить при замене звука

- Порядок, число и имена записей в контейнере: на индексы ссылаются таблицы движка.
- Число каналов (моно → моно). Частоту и кодек FMOD менять позволяет; для Wwise, CRI и Unity это не проверено.
- Громкость относительно оригинальной реплики, а не абсолютная норма.
- Длительность там, где звук играет синхронный инструмент или таймлайн (катсцены, видео) и где длина записана в метаданных (Bethesda `.lip`, фонемы Source). CLI помечает такие реплики `fixed_length`.

## Ловушки, о которых надо сообщить

- Steam «Проверить целостность файлов» и обновления возвращают оригиналы → `babeldub reapply`.
- Античит → отказ по умолчанию.
- Заранее посчитанный липсинк (`.lip`, фонемы Source, FaceFX) при замене звука не обновится.
- Шрифты без кириллицы — важно, если меняются тексты (в v0.1 тексты в игре не меняются).
- Шифрование: ключ FMOD-банка, AES в Unreal и Godot, ключи HCA у CRIWARE, шифрованные ресурсы RPG Maker.
- Распространять стоит патчер, а не собранные игровые контейнеры: в них чужие ассеты.

## Справочники

Читай файл только когда `scan` или листинг папки показал соответствующий признак.

- Движки: `references/engines/` — [unity](references/engines/unity.md), [unreal](references/engines/unreal.md), [godot](references/engines/godot.md), [renpy](references/engines/renpy.md), [rpgmaker](references/engines/rpgmaker.md), [gamemaker](references/engines/gamemaker.md), [source](references/engines/source.md), [bethesda](references/engines/bethesda.md), [other](references/engines/other.md).
- Звук: `references/audio/` — [fmod](references/audio/fmod.md), [wwise](references/audio/wwise.md), [criware](references/audio/criware.md), [loose](references/audio/loose.md), [video](references/audio/video.md).
- Тексты: `references/text/` — [unity-localization](references/text/unity-localization.md), [i2-localization](references/text/i2-localization.md), [unreal-locres](references/text/unreal-locres.md), [godot](references/text/godot.md), [renpy](references/text/renpy.md), [rpgmaker](references/text/rpgmaker.md), [generic](references/text/generic.md).

Пометки в справочниках: «проверено» — первоисточник или эксперимент на тестовой игре; «по гайдам сообщества» — вторичный источник; «не проверено» — гипотеза, перед действием её нужно подтвердить.
