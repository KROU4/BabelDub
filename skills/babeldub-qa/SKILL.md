---
name: babeldub-qa
description: "Quality control of a game dub made in the original characters' voices: measure intelligibility (ASR, CER), voice similarity of every dubbed line to the original hero, duration and peaks, then decide accept, regen, reref, shorten, retranslate, keep_original or skip and apply the decisions. Use after synthesis, when qa returns attention items or when asked about dub quality: проверь озвучку, голос не похож, брак синтеза, контроль качества, отчёт по персонажам."
license: MIT
compatibility: "Local agent with shell access; babeldub CLI 0.1.0 with the qc engine installed (babeldub setup --engines qc, faster-whisper and SpeechBrain ECAPA); NVIDIA GPU recommended, CPU works slower."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: контроль качества дубляжа

Скилл стадии `qa`. Критерий качества дубляжа BabelDub: реплика звучит **голосом своего оригинального героя**, разборчиво и в длину оригинала. QA проверяет это метриками, принимает решения по браку и доводит каждую реплику до терминального состояния: в сборку (`accept`) или с оригинальным голосом (`keep_original`, `skip`).

Ты звук не слышишь. Решения — **только по метрикам**; «на слух» выводов не делай. Скилл дополняет роль `qa` из скилла `babeldub-localize` (`references/roles/qa.md`).

## Команды

```text
babeldub qa                 метрики последней обработанной попытки каждой реплики + авто-решения
babeldub decide apply       слить qa/decisions.agent.jsonl в канон
babeldub report             qa/report.md: итоги по персонажам и список на внимание
```

Предусловие `qa` — стадия `post`. `qa` занимает GPU (`state/locks/gpu.lock`): не запускай его параллельно с `synth`. Повторный `qa` проверяет только новые попытки; ранее проверенные пропускаются.

## Что измеряется

| Метрика | Как | Порог по умолчанию |
|---|---|---|
| `cer` — разборчивость | распознавание речи дубля (faster-whisper large-v3-turbo) против `dub_text`, нормализованные тексты | `qa.max_cer` = 0,15 |
| `sim` — тот же ли голос | среднее из `sim_line` (сходство ECAPA с оригинальной репликой) и `sim_char` (с референсом персонажа) | `sim ≥ qa.min_sim_ratio × sim_ref`, `min_sim_ratio` = 0,75 |
| `sim_ref` — базовая линия персонажа | среднее попарное сходство его собственных чистых оригиналов | — |
| `ratio` — длина | речь дубля / речь оригинала (из `post`) | `qa.max_duration_ratio` = 1,35; для `fixed_length` 1 + `post.fixed_length_tolerance` = 1,03 |
| `peak_db`, клиппинг | пик и доля клиппированных отсчётов | пик не выше `post.true_peak_db` + 0,5 дБ |
| тишина | громкость речи | не тише −50 дБ |

Подробно о каждой метрике, её слабостях и о том, почему сходство считается относительно базовой линии, — [references/metrics.md](references/metrics.md).

## Ход работы

1. `babeldub qa`. Вывод: `checked`, `pass`, `fail`, `regen`, `shorten`, `keep_original`, `accept` (авто-решения этого прогона), `attention[]`, `pending`, `cer_median`, `sim_median`, `errors[]`, `next`.
2. **Код 0** — всё решено автоматически. Если `regen` > 0 — цикл голоса (`synth --attempt-next` → `post` → `qa`, скилл `babeldub-voice`); если `shorten` > 0 — перевод (скилл `babeldub-translate`, `batch make --from-decisions`).
3. **Код 3** — `attention[]`: реплики, где авто-решения не хватило или оно помечено для проверки. Разбери их (шаги 4–6).
4. **Посмотри метрики** только нужных реплик:
   `babeldub query --where verdict=fail --fields id,char,kind,fixed_length,qa.attempt,qa.cer,qa.sim,qa.sim_line,qa.sim_char,qa.sim_ref,qa.ratio,qa.peak_db,qa.reasons --limit 50`.
   Метрики QA в `query` лежат под префиксом `qa.`; для сравнения текста и распознанного добавь `translation.dub_text` и `qa.asr_text`.
5. **Системный брак.** Если у одного персонажа в чанке больше 20 % брака, не решай построчно: это проблема промпта, референса или атрибуции. Верни персонажа в `failing_chars` (оркестратору, ролям voice и caster).
6. **Решения** запиши в `qa/decisions.agent.jsonl` и выполни `babeldub decide apply`. Таблица решений — ниже, подробно — [references/decisions.md](references/decisions.md).
7. **Отчёт:** `babeldub report` → `qa/report.md`. Оттуда в итоговый отчёт пользователю идут таблица по персонажам и не больше 30 реплик на внимание.

## Решения

| Решение | Когда | Что будет дальше |
|---|---|---|
| `accept` | метрики в порогах, либо отклонение допустимо и объяснено в `note` | реплика пойдёт в сборку |
| `regen` | случайный брак (`cer`, пики, тишина, зацикливание) при нормальном промпте, попытки остались | новая попытка с другим seed |
| `reref` | голос «уплыл»: низкое `sim`, особенно `sim_line`, на крике, шёпоте, шуме, эффекте рации | новая попытка с другим промптом (`line` → `char`) |
| `shorten` | `ratio` выше предела, `fixed_length` не влез | переводчик сокращает текст, затем новый синтез |
| `retranslate` | `cer` высокий повторно при нормальном голосе: числа, латиница, сложные слова | переводчик правит текст, затем новый синтез |
| `keep_original` | нет речи, исчерпаны попытки, ничего не помогает | звучит оригинал; причина в `note` |
| `skip` | реплику озвучивать не нужно (дубль, служебная запись) | звучит оригинал; причина в `note` |

Авто-решения CLI: `pass` → `accept`; провал качества (`cer`, `sim`, `peak`, `empty`, `silent`) → `regen`, пока номер попытки меньше `tts.max_attempts` (3), затем `keep_original` с пометкой; длина → `shorten`, не больше двух раз, затем `keep_original`. `reref` и `retranslate` CLI сам не назначает — это решения агента.

## Пороги

Пороги по умолчанию — ориентир, а не истина: они не откалиброваны на голосах конкретной игры. Когда и как их менять (`babeldub set qa.max_cer=…`), почему абсолютный порог сходства не годится и что делать после смены порога — [references/thresholds.md](references/thresholds.md).

## Правила

1. Папка игры только для чтения.
2. Канон (`qa/qa.jsonl`, `qa/decisions.jsonl`, `qa/baseline.json`, `qa/report.md`) пишет CLI. Агент пишет только `qa/decisions.agent.jsonl`.
3. Не читать файлы проекта целиком: `query` с `--fields` и `--limit`, отчёт — по разделам.
4. Не обращаться к человеку: неясное — в `human_pending` ответа роли с причиной; сборку это не блокирует (реплика получает `keep_original`).
5. Человеку — не больше 30 реплик за раунд и только с конкретным вопросом.
6. Каждая реплика должна прийти в терминальное состояние или в очередь (`regen`, `reref`, `shorten`, `retranslate`).

## Справочники

- Что значит каждая метрика, где она ошибается, как читать `sim_line` и `sim_char` — [references/metrics.md](references/metrics.md). Читай при первом разборе брака.
- Пороги по умолчанию, калибровка на пилоте, когда их менять — [references/thresholds.md](references/thresholds.md). Читай, если брака подозрительно много или мало.
- Дерево решений, формат `qa/decisions.agent.jsonl`, типичные случаи с примерами — [references/decisions.md](references/decisions.md). Читай перед записью решений.
