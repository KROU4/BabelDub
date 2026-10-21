# Wwise (Audiokinetic): `.bnk`, `.wem`, `.pck`

Читай, если в папке игры есть `*.bnk`, `*.wem`, `*.pck`, `AkSoundEngine*.dll` или `GeneratedSoundBanks/`.

## Уровень в BabelDub v0.1

**B** — адаптер `wwise` находит и декодирует голос, но не собирает:

- реплики из `.bnk` (медиа в `DATA` по `DIDX`), из `.pck` (стримы, внешние источники, банки внутри пакета) и из отдельных `.wem`;
- имена — из `SoundbanksInfo.xml`/`.json`, если игра их поставляет, иначе ключом служит ID медиа;
- язык — из языковой карты пакета или папки языка (`English(US)` → `en-US`);
- декодирование: PCM напрямую, остальные кодеки — через `vgmstream-cli`.

Оригиналы годятся как голосовые промпты дубляжа, перевод и синтез идут как обычно. **Сборка невозможна без Wwise Authoring:** перекодировать WAV в `.wem` умеет только WwiseConsole той же версии Wwise, что у игры (нужен аккаунт Audiokinetic). `babeldub build` для таких ассетов возвращает код 2 «native rebuild is not automated (support level B)» — опиши пользователю путь ниже. План полной поддержки — v0.2.

## Форматы

- **`.bnk`** — чанки `BKHD` (версия, ID банка), `DIDX` (ID медиа, смещение, размер), `DATA` (встроенные `.wem` или prefetch-часть стримов), `HIRC` (события, действия, звуки, контейнеры, параметры кодека), `STID`, `STMG`/`INIT` и др.
- **`.wem`** — RIFF/RIFX `WAVE`, кодек в `fmt`: `0xFFFF` Vorbis (вариант Wwise с упакованными codebooks), `0x0002`/`0x8311` ADPCM/PTADPCM, `0x0001`/`0xFFFE` PCM, `0x3040`/`0x3041` Opus, `0x3039` OpusNX, а также XMA, AAC, ATRAC9, DSP.
- **`.pck`** (`AKPK`) — языковая карта, таблицы банков, стримов и внешних источников. У локализованных игр отдельные `.pck` или папки по языкам; язык выбирается в рантайме (`SetCurrentLanguage`).
- **ID** — 32-битные хэши FNV-1 от имён в нижнем регистре (ShortID). Имена восстанавливают списками `wwnames` (wwiser).

## Связь реплики и субтитра

Событие (`Play_VO_xxx`) → Sound → `sourceID` → `.wem`. Имя события или ID чаще всего лежит в данных диалогов игры рядом с ключом строки. Dialogue Events используют switch/state (персонаж, язык).

## Чем извлечь

| Инструмент | Назначение |
|---|---|
| vgmstream | декодирование `.wem/.bnk/.pck`, TXTP |
| wwiser | разбор `.bnk` в XML/дерево, имена, TXTP (событие → звуки) |
| bnkextr | извлечение `.wem` из `.bnk` |
| ww2ogg (+ revorb) | Wwise Vorbis → Ogg (старое) |
| Wwise-Unpacker | пакетная распаковка `pck/bnk` |
| BnkExtractor | библиотека извлечения `wem/bnk/pck` |

## Пересборка

1. **WAV → `.wem`** — только Wwise Authoring той же версии и с теми же Conversion Settings (кодек, качество, частота), что у игры. Пример рабочего вызова — в коде ZZAR: `WwiseConsole convert-external-source <project.wproj> --source-file list.wsources --output <dir>` (`.wsources` — XML с корнем `ExternalSourcesList`). По официальной документации Wwise синтаксис не проверен. Скачивание Wwise через Audiokinetic Launcher требует аккаунта.
2. **Подмена в `.bnk`/`.pck`:** wwiseutil («replacement .wem files are allowed to be larger or smaller… all metadata… updated», не обновлялся с 2018), BNKReplacer, PckTool (.NET), mhws-sound-tool (Rust), RingingBloom (игры Capcom).
3. Прецедент полного конвейера: hsr-ru-voiceover (Honkai: Star Rail) — английские `.wem` как референсы, WwiseConsole → `wem`, пересборка `pck`, установщик с откатом.

## Подводные камни

- Кодек замены должен совпадать с исходным: тип источника и кодека записан в `HIRC` (по гайдам сообщества). Можно ли заменить Vorbis-`.wem` на PCM-`.wem` без правки `HIRC`, не проверено.
- Стриминг с prefetch: начало звука лежит и в `DATA` банка, и в `.pck` — обновлять оба.
- Одинаковые ID в нескольких `.pck` (например `SoundBank_*` и `Streamed_*` у Zenless Zone Zero).
- Маркеры и cue в `.wem` иногда управляют субтитрами или липсинком (не проверено).
- Длительность: обычные Play-действия её не ограничивают, Music Segments и тайминги катсцен — ограничивают.
- Частоту и каналы при замене лучше сохранять: для Wwise допустимость изменения не проверена.

## Ссылки

- vgmstream — https://github.com/vgmstream/vgmstream (`src/meta/wwise.c`)
- wwiser — https://github.com/bnnm/wwiser ; bnkextr — https://github.com/eXpl0it3r/bnkextr ; ww2ogg — https://github.com/hcs64/ww2ogg
- Wwise-Unpacker — https://github.com/Vextil/Wwise-Unpacker ; BnkExtractor — https://github.com/AssetRipper/BnkExtractor
- wwiseutil — https://github.com/hpxro7/wwiseutil ; BNKReplacer — https://github.com/EtiTheSpirit/BNKReplacer ; PckTool — https://github.com/coconutbird/PckTool ; mhws-sound-tool — https://github.com/eigeen/mhws-sound-tool ; RingingBloom — https://github.com/Silvris/RingingBloom
- ZZAR — https://github.com/Pucas01/ZZAR
- hsr-ru-voiceover — https://github.com/VadimDeeptech/hsr-ru-voiceover
- Audiokinetic Q&A (convert external sources) — https://www.audiokinetic.com/qa/1539/how-do-convert-from-wav-wem-when-using-external-audio-source
