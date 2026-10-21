# Bethesda Creation Engine (Oblivion, Fallout 3/NV/4/76, Skyrim, Starfield)

Читай, если в папке игры есть `Data/*.bsa` или `*.ba2`, `*.esm`, `Data/Sound/Voice/`.

## Уровень в BabelDub v0.1

**C** — CLI v0.1 эти форматы не разбирает. План — позже, уровень B/C: нужен `FonixData.cdf` из Creation Kit для липсинка.

## Как опознать

- Архивы BSA (Oblivion, FO3/NV, Skyrim LE/SSE) и BA2 (FO4, FO76, Starfield).
- Loose-файлы в `Data/` перекрывают архивы (по гайдам сообщества).

## Где голос

- `Data/Sound/Voice/<Plugin>/<VoiceType>/<topic>_<FormID>_<n>.fuz`.
- FUZ — контейнер `FUZE` с `.lip` (липсинк) и `.xwm` (xWMA) (по гайдам сообщества; побайтовая раскладка заголовка не проверена).
- Несколько NPC делят один voice type: персонаж ≠ папка голоса.

## Где текст

Локализованные плагины: `Data/Strings/<plugin>_<язык>.STRINGS`, `.DLSTRINGS`, `.ILSTRINGS`. Стандартный редактор сообщества — xTranslator.

## Чем извлечь

BSArch (xEdit), Bethesda Archive Extractor, Unfuzer, Yakitori Audio Converter (по гайдам сообщества).

## Нативный путь пересборки

- Loose-файлы в `Data/Sound/Voice/...` с теми же именами.
- `.xwm` — `xWMAEncode` из DirectX SDK (по гайдам сообщества).
- `.lip` — Creation Kit или FaceFXWrapper («generate native LIP files … without … Creation Kit»; типы Skyrim и Fallout4, язык `USEnglish`; нужен `FonixData.cdf` из Creation Kit).
- Насколько хорош липсинк для русской речи при английской фонемной модели, не проверено.
- Пример конвейера сообщества: Mantella генерирует нативные `.lip`/`.fuz` через FaceFX/LipGen; xVASynth — ИИ-голоса для модов.

## Подводные камни

- Заранее посчитанный липсинк: без новых `.lip` губы не совпадут с речью.
- Voice type общий для многих NPC.
- Длительность реплики записана в связанных файлах.

## Ссылки

- FaceFXWrapper — https://github.com/Nukem9/FaceFXWrapper
- xVA-Synth — https://github.com/DanRuta/xVA-Synth
- Mantella — https://github.com/art-from-the-machine/Mantella
- CK wiki: пакетная генерация голоса — https://ck.uesp.net/wiki/How_to_generate_voice_files_by_batch
