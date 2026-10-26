# Translation batch format

Read this before working on a batch and when `batch submit` fails. Batches live in `translate/batches/`: the CLI writes `<bid>.in.jsonl`, the agent writes `<bid>.out.jsonl`. A `<bid>` looks like `b0001`.

## 1. `<bid>.in.jsonl` (read only)

The first line is the batch header:

```json
{"_batch": "b0007", "scene": "a1_m01", "scenes": ["a1_m01"], "target_lang": "ru", "source_lang": "en", "mode": "translate", "n": 58,
 "glossary": [{"term": "Zbyszek", "translation": "Збышек", "pronounce": "Збы́шек"}], "style": "# Style bible ...",
 "out": "b0007.out.jsonl", "delivery_tags": ["shouting", "whispered", "urgent", "energetic", "calm", "sad", "soft", "loud", "quick", "slow", "expressive", "flat"],
 "rules": "Write b0007.out.jsonl: one JSON object per line ..."}
```

| Field | Meaning |
|---|---|
| `_batch` | batch identifier |
| `scene`, `scenes` | the scene (if there is one) and all scenes of the batch |
| `target_lang`, `source_lang` | target language and original language |
| `mode` | `translate` or `adapt` (details: `references/adapt-vs-translate.md`) |
| `glossary` | terms of the canonical glossary found in this batch's source texts |
| `style` | the style bible (up to 8000 characters) |
| `out`, `rules` | name of the answer file and brief rules |
| `delivery_tags` | the vocabulary of delivery tags (section 2a) |

Then one line per voice line:

```json
{"id": "fmod:StreamingAssets/Dialogue_en-US.bank#fsb1:0042", "key": "dlg_a1_m01_d03_Zbyszek_01", "speaker": "Zbyszek",
 "src": "Okay, let's see.", "tgt_official": null, "dur_s": 2.35, "budget_syll": 13, "fixed_length": false,
 "placeholders": [], "context_prev": "...", "context_next": "...", "delivery_hint": []}
```

| Field | Meaning |
|---|---|
| `id` | line identifier; in the answer, the same one, byte for byte |
| `key` | engine key (a hint about the scene and the character) |
| `speaker` | the speaker from casting (character name) |
| `src` | source text |
| `tgt_official` | the official translation, if any (the base in `adapt` mode) |
| `dur_s`, `budget_syll` | duration of the original and the syllable budget (`references/length-budget.md`) |
| `fixed_length` | `true` for a cutscene, video or timeline: the budget is mandatory |
| `placeholders` | placeholders and tags of the source that must be preserved (`references/placeholders.md`) |
| `context_prev`, `context_next` | neighbouring lines of the scene, for coherence |
| `delivery_hint` | delivery tags measured from the original's prosody against the same character's other lines; `[]` when the line is the character's normal delivery, or without a measurement (section 2a) |
| `task` | only for rework: `adapt`, `shorten` or `retranslate` |
| `current` | only with `task`: the line's current text, to be improved or shortened |
| `current_delivery` | only with `task` and only when the current text has one: the `delivery` an agent chose for it; on `shorten`/`retranslate` it is kept unless you give `delivery` (section 2a) |

You can and should read the batch in full. Do not read other batches or project files.

## 2. `<bid>.out.jsonl` (written by the agent)

One object for **every** line of the batch, UTF-8 without BOM, no blank lines:

```json
{"id": "fmod:StreamingAssets/Dialogue_en-US.bank#fsb1:0042", "dub_text": "Так, посмотрим.", "variants": ["Так, глянем."], "notes": ""}
```

| Field | Required | Rules |
|---|---|---|
| `id` | yes | from the input; no extra and no missing ids |
| `dub_text` | yes | non-empty string; all placeholders and tags from `placeholders` |
| `sub_text` | no | string; only if the subtitle must differ from `dub_text`; the same placeholders |
| `variants` | no | list of non-empty strings, each shorter than the previous one; the same placeholders |
| `delivery` | no | only when you disagree with `delivery_hint`: a list of at most 3 tags of `delivery_tags`; `[]` = explicitly neutral; leave the field out (or `null`) to accept the hint, on a `shorten`/`retranslate` line with `current_delivery` to keep that one (section 2a) |
| `notes` | no | string: what was changed during adaptation, doubts, how a placeholder sounds |

A repeated `id` in the file is allowed: the last line wins. Lines with `_batch` or `_schema` are ignored.

## 2a. Delivery tags: `delivery_hint` and `delivery`

By default VoxCPM2 voices every line with the character's long original reference (the timbre) and a short English instruction made of the line's delivery tags (how this line is said), for example `(whispering, urgent)`. The CLI measures the tags on the original: pitch, loudness, voicing, pitch range and speaking rate of the line compared with the same character's other lines. A measurement hears the sound, not the meaning, and the actor sometimes recorded a line differently from how the scene plays. You know the scene: correct the hint where it is wrong.

