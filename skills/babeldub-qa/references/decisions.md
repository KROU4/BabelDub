# Решения по браку: дерево, формат, примеры

Читай перед записью `qa/decisions.agent.jsonl`.

## 1. Формат `qa/decisions.agent.jsonl`

UTF-8 без BOM, по объекту на строку:

```json
{"id": "fmod:StreamingAssets/Dialogue_en-US.bank#fsb1:0042", "decision": "reref", "attempt": 2, "note": "sim 0.41 / sim_ref 0.62, sim_line low: shout with radio effect"}
```

| Поле | Обязательно | Значение |
|---|---|---|
| `id` | да | существующий `id` реплики |
| `decision` | да | `accept` \| `regen` \| `reref` \| `shorten` \| `retranslate` \| `keep_original` \| `skip` |
| `attempt` | нет | целое ≥ 1; по умолчанию — последняя обработанная попытка реплики |
| `note` | нет | строка: почему так решено (метрики, причина), для `shorten` — фактическое `ratio` и сколько срезать |

Поля `by` и `ts` не пиши: их ставит CLI. `babeldub decide apply` проверяет весь файл; если хоть одна строка невалидна (неизвестный `id`, решение не из списка, `attempt` не целое), не применяется ничего (код 2, список в `errors`). Одинаковые решения повторно не дублируются. Решение агента для попытки имеет приоритет над авто-решением CLI для той же попытки.

**Важно:** `accept` относится к последней обработанной попытке. В v0.1 `post` хранит файл только последней попытки, поэтому принять более раннюю нельзя: сборка пометит такую реплику `stale_attempt` и не возьмёт её. Если лучшей была ранняя попытка, а последняя хуже — `regen` (новая попытка) или `keep_original`.

## 2. Дерево решений

Проходи сверху вниз, первое подходящее правило решает:

1. **Нет речи по смыслу** (реплика — смех, кашель, вздох, музыка; `dub_text` — междометие, а модель выдала мусор) → `keep_original`.
2. **Реплику не нужно озвучивать** (дубль другой реплики, служебная запись, тестовая строка) → `skip`.
3. **Служебная причина** (`missing_file`, `qc_error`, `no_dub_text`) → не решай: исправь причину (`post`, повтор `qa`, перевод).
4. **Попытки исчерпаны** (`attempt` ≥ `tts.max_attempts`) и брак не по длине → `keep_original`, в `note` — что не помогло.
5. **Голос «уплыл»** (`sim` < `min_sim_ratio × sim_ref`):
   - `sim_line` низкий, `sim_char` нормальный, промпт был `line` → `reref`;
   - оба низкие, попытка первая → `regen`;
   - оба низкие повторно → `reref`;
   - массово у персонажа (больше 20 % в чанке) → не решать построчно, вернуть персонажа в `failing_chars`.
6. **Разборчивость** (`cer` > `max_cer`), голос в норме:
   - сравни `dub_text` и `asr_text`; разница только в имени, числе или междометии → `accept` с `note` или `retranslate` (числа словами, `pronounce`);
   - пропуски, повторы, чужие слова → `regen`;
   - второй раз подряд высокий `cer` на том же тексте → `retranslate` с `note`, что упростить.
7. **Длина** (`ratio` выше предела или `fixed_length` не влез):
   - `ratio` больше 2 → `regen` (сбой модели);
   - иначе → `shorten`, в `note` — `ratio` и сколько слогов срезать;
   - два сокращения уже были → `keep_original`, или `accept` с `note`, если звук в этой игре не привязан к таймингу (только для обычных диалогов, не для `fixed_length`).
8. **Пики, клиппинг, тишина** → `regen`.
9. **Всё в порогах** → `accept`.

## 3. Примеры

| Метрики | Решение | `note` |
|---|---|---|
| `cer` 0,05, `sim` 0,52, `sim_ref` 0,60 (0,87 × базы), `ratio` 1,1 | `accept` | — |
| `cer` 0,40; `dub_text` «Збышек, иди сюда»; `asr_text` «збышик иди сюда» | `accept` | «ASR misspells the name; the rest matches» |
| `cer` 0,60; `asr_text` содержит половину фразы и повтор | `regen` | «skipped words and repetition, attempt 1» |
| `sim` 0,30, `sim_line` 0,22, `sim_char` 0,38, `sim_ref` 0,58; реплика — крик по рации | `reref` | «line prompt carries the radio effect; use char» |
| `ratio` 1,22, `fixed_length` true | `shorten` | «ratio 1.22 on a cutscene: cut ~3 syllables of 14» |
| `ratio` 3,4, `cer` 0,7 | `regen` | «runaway generation» |
| попытка 3, `sim` всё ещё низкий | `keep_original` | «3 attempts, voice drifts on whisper» |
| `dub_text` «Ха-ха», модель выдала шум | `keep_original` | «laughter, original kept» |

## 4. После `decide apply`

- `regen`, `reref` → `babeldub synth --attempt-next` → `babeldub post` → `babeldub qa` (скилл `babeldub-voice`).
- `shorten`, `retranslate` → `babeldub batch make --from-decisions` → переводчик → `batch submit` → `babeldub synth` → `post` → `qa` (скилл `babeldub-translate`).
- `accept`, `keep_original`, `skip` → терминальные; `babeldub report` и сборка (скилл `babeldub-pack`).

## 5. Что вынести человеку

Не больше 30 реплик за раунд, только когда ни одно решение не подходит без суждения о смысле или вкусе: спорная атрибуция голоса, сюжетно важная реплика, которую не удаётся сократить, мат или чувствительный контент. Такие реплики получают `keep_original` (сборку они не блокируют) и попадают в раздел «Требует решения человека» итогового отчёта.
