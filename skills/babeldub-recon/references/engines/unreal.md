# Unreal Engine 4/5

Читай, если в папке игры есть `<Game>/Content/Paks/*.pak` (и, возможно, `.utoc`/`.ucas`) и `<Game>-Win64-Shipping.exe`.

## Уровень в BabelDub v0.1

| Что | Уровень | Комментарий |
|---|---|---|
| голос в банках FMOD внутри UE (`Content/FMOD/Desktop/*.bank`) | см. `audio/fmod.md` | формат банка от движка не зависит; на играх не-Unity не проверялось |
| голос в Wwise | см. `audio/wwise.md` | |
| тексты `.locres` | C в v0.1 (план — B, v0.3) | см. `text/unreal-locres.md` |
| SoundWave (родной звук UE) | C | нужен cook в UE Editor той же версии |

## Как опознать

- `Content/Paks/*.pak` — паки (версии v1–v11).
- `.utoc` + `.ucas` рядом с «пустым» `.pak` — **IoStore** (UE5 и поздний UE4).
- Шифрование AES-256 (ключ в exe), подписи `.sig`.
- `Content/FMOD/Desktop/*.bank` — FMOD; `.bnk`/`.wem` или uasset-медиа — Wwise.

## Где голос

- `SoundWave` (`.uasset/.uexp`, данные в `.ubulk` или внутри; форматы OGG, ADPCM, Opus, Bink Audio в UE5 — детали по версиям не проверены).
- `DialogueWave` + `DialogueVoice`.
- Локализованные ассеты в `Content/L10N/<culture>/...` — родной механизм голоса по культурам (по гайдам сообщества).
- Часто middleware: Wwise или FMOD.

## Где текст

`Content/Localization/<Target>/<culture>/<Target>.locres` (+ `.locmeta`); ключи — namespace + key + hash. Строки субтитров могут лежать и в `SoundWave.Subtitles`. Подробно — `text/unreal-locres.md`.

## Чем извлечь

| Инструмент | Назначение | Состояние на 2026-10 |
|---|---|---|
| FModel / CUE4Parse | просмотр и экспорт (`.locres` → JSON, звук), работа с AES-ключами | активны |
| repak | `.pak` unpack/pack/list, AES; mount point по умолчанию `../../../`; чтение и запись версий 2–5, 7, 8A/8B, 9, 11 (UE 4.0–5.3), версии 1, 6, 10 не проверены авторами | v0.2.3, Apache-2.0 |
| retoc | IoStore: `unpack`, `unpack-raw`, `pack-raw`, `list`, `verify`; сборка мода — `to-zen` (legacy → IoStore), обратно — `to-legacy`; отдельной команды `pack` нет | v0.1.5, MIT |
| UnrealLocres, UnrealLocresEditor, LocRes-Builder, UEExtractor | `.locres` ↔ CSV, редактор, извлечение текста | активны (UE4LocalizationsTool архивирован) |
| RE-UE4SS | рантайм: Lua/C++ моды, хуки функций | v3.0.1 + experimental |

## Нативный путь пересборки

- **Пак мода:** папка `ModName_P/<Game>/Content/...` → `repak pack` → файл `ModName_P.pak` в `Content/Paks/` или `~mods/`. **Суффикс `_P` обязателен.** Для UE ≤ 4.27 с `.sig` копируют любой `.sig` под именем мода (по гайдам сообщества).
- **IoStore-игры:** мод тоже в IoStore (`.pak` + `.utoc` + `.ucas`); сборка `retoc to-zen` или UnrealReZen (по гайдам сообщества).
- **Звук:** cook SoundWave в UE Editor **той же версии** (редактор бесплатен) с тем же путём пакета. Звук через Wwise или FMOD собирается по справочникам middleware.
- **Текст:** заменить `.locres` существующей культуры в `_P`-паке. Новая культура должна быть разрешена игрой.

## Подводные камни

- AES-шифрование паков (ключ извлекают из exe), подписи `.sig`.
- EasyAntiCheat во многих UE-играх → по умолчанию не модифицировать.
- FaceFX и кривые липсинка, тайминги субтитров в SoundWave.
- Совпадение версии cook с игрой.

## Ссылки

- FModel — https://github.com/4sval/FModel ; CUE4Parse — https://github.com/FabianFG/CUE4Parse
- repak — https://github.com/trumank/repak ; retoc — https://github.com/trumank/retoc
- UnrealLocres — https://github.com/akintos/UnrealLocres ; UnrealLocresEditor — https://github.com/Snoozeds/UnrealLocresEditor ; LocRes-Builder — https://github.com/matyalatte/LocRes-Builder ; UEExtractor — https://github.com/SolicenTEAM/UEExtractor
- RE-UE4SS — https://github.com/UE4SS-RE/RE-UE4SS
- Гайды Dmgvol (`BasicModding/UnrealPak.md`, `BasicModding/IoStorePacking.md`) — https://github.com/Dmgvol/UE_Modding
- Форум Epic: Loading Content and Pak Files at Runtime — https://forums.unrealengine.com/t/knowledge-base-primer-loading-content-and-pak-files-at-runtime/536669
