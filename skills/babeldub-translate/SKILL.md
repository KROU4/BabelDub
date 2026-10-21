---
name: babeldub-translate
description: "Translate or adapt game dialogue for dubbing in the original characters' voices: keep the emotional shape of every line so it fits the cloned voice and delivery, fit the syllable budget of the original duration, preserve placeholders, tags and Smart Strings, follow the glossary, gender and formal or informal address. Use for the translate stage, glossary, style bible and shorten requests. Example requests: 'translate the dialogue', 'localize this game', 'adapt the translation for voice-over', 'make a glossary', 'shorten this line', 'переведи игру', 'переведи реплики', 'адаптируй перевод под озвучку', 'глоссарий', 'ты или вы', 'сократи реплику', 'traduce los diálogos', 'traduire les dialogues'."
license: MIT
compatibility: "Any agent that can read and write files; babeldub CLI 0.1.0 for batch make, next and submit (install via the babeldub-doctor skill). Web chats without the CLI can translate an exported batch file by the same rules. No GPU needed."
metadata:
  babeldub-cli: "0.1.0"
---

# BabelDub: text for the dub

This skill covers the `translate` stage: the glossary, the style bible, translating or adapting batches of lines, and shortening lines after QA decisions. The text you write will be spoken in **the original character's voice**, cloned from that character's own line: the same timbre, emotion and pace. So the translation must sound natural in the target language, keep the emotional shape of the original, and fit its duration.

The skill complements the `lexicon` and `translator` roles of the `babeldub-localize` skill (`references/roles/`). The CLI never translates by itself: it decides the mode, cuts batches, validates what the agent wrote and merges it into the canon.

Talk to the user in the user's language. Write `dub_text`, `sub_text` and `variants` in the project's target language.

## Core rules of translating for dubbing

1. **Keep the emotional shape of the original.** An exclamation stays an exclamation, a question stays a question. Keep hesitations and repetitions ("I… I don't know"), cut-offs, laughter and interjections, the register and the level of profanity (no censoring and no amplifying). The voice prompt is the original line: a text "calmer" than the original fights its intonation.
2. **Length.** Aim for `budget_syll`, the number of syllables that fits into the duration of the original. For `fixed_length: true` (cutscenes, videos) the budget is mandatory. If the line does not fit, provide shorter `variants`. Details: [references/length-budget.md](references/length-budget.md).
3. **Two texts.** `dub_text` is what gets spoken: numbers in words, abbreviations expanded, difficult words per the glossary's `pronounce`, no stage directions. `sub_text` is the subtitle; write it only if it must differ from `dub_text` (in v0.1 in-game subtitles are not changed).
4. **Placeholders and tags** (`{0}`, `{name}`, `%s`, `<color=…>`, `[b]`, Unity Smart Strings) are carried over byte for byte **into `dub_text`, `sub_text` and every variant**: `batch submit` checks all three, and before synthesis the CLI strips the markup itself so it is not spoken. Details: [references/placeholders.md](references/placeholders.md).
5. **Names and terms come only from the glossary.** Do not invent your own: translate a missing term by meaning and list it in `notes` or in `new_terms` of the role's answer.
6. **Target-language grammar:** gender of the speaker and the addressee, formal or informal "you", agreement. For Russian: [references/russian.md](references/russian.md).

## Stage flow

### 1. Mode

```text
babeldub translate plan
```

Output: `mode`, `need_agent`, `lines`, `with_official`, `official_share`, `no_text`, `glossary`, `style_bible`. Modes:

| `mode` | Meaning | What to do |
|---|---|---|
| `official` | an official translation into the target language exists for ≥ 95% of lines; it is voiced as is | `babeldub translate use-official`; the agent translates the remaining lines without official text (`missing`) |
| `translate` | there is no official text, or too little of it; lines are translated from the source | glossary → batches |
| `adapt` | official text exists, but it is adapted for voice-over (length, pronounceability) | glossary → batches with `tgt_official` |

`auto` (default) chooses only between `official` and `translate`. The `adapt` mode is enabled explicitly: `babeldub set translation.mode=adapt` (or `init --mode adapt`). When to choose which: [references/adapt-vs-translate.md](references/adapt-vs-translate.md).

### 2. Glossary and style bible (lexicon role)

