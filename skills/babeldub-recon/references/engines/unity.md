# Unity

Читай, если `scan` показал Unity или в папке игры есть `<Game>_Data/`, `UnityPlayer.dll`.

## Уровень в BabelDub v0.1

| Что | Уровень | Комментарий |
|---|---|---|
| голос в банках FMOD Studio | **A** | адаптер `fmod`, см. `audio/fmod.md` |
| голос в Wwise | см. `audio/wwise.md` | кодирование `.wem` только в Wwise Authoring |
| тексты Unity Localization | чтение | адаптер `unity-localization` читает таблицы для `link`; запись в игру — в следующих версиях |
| I2 Localization | C | см. `text/i2-localization.md` |
| встроенный звук Unity (AudioClip) | C | открытого импортёра нет, нужен эксперимент (план — v0.3) |

## Как опознать

- `<Game>_Data/`, `UnityPlayer.dll`, `globalgamemanagers` — Unity. Версия Unity — в заголовке `globalgamemanagers` или через UnityPy.
- `GameAssembly.dll` + `<Game>_Data/il2cpp_data/Metadata/global-metadata.dat` — IL2CPP.
- `<Game>_Data/Managed/Assembly-CSharp.dll` — Mono (легко декомпилируется, мод-лоадер BepInEx 5).
- `StreamingAssets/aa/catalog.json` или `catalog.bin`, `settings.json` — Addressables; версия в `settings.json` → `m_AddressablesVersion`.

## Где голос

1. **Звуковое middleware** (частый случай для игр с озвучкой): FMOD (`*.bank` в `StreamingAssets`, `fmodstudio.dll` в `Plugins/x86_64`) или Wwise (`*.bnk`, `*.wem`, `*.pck`). Тогда голос разбирает соответствующий справочник `audio/`.
2. **Встроенный звук Unity — AudioClip.** Поля `m_LoadType`, `m_Channels`, `m_Frequency`, `m_BitsPerSample`, `m_Length`, `m_Resource{m_Source, m_Offset, m_Size}`, `m_CompressionFormat` (PCM, Vorbis, ADPCM, MP3, VAG, HEVAG, XMA, AAC, GCADPCM, ATRAC9 — значения 0…9). Сами данные — FSB5, лежат в `.resource` (`resources.resource`, `sharedassetsN.resource`) или в бандле.
3. **Addressables и AssetBundles:** UnityFS-бандлы (LZ4/LZMA), внутри `CAB-…` (SerializedFile) и `CAB-….resS/.resource`.

Остальные файлы `<Game>_Data/`: `globalgamemanagers(.assets)`, `level*` (сцены), `sharedassetsN.assets`, `resources.assets`, `.resS` (текстуры и меши), `StreamingAssets/` (сырые файлы).

## Где текст

- Unity Localization — `text/unity-localization.md`.
- I2 Localization — `text/i2-localization.md`.
- TextAsset, MonoBehaviour с таблицами диалогов (Yarn, Ink, Pixel Crushers и т. п.) — `text/generic.md`.

## Чем извлечь

| Инструмент | Назначение | Состояние на 2026-10 |
|---|---|---|
| UnityPy | Python: чтение и запись бандлов и SerializedFile, правка typetree; AudioClip — **только экспорт** | 1.25.4, MIT; используется CLI |
| AssetRipper | экспорт проекта и ассетов (аудио в wav/ogg) | 2.0.0, GPL-3.0 |
| UABEA / UABEANext | GUI: редактирование ассетов, дампы; AudioClip **только экспорт** | UABEA v8, UABEANext nightly |
| AssetsTools.NET | C#-библиотека чтения и записи (основа UABEA) | v24, MIT |
| AddressablesTools | чтение и запись `catalog.json`/`.bin`; `patchcrc` обнуляет `m_Crc` | v3; лицензия в GitHub не указана |
| AssetStudioMod | просмотр и экспорт | v0.19.0 |
| Il2CppDumper, Cpp2IL | восстановление типов и логики IL2CPP (поиск, как игра выбирает язык и банк) | Il2CppDumper v6.7.46, Cpp2IL активен |

## Нативный путь пересборки

