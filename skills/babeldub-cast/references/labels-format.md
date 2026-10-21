# Format of `cast/labels.agent.jsonl`

Read this before labelling characters. The agent (caster role) writes the file; `babeldub cast apply-labels` moves it into the canon.

## 1. Format

UTF-8 without BOM, one JSON object per line, no blank lines between objects:

```json
{"char_id": "cluster_03", "name": "Maciek", "name_tgt": "Мачек", "gender": "m", "age": "adult", "notes": "the hero's friend, colloquial style, jokes a lot; confidence high"}
{"char_id": "zbyszek", "name_tgt": "Збышек", "gender": "m", "age": "adult"}
```

| Field | Required | Values | Meaning |
|---|---|---|---|
| `char_id` | yes | an existing `char_id` from the casting sheet | whom we label; a label never creates new characters |
| `name` | no | non-empty string | name in the original language (as in keys, event folders, forms of address) |
| `name_tgt` | no | string | name in the target language |
| `gender` | no | `m` \| `f` \| `u` | the speaker's gender; `u` means unknown |
| `age` | no | `child` \| `adult` \| `old` \| `u` | age; `u` means unknown |
| `notes` | no | string | speech traits useful to the translator, plus confidence |

No other fields are allowed: `apply-labels` rejects a line with an unknown field. Include only what you change: a missing field leaves the current value untouched. A repeated `char_id` is a new revision; the last line wins.

After writing, run `babeldub cast apply-labels`. Exit code 2 means errors in `errors[]` (line number and reason); fix them and repeat, up to 3 iterations.

## 2. How to determine the fields

**`name`.** Sources by reliability: the event folder or key (`.../Maciek/dlg_...`), the game's dialogue data, forms of address in neighbouring lines ("Maciek, wait!"), speaker labels in subtitles (`{CharacterName}:`). For a cluster: if its lines address the character by name, use that name; if not, put a descriptive label in `notes` and leave `name` unchanged.

**`name_tgt`.** If an official translation into the target language exists, take the name from it: in the `official` and `adapt` modes official terminology is mandatory. If not, transliterate by the norms of the target language, taking the language of the name's origin into account (Polish Maciek → "Мачек", not "Мациек"). The same name goes into the glossary (lexicon role); keep them identical.

**`gender`.** From text only:

- grammar of the official translation (Russian: "я пошёл / пошла", "рад / рада");
- forms of address to the character in neighbouring lines ("sir", "ma'am", "pani");
- keys and names (`_F`, female and male names);
- if there are no signs, use `u`, and the translator will phrase lines neutrally.

Gender matters three times: for the grammar of the translation, for the QA check "the voice changed gender", and for choosing neutral wording. A wrong value is worse than `u`.

**`age`.** `child` needs explicit signs of a child in the text or keys; `old` needs forms of address like "grandpa" or "old man", or the plot; otherwise `adult` if you are confident, `u` if you are not.

**`notes`.** One or two sentences: register (vernacular, bureaucratese, jargon), pet words, pace, manner (stutters, swears, speaks in short phrases), whom the character addresses formally or informally, if that is visible. End with confidence: "confidence high / medium / low, and why". Do not put instructions for other roles there.

## 3. What a label cannot do

- **Split a cluster** into two characters or **merge** two characters into one: only the CLI divides the canon. `babeldub cast` itself merges a cluster into a named character whose voice it matches (`cast.merge_threshold`, `references/merging.md`); a cluster it left apart can still get that character's `name` through a label. If a cluster is mixed (different names in forms of address, different verb gender), describe it in `notes` and return it in `human_pending` of your answer.
- **Reassign a line** to another character.
- **Choose a reference**: that is done by `babeldub cast` (skill `babeldub-cast`, `references/ref-selection.md`).
- **Choose whom to dub**: `babeldub cast select` after the question to the user.
