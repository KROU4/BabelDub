# Glossary and style bible

Read this if you are the lexicon role or are fixing the glossary after `glossary check`.

## 1. Files and who writes them

| File | Who writes it | How it gets into the work |
|---|---|---|
| `translate/glossary.agent.jsonl` | the agent (lexicon) | `babeldub glossary apply` validates the lines and merges them into the canon |
| `translate/glossary.jsonl` | the CLI only | terms found in a batch's source text go into the batch header (`glossary`); `glossary check` verifies the translations |
| `translate/style_bible.md` | the agent (lexicon) | the CLI inserts the text into the header of every batch (`style`, up to 8000 characters) |

Write both files **before** `babeldub batch make`.

## 2. Format of `glossary.agent.jsonl`

UTF-8 without BOM, one object per line:

```json
{"term": "Zbyszek", "translation": "Збышек", "pronounce": "Збы́шек", "note": "protagonist, male"}
{"term": "Maciek", "translation": "Мачек", "note": "the hero's friend; informal «ты» with Zbyszek"}
{"term": "PZPM", "translation": "ПЗПМ", "pronounce": "пэ-зэ-пэ-эм"}
{"term": "Hmm", "translation": "Хм", "note": "interjection"}
```

| Field | Required | What to write |
|---|---|---|
| `term` | yes | the term as in the source text (case does not matter: the search is case-insensitive and allows endings of up to 3 letters) |
| `translation` | yes | how to write it in the target language; in the `official` and `adapt` modes, as in the official translation |
| `pronounce` | no | how to pronounce it when the reading is not obvious: stress with the U+0301 mark, letters of an acronym separated by hyphens, transliteration of Latin script |
| `note` | no | gender, role, context, "do not inflect", and so on |

A repeated `term` is a new version; the last one wins. `glossary apply` rejects a line without `term` or `translation` (exit code 2, list in `errors`).

## 3. What to include

1. **Character names**: every character in `main[]` of the casting is mandatory, taken from `name_tgt` in the casting sheet or from the official translation. Names must match the casting's `name_tgt`.
2. **Place names, organizations, items, game terms**: everything that occurs more than once or matters to the plot.
3. **Interjections and sounds** with their translation, so that all translators write them the same way.
4. **`pronounce`** for anything the synthesis model may read wrongly: Latin script, acronyms, foreign names, numbers with units, homographs.

Do not include ordinary words of the language "just in case": every term is checked by `glossary check`, and extra terms produce false violations.

## 4. Style bible

A short markdown file (1–3 screens) with these sections:

1. **Dubbing in the character's voice.** The translation keeps the emotional shape of the original: exclamations, questions, hesitations and repetitions, cut-offs, laughter, sighs, whispering. The voice prompt is the original line itself, so the text must "sit" on its intonation and pace.
2. **Characters.** One or two lines per main character: register, speech character, pet words, age.
3. **Formal and informal "you".** A matrix by character pairs, plus how the player is addressed; mark unclear cases "default".
4. **Swearing and rudeness**: the same level as the original, with example substitutions.
5. **Numbers, dates, units**: in words, in the correct grammatical case; for Russian, «ё» wherever it is pronounced.
6. **What not to translate**: names per the glossary, brand names per `pronounce`.

## 5. After `glossary check`

`violations[]` means: a glossary term is in the line's source, but its translation is not in `dub_text` (the check allows for endings). Options:

- the translation really is wrong → `babeldub batch make --ids <id1>,<id2>` → the translator fixes it → `batch submit`;
- the term means something else in this line (a homonym) → leave it and explain in the report;
- the glossary entry is poor (too general a term) → fix the line in `glossary.agent.jsonl` → `babeldub glossary apply` → `glossary check` again.