- **Голос в FMOD или Wwise** — по справочнику middleware.
- **Тексты:** UnityPy `obj.patch(tree)` → `env.file.save(packer="lz4")` (round-trip проверен на тестовой игре; поведение в игре не проверено). После изменения бандла нужно обнулить или пересчитать `m_Crc` в каталоге Addressables (см. ниже).
- **AudioClip** (не проверено, вне v0.1):
  1. свой PCM-FSB5 в ресурс + правка полей AudioClip через UnityPy (`m_Resource.m_Offset/m_Size`, `m_CompressionFormat=0`, `m_BitsPerSample=16`, `m_Frequency`, `m_Channels`, `m_Length`) — гипотеза: звук Unity построен на FMOD Core, но на игре это не проверялось;
  2. Unity Editor той же версии (бесплатно): импорт WAV, сборка AssetBundle и пересадка FSB и полей AudioClip — приём моддеров (по гайдам сообщества);
  3. рантайм: BepInEx или MelonLoader + Harmony-патч `AudioSource.Play` и загрузка OGG.

### CRC в Addressables

- Addressables 1.22.3 грузит локальный бандл через `AssetBundle.LoadFromFileAsync(path, crc)`; при ненулевом CRC несовпадение даёт ошибку загрузки.
- CRC хранится в `catalog.json` (1.x): поле `m_ExtraDataString` — base64 с `AssetBundleRequestOptions` (`m_Hash`, `m_Crc`, `m_BundleName`, `m_BundleSize` …).
- Обход: `AddressablesTools` `Example patchcrc catalog.json` ставит `m_Crc=0` у всех записей; v3 сама подменяет `catalog.json`, оригинал сохраняется как `catalog.json.old`. Точечное обнуление только у изменённых бандлов возможно по формату, но не проверено.
- Addressables 2.x (Unity 6) по умолчанию пишет бинарный `catalog.bin`; AddressablesTools его поддерживает (запись помечена «not been thoroughly tested»).

## Рантайм-путь (запасной)

Без пересборки ассетов: плагин мод-лоадера подменяет звук при загрузке или проигрывании.

- BepInEx 5.x для Mono (стабильный 5.4.23.5); BepInEx 6 для IL2CPP — только pre-release (`v6.0.0-pre.2`, 2024) и bleeding-edge сборки.
- MelonLoader — Mono и IL2CPP, v0.7.3.
- XUnity.AutoTranslator — перехват UI-текста; для IL2CPP поддержка неполная (по README).
- Минусы: мод-лоадер ставит пользователь, хрупкость к обновлениям игры, античит, реакция антивирусов.

## Подводные камни

- CRC Addressables при правке бандлов.
- Шрифты TextMeshPro (SDF-атласы) часто без кириллицы: перевод на новый алфавит требует замены Font Asset или fallback.
- Новый язык в Unity Localization добавить трудно (ассет `Locale`, таблицы, записи каталога) — практичнее заменить содержимое существующей локали (не проверено).
- Игра сама выбирает банк или таблицу языка в коде: какой файл подменять, иногда видно только после декомпиляции IL2CPP.

## Пример: Low-Budget Repairs

Unity 2022.3.62f3, IL2CPP; FMOD Studio 2.03.6 (`fmodstudio.dll` в `Plugins/x86_64`); Unity Localization через Addressables 1.22.3 (55 бандлов, у всех `m_Crc ≠ 0`). Голос — в `StreamingAssets/Dialogue_en-US.bank` и `Dialogue_pl-PL.bank`; игра грузит `Dialogue_<язык озвучки>.bank`, в меню жёстко только `en-US` и `pl-PL`. Поэтому в v0.1 подменяется содержимое `Dialogue_en-US.bank` с бэкапом.

## Ссылки

- UnityPy — https://github.com/K0lb3/UnityPy
- AssetRipper — https://github.com/AssetRipper/AssetRipper
- UABEA — https://github.com/nesrak1/UABEA ; UABEANext — https://github.com/nesrak1/UABEANext ; AssetsTools.NET — https://github.com/nesrak1/AssetsTools.NET
- AddressablesTools — https://github.com/nesrak1/AddressablesTools
- Addressables 1.22.3 `AssetBundleProvider.cs` — https://github.com/needle-mirror/com.unity.addressables/blob/1.22.3/Runtime/ResourceManager/ResourceProviders/AssetBundleProvider.cs
- `AssetBundle.LoadFromFileAsync` — https://docs.unity3d.com/ScriptReference/AssetBundle.LoadFromFileAsync.html
- AssetStudioMod — https://github.com/aelurum/AssetStudioMod
- Il2CppDumper — https://github.com/Perfare/Il2CppDumper ; Cpp2IL — https://github.com/SamboyCoding/Cpp2IL
- BepInEx — https://github.com/BepInEx/BepInEx ; MelonLoader — https://github.com/LavaGang/MelonLoader ; XUnity.AutoTranslator — https://github.com/bbepis/XUnity.AutoTranslator
