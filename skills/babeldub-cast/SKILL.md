---
name: babeldub-cast
description: "Cast a game for dubbing in the original characters' voices: group voice lines by speaker (metadata or voice clusters), label characters with name, gender and age, rank them into main, secondary and minor, pick clean voice references and choose who gets a new voice. Use for the cast stage, the characters question and voice-reference problems: кто говорит, персонажи игры, лист кастинга, голосовые референсы, кого озвучивать, главные герои."
license: MIT
compatibility: "Local agent with shell access; babeldub CLI 0.1.0 (install via the babeldub-doctor skill). Voice clustering and typical-timbre references need the qc engine (babeldub setup --engines qc) and preferably an NVIDIA GPU; without it speakers come from game metadata only."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: кастинг — кто говорит и чьим голосом

Скилл стадии `cast`. От кастинга зависит главное в дубляже: каждая реплика должна прозвучать **голосом своего оригинального героя**. Ошибка атрибуции даёт «чужой голос» или смену пола посреди сцены — это худший брак, его не исправит ни перевод, ни пост-обработка.

Скилл дополняет роль `caster` из скилла `babeldub-localize` (`references/roles/caster.md`); вопрос пользователю о персонажах задаёт оркестратор (`references/questions-defaults.md` там же).

## Что делает `babeldub cast`

```text
babeldub cast [--refs 1..3] [--min-ref-s 3] [--max-ref-s 12]
```

1. **Персонажи из метаданных.** Говорящий берётся из `speaker_hint` реплики: папка события FMOD (`event:/Dialogue/Act1/<Персонаж>/...`), токен имени в ключе, данные диалогов. Такие персонажи надёжны (`source: metadata`, уверенность 1,0).
2. **Кластеры голосов** для реплик без говорящего — если установлен воркер `qc` (эмбеддинги ECAPA, агломеративная кластеризация по косинусу, порог 0,35; короткие реплики присоединяются к ближайшему кластеру потом). Кластер получает имя `cluster_NN` и уверенность; кластеры с уверенностью ниже 0,6 считаются неуверенными.
3. **Группа `unknown`** — реплики без говорящего, которые не удалось сравнить (нет воркера `qc` или файл не прочитался). Это не один голос: она не ранжируется и не озвучивается как персонаж.
4. **Уровни** (`tier`): `main`, `secondary`, `minor` — по минутам речи (`references/tiers.md`).
5. **Выбор** по `cast.policy`: `main` (по умолчанию), `all` или `list`.
6. **Референсы** для выбранных персонажей: до `--refs` чистых оригинальных реплик 3–12 с с известным текстом (`cast/refs.jsonl`, копии в `cast/refs/<char_id>/`). Правила отбора — `references/ref-selection.md`.
7. **Лист кастинга** `cast/casting_sheet.md`: таблица персонажей, их референсы, предупреждения.

Вывод: `characters`, `selected`, `main[]`, `uncertain[]`, `no_ref[]`, `clusters`, `refs`, `warnings[]`, `next`. Код 3 — есть `uncertain[]` или `no_ref[]`.

Метки, которые уже поставил агент, переживают повторный `cast` (они переносятся по `char_id`).

## Шаги

1. **Запусти** `babeldub cast`. Предусловие — стадия `extract` (иначе код 2).
2. **Прочитай вывод**, а не файлы целиком. Лист кастинга читай по разделам.
3. **Если в `warnings` есть «qc worker unavailable»** и много реплик без говорящего (большая группа `unknown`), предложи в отчёте установить `qc` (`babeldub setup --engines qc`, скилл `babeldub-doctor`) и повторить `cast`: без кластеров эти реплики останутся с оригинальным голосом.
4. **Неуверенные персонажи** (`uncertain[]`, а также кластеры без имени): роль caster размечает их.
   - Смотрит до 20 реплик персонажа: `babeldub query --where char=<char_id> --fields id,entry.key,scene,text.src,audio.dur_s --limit 20`.
   - Пишет `cast/labels.agent.jsonl` — формат в `references/labels-format.md`.
   - `babeldub cast apply-labels` → вывод `updated`, `uncertain[]`. Код 2 — невалидная строка: исправить по `errors[]` (до 3 итераций).
5. **Проверка референсов главных персонажей** (роль caster): текст референса для тембра — нейтральный, без крика, шёпота, смеха и музыки. Плохой референс отметь в `notes` персонажа и в отчёте (`references/ref-selection.md`, раздел 4).
6. **Вопрос 2 пользователю** (задаёт оркестратор): все / главные / список. Покажи из листа кастинга таблицу: имя, пол, реплик, минут речи, уровень. По умолчанию — главные.
7. **Применяй ответ:**
   - `babeldub cast select --policy main` или `--policy all`;
   - `babeldub cast select --chars <char_id или имя>,<...>` для списка.
   Вывод: `selected[]`. Невыбранные персонажи останутся с оригинальным голосом.

## Как понять, что кастинг удался

- У каждого выбранного персонажа есть хотя бы один референс (`no_ref[]` пуст).
- `uncertain[]` пуст или каждый оставшийся неуверенный персонаж объяснён в `human_pending` (например, кластер смешал двух говорящих).
- Имена главных персонажей есть и на языке оригинала, и на целевом (`name_tgt`) — их возьмёт глоссарий (скилл `babeldub-translate`).
- Пол указан у всех главных: от него зависит грамматика перевода (род глаголов прошедшего времени, прилагательных) и проверка «смены пола» в QA.

## Ошибки и что с ними делать

| Признак | Причина | Действие |
|---|---|---|
| код 2 «no voiced lines with audio» | `extract` не дал реплик со звуком | вернуться к разведке (скилл `babeldub-recon`) |
| `no_ref[]` не пуст | у персонажа нет пригодных реплик даже после ослабления правил | в отчёт; реплики персонажа озвучатся по собственным оригиналам (`line`), если они пригодны |
| `warnings`: «no ideal reference, rules relaxed» | референс короче 3 с, длиннее 12 с или без текста | проверить его в листе кастинга; при плохом тембре — `notes` и отчёт |
| `warnings`: «cast.list names not found» | имя из списка пользователя не совпало | `babeldub cast select --chars` с `char_id` из листа кастинга |
| один «персонаж» говорит разными голосами | метаданные врут (папка события ≠ говорящий) или кластер смешал актёров | `notes` + `human_pending`; канон руками не делить |

## Правила

1. Папка игры только для чтения.
2. Канон (`cast/characters.jsonl`, `cast/assign.jsonl`, `cast/refs.jsonl`, `cast/casting_sheet.md`) пишет только CLI. Агент пишет только `cast/labels.agent.jsonl`.
3. Не читать файлы проекта целиком: `query` с `--fields` и `--limit` (до 20 реплик на персонажа), лист кастинга — по разделам.
4. Звук ты не слышишь: пол и возраст определяй по тексту (обращения, грамматика официального перевода, ключи), а не «на слух».
5. Не подменять референс вручную копированием файлов: выбор референсов — работа `cast`.

## Справочники

- Как CLI выбирает референсы, что делает референс хорошим, почему эмоция берётся из самой реплики — [references/ref-selection.md](references/ref-selection.md). Читай при проверке референсов и при `no_ref[]`.
- Уровни персонажей, `cast.policy`, `main_share`, что сказать пользователю о выборе — [references/tiers.md](references/tiers.md). Читай перед вопросом 2.
- Формат `cast/labels.agent.jsonl`, правила имён, пола, возраста и заметок — [references/labels-format.md](references/labels-format.md). Читай перед разметкой.
