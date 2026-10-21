# Unity Localization (StringTable в Addressables)

Читай, если в `StreamingAssets/aa/<платформа>/` есть бандлы `localization-string-tables-*`, `localization-assets-shared*`, `localization-locales*`.

## Уровень в BabelDub v0.1

- **Чтение — да (A):** адаптер `unity-localization` (через UnityPy) читает строки всех языков в `inventory/texts.jsonl`, а `babeldub link` связывает их с репликами и находит официальный перевод (`tgt_official`). Поля MonoBehaviour берутся из typetree бандлов (в Addressables-бандлах он обычно есть даже у IL2CPP). Если typetree нет, CLI пробует необязательный пакет TypeTreeGeneratorAPI; без него ассет помечается уровнем C с предупреждением.
- **Запись в игру — нет** (план v0.2): субтитры в v0.1 не меняются. Round-trip правки StringTable через UnityPy проверен на тестовой игре, поведение в игре не проверено.

## Как устроено (по исходникам `com.unity.localization` 1.5.13)

- `LocalizationTable`: `m_LocaleId`, `m_SharedData` (ссылка на SharedTableData), `m_Metadata`, `m_TableData: List<TableEntryData>`.
- `TableEntryData`: `long m_Id`, `string m_Localized`, `m_Metadata`.
- `SharedTableData`: `m_TableCollectionName`, `m_TableCollectionNameGuidString`, `m_Entries: List<{long m_Id, string m_Key, m_Metadata}>`, `m_KeyGenerator`.
- Связь: `SharedTableData.m_Entries` (`m_Id` → `m_Key`) + `StringTable.m_TableData` (`m_Id` → `m_Localized`) нужной локали (`m_LocaleId.m_Code`, например `ru-RU`).
- Бандлы string tables бывают по языку; имя может содержать **самоназвание языка в нижнем регистре**, а не код локали (в Low-Budget Repairs: `localization-string-tables-русский_assets_all.bundle`, `…-polski_…`, `…-english_…`). Ключи — в общем `localization-assets-shared_assets_all.bundle`.
- **Asset tables** (`localization-asset-tables-*`) локализуют ассеты (спрайты, иногда AudioClip или ссылки).

## Связь с озвучкой

Частый случай — ключ строки = имя аудиофайла или сабсаунда. В Low-Budget Repairs ключ строки Unity Localization = имя сабсаунда FSB = ключ аудиотаблицы FMOD (1187 совпадений из 1252 диалоговых ключей). Хвосты: дубли `_vN`/`_BEST`, монтажные куски, ключи без аудио.

## Smart Strings и разметка

- Unity Smart Strings: `{0:plural:…}`, `{0:choose(1|2|3):…}`, вложенные области. Для русского у `plural` нужны три формы.
- TMP/uGUI-теги (`<color=…>`, `<b>`, `<i>`, `<size=…>`, `<sprite …>`, `<link=…>`) и плейсхолдеры игры (в Low-Budget Repairs: `{ColorStart}{CharacterName}:{ColorEnd}`).
- Всё это в субтитре сохраняется байт в байт; в произносимый текст (`dub_text`) не попадает.

## Если понадобится запись (вне v0.1)

1. Найти StringTable нужной локали по `m_LocaleId.m_Code`, заменить `m_Localized`.
2. UnityPy: `obj.patch(tree)` → `env.file.save(packer="lz4")`.
3. Обнулить CRC изменённого бандла в каталоге Addressables (`AddressablesTools patchcrc`, см. `engines/unity.md`).
4. Новый язык добавить трудно (ассет `Locale`, таблицы на каждую коллекцию, записи каталога). Практичнее заменить содержимое существующей ненужной локали (не проверено).
5. Шрифты TextMeshPro без кириллицы потребуют замены Font Asset или fallback.

## Ссылки

- Unity Localization 1.5.13, Tables — https://github.com/needle-mirror/com.unity.localization/tree/1.5.13/Runtime/Tables
- Smart Strings — https://docs.unity3d.com/Packages/com.unity.localization@1.5/manual/Smart/SmartStrings.html
- UnityPy — https://github.com/K0lb3/UnityPy
- AddressablesTools — https://github.com/nesrak1/AddressablesTools
