# I2 Localization (Unity)

Читай, если в Unity-игре есть ассет `I2Languages` (обычно в `resources.assets`).

## Уровень в BabelDub v0.1

**C** — адаптера I2 в v0.1 нет; только инструкции. Тексты из I2 не попадут в `inventory/texts.jsonl`, и `babeldub link` их не свяжет. План — v0.3, уровень B. Сведения ниже — по дампу сообщества (вторичный источник).

## Как устроено

- Ассет `I2Languages` (`LanguageSourceAsset`):
  - `mSource.mTerms: List<TermData{string Term; int TermType; string[] Languages; byte[] Flags; string[] Languages_Touch}>`;
  - `mSource.mLanguages: List<LanguageData{string Name; string Code; byte Flags}>`.
- Строка термина на языке N — элемент `Languages[N]`; язык N описан в `mLanguages[N]`.
- **Новый язык** = элемент в `mLanguages` плюс строка в `Languages[]` каждого термина.
- I2 умеет подтягивать таблицы из Google Sheets или локального CSV, если разработчик это включил (не проверено).

## Чем извлечь

- UABEA (дамп ассета), UnityPy.
- i2loc-manager — экспорт и импорт CSV/TSV.
- Для IL2CPP генератор typetree иногда ошибается — тогда нужен ручной разбор.

## Связь с озвучкой

Имя термина (`Term`) часто совпадает с именем аудио или ключом события; проверяй выборкой. Если голос через FMOD или Wwise — связь по ключу аудиотаблицы или имени события.

## Подводные камни

- Шрифты TextMeshPro без кириллицы.
- Порядок языков в `mLanguages` и в `Languages[]` должен совпадать.

## Ссылки

- I2 Localization — http://inter-illusion.com/tools/i2-localization/
- i2loc-manager — https://github.com/Veydzher/i2loc-manager
- Разбор I2 (дамп сообщества) — https://github.com/truix/travellers-rest-planner/blob/main/scripts/dump_i2l.py
