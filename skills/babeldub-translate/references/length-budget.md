# Length budget: syllables, variants, shortening

Read this on `over_budget[]` from `batch submit`, for `fixed_length` lines and for `shorten` tasks.

## 1. Where `budget_syll` comes from

`budget_syll` = the original's **speech span** (`speech_s`: from the first to the last spoken sound, the pauses between included, without the file's leading and trailing silence; the dubs' pace is measured over their span the same way) × the character's pace (`rate_syl_s`), rounded. Both are in every batch row. `rate_syl_s` is measured: the median syllables per second of the character's accepted dubs. Until the character has 3 accepted dubs it is the target language's average for dubbed speech, in syllables per second:

| Language | Syllables/s | | Language | Syllables/s |
|---|---|---|---|---|
| ru, uk, be | 5.5 | | fr, it | 6.8 |
| pl, cs, en, nl, sv | 5.8 | | es | 7.2 |
| de | 5.6 | | pt, tr | 6.6 |
| zh | 5.0 | | ja | 7.4 |
| ko | 6.4 | | other | 6.0 |

How the CLI counts syllables in a text (a heuristic, not phonetics):

- Russian, Ukrainian, Polish, Czech and other languages with "transparent" spelling: **by the number of vowel letters** (in Russian: `аеёиоуыэюя`); a Latin-script word inside Cyrillic text uses the English heuristic;
- German, Spanish, French, Italian: by vowel groups;
- English: vowel groups with a correction for silent `e`;
- Chinese, Japanese, Korean: by characters, Hangul syllables and kana;
- markup is dropped; digits become words if `num2words` is installed in the CLI environment, otherwise they count as roughly 1.5 syllables per digit (so write numbers in `dub_text` as words from the start).

The `fit` status in `translate/translations.jsonl`: `over` means more than 115% of the budget; `under` means less than 50%; otherwise `ok`.

The budget is a guide, not a law: the real duration depends on the voice and the delivery. The final length is checked by `post` and QA (`ratio`). `post` never slows a dub down: a short line is fine, a long one is compressed (pauses first, then at most 15% faster).

## 1a. Pauses: `src_segmented`

When the original has pauses of 250 ms or more, the batch row has `src_segmented`: `src` with ` / ` where the speaker pauses (placed by the time share, so it can be off by a word). Keep a pause at each mark in `dub_text` with punctuation: a comma, a dash, an ellipsis or a sentence break. Never write the slash itself. A natural pause in the right place makes the dub's rhythm match the original and lets cutscene lines be placed segment by segment.

Also for the voice: in Russian write ё wherever it belongs (все / всё), and avoid stress homographs (за́мок / замо́к): pick an unambiguous word, because the TTS may stress them wrongly.

## 2. How to stay within the budget

1. **Meaning and intonation first, length second.** Start with a natural translation, then tighten it.
2. **Tighten the way dubbing does** (for Russian: `references/russian.md`, section 7): remove redundant pronouns and connectives, take a shorter synonym, swap a subordinate clause for a participial phrase or vice versa, whichever is shorter, compress the form of address, keep the key word and the emotion.
3. **Do not sacrifice** meaning, glossary names, the emotional shape (exclamation, question, cut-off), or a joke that moves the scene.
4. **Variants.** If the full translation does not fit, give up to two shorter ones in `variants`: first a compact one, then the tightest. `batch submit` takes the first variant that fits (`picked_variant`) and keeps the agent's original text in `agent_text`.
5. **Shorter than the budget is fine** if it sounds natural. `under` is a hint to check that no meaning was lost, not an error. Do not pad a line with filler words to reach the length.
6. **Delivery tags do not change the budget.** The instruction made of the line's delivery tags is never spoken and never counted; `quick` does not buy room for a longer text, and `slow` makes the dub longer. On a tight or `fixed_length` line do not add `slow` (or `sad`, which is usually slower) unless the scene really needs it; the pace measured on the original is already in `delivery_hint` (`references/batch-format.md`, section 2a).

## 3. `fixed_length: true`

Cutscenes, videos and timelines: the audio must fit into the duration of the original (3% tolerance, `post.fixed_length_tolerance`), and `post` places each spoken chunk where the original's chunk starts. For these lines:

- the budget is mandatory; aim at or below the budget, preferably 1–2 syllables under;
- always provide variants if the full translation is close to the limit;
- if `batch submit` returned the hint "fixed_length lines over budget will be cut in-game", submit a shorter `dub_text` or variants.

## 4. Shortening after a QA decision (`shorten`)

The line arrives in a new batch with `task: "shorten"` and `current`, the current text. The QA note usually contains the actual duration ratio `ratio` (dub / original).

How much to cut: roughly `budget_syll × (1 − 1/ratio)` syllables. Example: budget 12 syllables, `ratio` 1.25 → remove about 2–3 syllables.

Rules:

- shorten `current`, do not translate from scratch: the terms, the formal or informal "you" and the wording found earlier have already been checked;
- if the line has already been shortened (this is the second `shorten`), look for a different sentence structure rather than one more word to drop; after two unsuccessful shortenings the CLI keeps the original voice;
- explain in `notes` what was removed ("removed the filler 'you know', ratio 1.25 → ~1.05").

## 5. The `retranslate` task

It arrives when QA sees poor intelligibility (`cer`) with a normal voice. The causes are almost always in the text:

- numbers, dates, units written as digits or abbreviations: write them as words;
- Latin script, brands, acronyms: per the glossary's `pronounce` or by transliteration;
- long compound words and consonant clusters: use a synonym;
- homographs (Russian за́мок / замо́к, "castle" / "lock"): rephrase or add a stress mark (for Russian: `references/russian.md`, section 4).
