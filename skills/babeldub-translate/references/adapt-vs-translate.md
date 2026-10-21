# Official translation, adaptation or translation

Read this after `babeldub translate plan` if it is unclear which mode is needed, and before working in `adapt` mode.

## 1. Three modes

| Mode | Source material | Who writes the text | Pros | Cons |
|---|---|---|---|---|
| `adapt` (the default with official texts) | the official translation as the base | the agent turns it into spoken dub text | terminology and meaning as in the game, but it sounds natural and fits the length; the subtitle stays official | the voice differs from the on-screen text in places |
| `translate` | the source text | the agent translates | full control over how it sounds | no official terminology; more work and more checks |
| `official` (explicit only) | the game's official translation into the target language | the CLI copies it (`translate use-official`) | subtitles and voice match word for word; zero translator work | official text was written to be read, not spoken: it can be long, bookish, full of digits, abbreviations and Latin names |

The `translation.mode` setting:

- `auto` (default): `adapt` when official target-language text exists (lines without it are translated from `src` in the same batches), otherwise `translate`.
- explicit: `babeldub set translation.mode=official` (or `adapt`, `translate`), then `babeldub translate plan`.

## 2. When to choose which

| Situation | Mode |
|---|---|
| there is no official translation | `translate` (`auto`) |
| an official translation exists, complete or not | `adapt` (`auto`) |
| the user wants the voice to match the subtitles word for word and the official text is already conversational | `official`, set explicitly; mention the trade-off in the report |

Choosing the mode is not a question for the user (the two-questions rule). Keep `auto` unless there is a reason; put a changed mode and the reason into the final report.

Why `adapt` is the default: in the Low-Budget Repairs listening rounds (English → Russian) the official Russian text, voiced as is, sounded read rather than spoken, and digits and Latin-script names («Pine Street 36») broke the synthesis; the adapted lines («Пайн-стрит, тридцать шесть») won. The official text still exists for every key there, so meaning and terms stay as the players see them.

## 3. Adaptation rules (`adapt`)

The base is `tgt_official`. Your task is to make it **speakable in the character's voice and fit it into the duration**, not to rewrite it.

1. **Meaning and terms do not change.** Names, titles, key information stay as in the official text and the glossary.
2. **Edit only for the sake of the voice:**
   - length: shorten to the budget (`references/length-budget.md`);
   - pronounceability: numbers, dates and symbols in words, abbreviations expanded, Latin-script names transliterated into the target script (per the glossary), «ё» in Russian; no U+0301 stress marks (the default TTS engines voice them as extra sounds);
   - register: spoken and colloquial, as the character would say it (the style bible); keep the character's swearing level;
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
- many `over_budget` lines, or cutscenes that do not fit: a reason to go back to `adapt` (`babeldub set translation.mode=auto`, `translate plan`, `batch make`); in that mode lines with text from `official` go into batches;
- later QA itself sends to `shorten` whatever still does not fit after post-processing.

## 6. Subtitles

In v0.1 the game's subtitles are not rebuilt: the player sees the official (or source) text and hears `dub_text`. So in `adapt` keep `dub_text` close to the official text in meaning and in the order of information; then the mismatch with the on-screen line is barely noticeable. `sub_text` is kept in the project for a future version that writes subtitles into the game; in `adapt` it defaults to the official text when the agent leaves it out.
