# Official translation, adaptation or translation

Read this after `babeldub translate plan` if it is unclear which mode is needed, and before working in `adapt` mode.

## 1. Three modes

| Mode | Source material | Who writes the text | Pros | Cons |
|---|---|---|---|---|
| `official` | the game's official translation into the target language | the CLI copies it (`translate use-official`) | subtitles and voice match word for word; zero translator work | official text was written to be read, not spoken: it can be long, bookish, full of digits and abbreviations |
| `adapt` | the official translation as the base | the agent edits it for voice and length | terminology and meaning as in the game, but it sounds natural and fits the length | subtitles (unchanged in v0.1) diverge from the voice in places |
| `translate` | the source text | the agent translates | full control over how it sounds | no official terminology; more work and more checks |

The `translation.mode` setting:

- `auto` (default): `official` if official text exists for ≥ 95% of the voiced lines with text, otherwise `translate`. **`auto` never chooses `adapt`.**
- explicit: `babeldub set translation.mode=adapt` (or `official`, `translate`), then `babeldub translate plan`.

## 2. When to choose which

| Situation | Mode |
|---|---|
| there is no official translation | `translate` |
| an official translation exists; lines are short and conversational, with no digits or abbreviations | `official` |
| an official translation exists but is noticeably longer than the original, bookish, with digits and Latin script, and there are `fixed_length` cutscenes | `adapt` |
| the official translation is incomplete (below 95%) | `auto` gives `translate`; if the official text is good, `adapt` is better: the agent translates lines without official text and adapts the rest |

Choosing the mode is not a question for the user (the two-questions rule). Decide from the `translate plan` output (`official_share`) and a sample: `babeldub query --where translated=false --fields id,text.src,text.tgt_official,audio.dur_s --limit 20`; compare the length of the official text with the syllable budget. Put the decision and the reason into the final report.

Example: in Low-Budget Repairs official Russian text exists for every dialogue key, and the median ratio of Russian to English line length is 1.00, so the game analysis recommends `adapt`: almost nothing needs changing, but the cutscenes need fitting. This is an illustration, not a rule for other games.

## 3. Adaptation rules (`adapt`)

The base is `tgt_official`. Your task is to make it **speakable in the character's voice and fit it into the duration**, not to rewrite it.

1. **Meaning and terms do not change.** Names, titles, key information stay as in the official text and the glossary.
2. **Edit only for the sake of the voice:**
   - length: shorten to the budget (`references/length-budget.md`);
   - pronounceability: numbers and dates in words, abbreviations expanded, Latin script per the glossary, «ё» in Russian;
   - intonation: restore the emotional shape of the original if the official text smoothed it out (the original shouts "Stop!", while the official text says «Пожалуйста, остановитесь.» ("Please stop.") → «Стой!» ("Stop!"));
   - living speech: replace bookish phrases an actor would never say (Russian examples: «осуществить», «в настоящий момент»).
3. **Explain every noticeable difference** in `notes`: "shortened for length", "restored the exclamation", "number in words". Minor edits («ё», numbers) need no explanation.
4. **If the official text is already good**, return it unchanged in `dub_text`. Do not edit for the sake of editing.
5. **If the official text is wrong** (wrong gender of the speaker, mixed-up formal and informal "you", an obvious mistranslation), fix it and always mark it in `notes`.

## 4. Translation (`translate`)

- Translate from `src` by the rules of the skill: emotional shape, budget, placeholders, glossary, grammar.
- If some lines have an official translation (`tgt_official` is not empty), use it as a guide for terminology and style.
- Lines without source text do not go into batches: they are not translated and sound in the original.

## 5. Official text (`official`)

`babeldub translate use-official` copies the official text into `translate/translations.jsonl` (`by: official`) and computes `fit`. Output: `copied`, `missing`, `over_budget`. Then:

- `missing` > 0: lines without official text → `batch make` → translator;
- many `over_budget` lines, or cutscenes that do not fit: a reason to switch to `adapt` (`babeldub set translation.mode=adapt`, `translate plan`, `batch make`); in that mode lines with text from `official` go into batches;
- later QA itself sends to `shorten` whatever still does not fit after post-processing.

## 6. Subtitles

In v0.1 the game's subtitles are not rebuilt: the player sees the official (or source) text and hears `dub_text`. So in `adapt` keep `dub_text` close to the official text in meaning and in the order of information; then the mismatch with the on-screen line is barely noticeable. `sub_text` is kept in the project for a future version that writes subtitles into the game.