| Tag | The voice is asked for | | Tag | The voice is asked for |
|---|---|---|---|---|
| `shouting` | shouting | | `soft` | a soft voice |
| `whispered` | whispering | | `loud` | a loud voice |
| `urgent` | urgency | | `quick` | a fast pace |
| `energetic` | energy | | `slow` | a slow pace |
| `calm` | calm | | `expressive` | expressive intonation |
| `sad` | sadness | | `flat` | flat intonation |

Rules:

1. Read `delivery_hint` together with the scene (`context_prev`, `context_next`, `scene`, `style`) and the line itself.
2. **Agree → leave `delivery` out.** That is the normal case: most hints are right, and most lines have `[]` (the character's normal delivery).
3. **Disagree → give `delivery`:** up to 3 tags from `delivery_tags`, written as in the vocabulary (case and spaces do not matter, repeats are dropped), the main delivery first (`["whispered", "urgent"]`). Do not combine opposites (`shouting` + `whispered`, `quick` + `slow`, `expressive` + `flat`).
4. **`[]`** = explicitly neutral: use it when the hint asks for a delivery the scene does not have (a "loud" line that is a calm explanation).
5. Your `delivery` wins over the measurement for this line (unless the project ignores agent tags: `tts.delivery_tags=measured`). The text and the tags must agree: a whispered line gets no exclamation marks, a shouted one keeps them. Repeating `delivery_hint` exactly is the same as leaving `delivery` out.
6. **Rework lines** (`task: shorten` or `retranslate`) with `current_delivery`: an agent already chose that delivery for the line. Leave `delivery` out to keep it (the CLI carries it over to the new text), give a new `delivery` to change it, or repeat `delivery_hint` to return to the measured delivery. Without `current_delivery` a rework line works like any other: no `delivery` accepts the hint.
7. Tags never count toward `budget_syll`, and `quick` does not make room for a longer text. Never put a delivery into `dub_text` as a bracketed direction.
8. Decide yourself; never ask the user. A real doubt goes to `notes`.

Examples (Russian target; the lines are illustrations):

| Scene and `src` | `delivery_hint` | Your answer |
|---|---|---|
| A customer shows off his own botched "repair"; Zbyszek, dry sarcasm: "Oh, wonderful. A true masterpiece." | `[]` | `"delivery": ["calm", "flat"]`: a neutral read would sound sincere; sarcasm is read calm and deadpan |
| Helena by a hospital bed, sobbing in the original: "Please… please, wake up." | `["expressive"]` | `"delivery": ["sad", "soft", "slow"]`: the pitch breaks are crying, not liveliness |
| An alarm is wailing, guards search the garage; Maciek hides and says to Zbyszek: "Get down. Not a sound." | `["urgent"]` | `"delivery": ["whispered", "urgent"]`: the actor recorded it at full voice, but in the scene he hides |
| Zbyszek laughs at Maciek's dented car: "You call THAT parking?!" | `["shouting", "quick"]` | `"delivery": ["energetic", "expressive"]`: a friendly joke, loud and lively, not an angry shout |
| A fight in the garage; Zbyszek: "Get out of my garage!" | `["shouting"]` | no `delivery` field: the hint is right |

```json
{"id": "fmod:StreamingAssets/Dialogue_en-US.bank#fsb1:0107", "dub_text": "Пригнись. Ни звука.", "delivery": ["whispered", "urgent"]}
```

`batch submit` rejects a `delivery` that is not a list, has an unknown tag or more than 3 tags (exit code 2, `field: "delivery"` in `errors_file`, a `hint` with the vocabulary). A `delivery` changed by a later submit makes `synth` voice the line again with the new delivery.

## 3. What `batch submit` checks

| Check | Result on violation |
|---|---|
| the file exists and every line is a valid JSON object | exit code 2, error with the line number |
| every `id` from the input is in the answer, and there are no extra `id`s | exit code 2: "missing in .out", "id is not in this batch" |
| `dub_text` is non-empty; `sub_text` is a string; `variants` is a list of non-empty strings | exit code 2 |
| placeholders of `dub_text`, `sub_text` and every variant match `src` (except line breaks) | exit code 2: "placeholders differ from src: missing '{0}' (1/0)" |
| `delivery`, when present, is a list of at most 3 tags of the vocabulary | exit code 2: "unknown delivery tag 'angry'", "delivery takes at most 3 tags, got 4"; the output has a `hint` with the vocabulary |
| syllables of `dub_text` are within 115% of the budget; if not, the first variant that fits | exit code 3 and `over_budget[]` if none fits |

With exit code 2, the full error list is in the file named by the `errors_file` field. After fixing, submit the same `<bid>` again. After a successful submission the CLI writes to `translate/translations.jsonl`: `id`, `dub_text`, `sub_text`, `lang`, `by` (`agent`), `rev`, `est_syll`, `budget_syll`, `fit`, `batch`, `variants`, `picked_variant`, `notes`, `delivery` (only when you gave it, or carried over from `current_delivery` on a rework line; `[]` is kept as explicitly neutral).

## 4. Working without the CLI (web chats)

In a web chat without access to the game files you can translate an exported batch: the user gives you `<bid>.in.jsonl`, you return the contents of `<bid>.out.jsonl` by the same rules, and the user puts the file next to it and runs `babeldub batch submit <bid>` on their machine.
