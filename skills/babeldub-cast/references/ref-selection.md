# Character voice references

Read this when checking the references of the main characters, when `no_ref[]` is not empty, and when you see warnings about relaxed rules.

## 1. Why a reference, if the prompt is the line itself

The main voice prompt of the dub is the original recording of the same line (`tts.ref_strategy=line`, skill `babeldub-voice`): it carries both the timbre and the emotion of that phrase. The character reference (`char`) is needed where the line cannot serve as a prompt:

- short lines ("Yes", "Hey!") below `tts.line_ref_min_s` (1.6 s);
- lines with music or noise under the voice (`has_bed`) and non-verbal lines (`nonverbal`);
- lines without original text;
- regeneration with a different prompt after the QA decision `reref`.

QA also compares the voice of every dubbed line with the character reference (`sim_char`). So a bad reference spoils both the synthesis and the quality score.

## 2. How the CLI picks references

For every **selected** character, `babeldub cast` takes the character's original lines (including the lines of voice clusters merged into the character, `references/merging.md`) and filters them:

1. **Flags:** no `has_bed`, `nonverbal`, `alt_take`, `no_text`, `no_audio`.
2. **Duration:** from `--min-ref-s` (3 s) to `--max-ref-s` (12 s); the ideal is about 7 s.
3. **Known original text**: it becomes the `ref_text` of the prompt.
4. **Level:** no clipping, peak not below −40 dB, speech loudness not below −40 dB.
5. **Score:** closeness of the duration to 7 s and a normal speech level (−30 to −8 dB); if the `qc` worker is installed, also closeness to the "centroid" of the character's voice (the most typical lines win). Total: 0.4 × heuristic + 0.6 × similarity to the centroid.
6. **Relaxing the rules** if nothing qualifies: first duration 1.5–20 s, then no text requirement, then both. In that case `warnings` contains "no ideal reference, rules relaxed (…)".

The best `--refs` (up to 3) candidates are copied to `cast/refs/<char_id>/<uid>.wav` and recorded in `cast/refs.jsonl`: `ref_id`, `char_id`, `line_id`, `wav`, `text`, `lang`, `dur_s`, `score`, `chosen`, `rank`. The top-ranked one is the primary reference (`chosen`).

## 3. What makes a good reference

| Property | Why | Bad example |
|---|---|---|
| neutral delivery | `char` is used for short and "dirty" lines of any emotion; a neutral timbre transfers into them predictably | shouting, whispering, laughing, crying, singing |
| clean sound | the model clones the background too | music, echo, radio, street noise |
| 3–12 s of speech | shorter: the timbre is unstable; longer than 15 s: the CLI trims the prompt and loses the transcript | "Hmm.", a 25-second monologue |
| text matches the audio exactly | the transcript aligns the prompt | the subtitle is shortened or rewritten relative to the voice-over |
| the character's typical voice | QA compares dubbed lines with it | a line where the character imitates someone else, talks with a full mouth, or speaks over the phone |
| the same actor | — | a line wrongly attributed to the character |

## 4. How to check (caster role)

You cannot hear audio. Check by text, flags and numbers:

1. Find the character's section in `cast/casting_sheet.md`, or query the references: `babeldub query --where char=<char_id> --fields id,text.src,audio.dur_s,flags --limit 20`, and match them against the `ref_id` values in the casting sheet.
2. **Reference text.** Repeated exclamation marks, all caps, "Aaaah!", "*whispering*", laughter in the text, stage directions are signs of emotional delivery. Short questions and calm narrative sentences are good candidates.
3. **Scene and key.** Keys like `_shout`, `_whisper`, `_radio`, `_phone`, `_scream`, and fight or chase scenes are suspicious for a neutral reference.
4. **Flags** `has_bed` and `nonverbal` must not be on a reference (if the rules were relaxed, they may be; that is a warning).

What to do with a bad reference in v0.1:

- if the character has several references and only the first is bad, record it in `notes`: on `reref`, QA moves on to the next one;
- if all of them are bad, record it in the character's `notes`, in `warnings` of your answer, and as a line in the report. You can re-run `babeldub cast --refs 3 --min-ref-s 4` with different duration bounds; v0.1 does not let you pick a specific file by hand;
- if a main character has no good reference at all, keep `tts.ref_strategy=line` for them (the default `auto` already takes `line` for every usable line) and do not switch to `char`.

## 5. Reference language

A reference carries the phonetics of its language; synthesis in another language may come out with an accent (skill `babeldub-voice`, `references/ref-strategy.md`, the accent section). If the game has voice-overs in several languages, references come from the locale that `extract` extracted. Comparing reference languages is a manual pilot experiment; record its outcome in the report.

## 6. What not to do

- Do not "clean" references (denoising, EQ, normalization): the prompt must sound like the character in the game.
- Do not splice several lines into one reference by hand.
- Do not use another character's line "with a similar voice" as a reference: QA compares against this character's originals, and such a dubbed line fails the similarity check.
