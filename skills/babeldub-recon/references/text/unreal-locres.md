# Unreal: тексты `.locres`

Читай, если игра на Unreal Engine 4/5 (см. `engines/unreal.md`).

## Уровень в BabelDub v0.1

**C** — CLI v0.1 `.locres` не читает. План — v0.3, уровень B (если пак зашифрован, нужен ключ AES).

## Где лежит

- `Content/Localization/<Target>/<culture>/<Target>.locres` (+ `.locmeta`) внутри `.pak` или IoStore (`.utoc`/`.ucas`).
- Ключи — namespace + key + hash.
- Строки субтитров могут лежать и в `SoundWave.Subtitles` (тайминги внутри ассета звука).

## Чем извлечь

| Инструмент | Назначение |
|---|---|
| FModel / CUE4Parse | просмотр паков, экспорт `.locres` → JSON, AES-ключи |
| UnrealLocres | `.locres` ↔ CSV |
| UnrealLocresEditor | редактор `.locres` |
| LocRes-Builder | сборка `.locres` |
| UEExtractor | извлечение текста из pak/ucas |

UE4LocalizationsTool архивирован.

## Нативный путь

Заменить `.locres` существующей культуры и положить его в пак мода с суффиксом `_P` (`repak pack` для `.pak`, `retoc to-zen` для IoStore). Новая культура должна быть разрешена игрой (список культур или настройки).

## Связь с озвучкой

`DialogueWave`, имена SoundWave или события Wwise/FMOD рядом с ключом строки в данных диалогов. Прямого общего ключа может не быть: ищи по выборке.

## Подводные камни

- AES-шифрование паков, подписи `.sig`.
- Шрифты без кириллицы.
- Тайминги субтитров в SoundWave при изменении длины реплик.

## Ссылки

- FModel — https://github.com/4sval/FModel ; CUE4Parse — https://github.com/FabianFG/CUE4Parse
- UnrealLocres — https://github.com/akintos/UnrealLocres ; UnrealLocresEditor — https://github.com/Snoozeds/UnrealLocresEditor
- LocRes-Builder — https://github.com/matyalatte/LocRes-Builder ; UEExtractor — https://github.com/SolicenTEAM/UEExtractor
- repak — https://github.com/trumank/repak ; retoc — https://github.com/trumank/retoc