1. Scene samples: `babeldub sample --scene <scene> --n 30 --out translate/samples/<scene>.jsonl`; find scenes and speakers with `babeldub query --where kind=dialogue --fields scene,speaker_hint --limit 50`.
2. Glossary: `translate/glossary.agent.jsonl` (format: [references/glossary-format.md](references/glossary-format.md)), then `babeldub glossary apply`: the CLI validates the lines and merges them into the canonical `translate/glossary.jsonl`. Only the canonical glossary goes into batch headers.
3. Style bible: `translate/style_bible.md`, short and divided into sections (see section 4 of the same reference). The CLI inserts it into the header of every batch (up to 8000 characters).
4. Build the glossary and the style bible **before** `batch make`: batches cut earlier do not get them (`batch make --force` re-cuts batches that have not been submitted yet).

### 3. Batches

```text
babeldub batch make [--size 60]
babeldub batch next
babeldub batch submit <bid>
```

- `batch make` cuts the lines that still need a translation into batches by scene (output `batches[]`, `open`).
- `batch next` returns the first unsubmitted batch: `batch`, `state` (`todo`, `ready`, `error`), `in`, `out`, `remaining`. An empty `batch` means there are no batches left.
- The translator reads **only its own** `<bid>.in.jsonl`, in full, writes `<bid>.out.jsonl` next to it, and runs `batch submit <bid>`. Format of both files: [references/batch-format.md](references/batch-format.md).
- On hosts with subagents, run up to 4 translators in parallel, one batch each.

Reacting to `batch submit`:

| Code | Meaning | Action |
|---|---|---|
| 0 | the batch is merged | run `batch next` again |
| 2 | `errors[]`: missing `id`, empty `dub_text`, broken placeholders, invalid JSON; details in `errors_file` | fix your `.out.jsonl` and submit again, up to 3 iterations |
| 3 | `over_budget[]`: lines more than 15% over budget | provide shorter `variants` and submit again (1 iteration); if a line cannot get shorter without losing meaning, leave it: `post` does the rest of the fitting |

Resubmitting a changed `.out.jsonl` creates a new translation revision; an unchanged file is not merged twice.

### 4. Glossary check

```text
babeldub glossary check
```

Exit code 3 means `violations[]` (a glossary term is in the source, but its translation is not in `dub_text`); the full list is in `translate/glossary_violations.jsonl`. Fix: `babeldub batch make --ids <id1>,<id2>` (a new batch with these lines and their current text) → translator → `batch submit`. For a false positive (the term is used in a different sense), explain it in the report.

### 5. Shortening and retranslating after QA

The QA decisions `shorten` and `retranslate` send lines back to the translator:

```text
babeldub batch make --from-decisions
```

In the batch, such lines carry `task` (`shorten` or `retranslate`) and `current`, the current text. For `shorten`, cut according to the actual `ratio` in the QA note; for `retranslate`, remove what hinders pronunciation (numbers in digits, Latin script, abbreviations, long compound words). Then the usual `batch next` → `batch submit`; `babeldub synth` picks up the new text as a new attempt.

## What not to do

- Do not polish the translation into literary prose at the cost of length and intonation: this is a dub, not subtitles.
- Do not add bracketed stage directions ("(laughs)"): they are not meant to be spoken, and the model will read them aloud.
- Do not edit `translate/translations.jsonl`, `glossary.jsonl` or `.in.jsonl`: only the CLI writes the canon.
- Do not read other batches or whole project files; get data through `query` and `sample`.
- Do not change names already fixed by the glossary or the official translation.

## References

- Before the first batch and on placeholder errors: [references/placeholders.md](references/placeholders.md).
- On `over_budget[]`, `fixed_length`, `shorten`: [references/length-budget.md](references/length-budget.md).
- When translating into Russian (gender, formal or informal "you", the letter «ё», stress marks, numbers, shortening for dubbing): [references/russian.md](references/russian.md).
- Lexicon role, glossary and style bible format: [references/glossary-format.md](references/glossary-format.md).
- Format of `.in.jsonl` and `.out.jsonl`, fields and examples: [references/batch-format.md](references/batch-format.md).
- Choosing between `official`, `adapt` and `translate`, rules for adapting official text: [references/adapt-vs-translate.md](references/adapt-vs-translate.md).
