# QA thresholds: values, calibration, when to change them

Read this when there are suspiciously many or suspiciously few defects, and after the pilot.

## 1. Default values

| Setting (`project.json`) | Value | What it checks |
|---|---|---|
| `qa.max_cer` | 0.15 | intelligibility: share of character errors in recognition |
| `qa.min_sim_ratio` | 0.75 | voice similarity: `sim ≥ 0.75 × sim_ref` |
| `qa.max_duration_ratio` | 1.35 | length of ordinary lines |
| `post.fixed_length_tolerance` | 0.03 | length of `fixed_length` lines (cutscenes, video): `ratio ≤ 1.03` |
| `post.true_peak_db` | −1.0 | peak ceiling; QA allows another 0.5 dB |
| `qa.silence_db` | −50 | below this the line counts as silence |
| `tts.max_attempts` | 3 | how many synthesis attempts before `keep_original` |

These are starting values, not the result of calibration: nobody has checked them on the voices of a particular game. The research BabelDub is built on also proposed stricter targets for Russian (CER up to 0.08 is "good", up to 0.20 is "regenerate"; similarity no lower than the character's baseline minus 0.05), but those are marked as needing calibration too.

## 2. Calibration on the pilot

After the pilot (skill `babeldub-voice`, step 3), look at the metric distribution of the main characters:

```text
babeldub query --where char=<id> --fields id,qa.cer,qa.sim,qa.sim_ref,qa.ratio,verdict --limit 50
babeldub report
```

- **CER.** If lines whose `asr_text` matches `dub_text` in meaning (differences in names and numbers) systematically score CER above the threshold, the threshold is too strict for this game (many names and terms). Raise it: `babeldub set qa.max_cer=0.2`.
- **Similarity.** If almost all of a character's lines fail similarity, and `sim_line` and `sim_char` behave the same, check the reference and the attribution (casting) first, and only then the threshold. Lowering `min_sim_ratio` below 0.65 means letting "foreign" voices through.
- **Length.** If lines in the game are tightly bound to timing (synchronous instruments, the next dialogue starts on a timer), tighten it: `babeldub set qa.max_duration_ratio=1.15`.

Put every threshold change, with its reason, into the final report.

## 3. After changing a threshold

- The new threshold applies to subsequent checks. To recheck attempts that were already checked, run `babeldub qa --force` (the stage flag from the common `--force` set). It recomputes the metrics and auto-decisions; the CLI does not overwrite agent decisions for the same attempt.
- The `sim_ref` baseline is cached in `qa/baseline.json` and does not depend on thresholds.

## 4. Signs that the threshold is not the problem

| Observation | Most likely | Where to go |
|---|---|---|
| defects for one character, none for the others | a bad reference, an attribution error, dirty originals | the voice and caster roles, skill `babeldub-cast` |
| CER defects equal across all characters | the text: numbers as digits, Latin script, abbreviations | `retranslate`, glossary (`pronounce`) |
| widespread length defects | the translation is longer than the original | syllable budget, `adapt` mode, `shorten` |
| high CER only on lines under 1 s | ASR is unreliable on short audio | `accept` with a `note` if `asr_text` is close in meaning |
| low similarity on all lines of one engine | the engine clones this language or language pair poorly | skill `babeldub-voice`, the section on accent |
