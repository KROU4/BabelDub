# Line length: how much is allowed and how to fit it

Read this on `over_length[]` from `post`, on the `duration` reason in QA, for cutscenes and videos (`fixed_length`), and before a `shorten` decision.

## 1. Why length matters

In a game, a line is a separate file that the engine plays. Length is dangerous where the audio is tied to time:

- **Cutscenes, timelines, videos.** A timeline clip or a video has a fixed length: a long line gets cut off or overlaps the next one. The CLI marks such lines `fixed_length: true`.
- **Synchronous middleware instruments.** In FMOD, a synchronous instrument plays only the part of the sound under the timeline cursor; an asynchronous one in Cut mode may also cut off a long sound. An asynchronous programmer instrument plays the line in full: in Low-Budget Repairs, a 6.0 s replacement for a 1.92 s line played completely (an experiment on the FMOD runtime; the game itself was not launched).
- **Regular dialogue**, where the next line waits for the sound to end, tolerates longer lines gracefully, but subtitles and facial animation may drift from the audio.
- **Length stored in metadata** (Bethesda `.lip`, Source phonemes) is not updated when the audio is replaced.

## 2. What the CLI computes

- The target is the **speech duration of the original** without leading and trailing pauses (`target_s` in `post/post.jsonl`).
- `ratio` = speech duration of the dub after fitting / `target_s`. QA checks the same value.
- The translation budget in syllables: `budget_syll` = duration of the original file × the language's pace (5.5 syllables per second for Russian, 5.8 for English, 7.2 for Spanish). In Russian the number of syllables equals the number of vowels.

## 3. Default limits

| Line type | `ratio` limit | Setting |
|---|---|---|
| `fixed_length` (cutscenes, videos, timelines) | ≤ 1.03 | `post.fixed_length_tolerance` = 0.03 |
| everything else (`dialogue`, `bark`) | ≤ 1.35 | `qa.max_duration_ratio` = 1.35 |
| threshold at which `post` starts compressing a regular line | > 1.05 | `post.tolerance` = 0.05 |
| maximum compression | 12% | `post.max_stretch` = 0.12 |

The 1.35 limit for dialogue is soft: it catches obvious failures (model looping, extra words), not every long phrase. For a game where dialogue is tightly bound to timing, you can tighten it: `babeldub set qa.max_duration_ratio=1.15`. Research on dubbing pipelines suggests targets of about 1.15 for dialogue and 1.3 for "barks", but this has not been verified on games ❓.

## 4. The fitting cascade

1. **Syllable budget during translation** (skill `babeldub-translate`). The translator aims for `budget_syll`; for `fixed_length` the budget is mandatory. If the full translation does not fit, they provide up to two shorter `variants`, and `batch submit` takes the fullest one that fits.
2. **The `line` prompt.** Cloning from the original line carries over its pace, so the dub is naturally closer to the original length than with a neutral reference.
3. **Compression in `post`** without changing pitch, no more than `post.max_stretch`. Tool: Rubber Band (R3 engine) if the user installed it; otherwise the `rubberband` filter in ffmpeg; otherwise `atempo` in ffmpeg. Rubber Band is distributed under the GPL and is not part of BabelDub.
4. **Regeneration.** The model sometimes speaks slower or inserts pauses; a new attempt (`regen`) is often shorter. The automatic QA decision for length is not `regen` but `shorten`; however, if a length failure coincides with an intelligibility failure, `regen` comes first.
5. **Shortening the text** (`shorten`): the line goes back to the translator with the actual `ratio`. The CLI allows two shortenings in a row; if the line still does not fit after them, it becomes `keep_original`: the line keeps the original voice and is listed in the report.

`post` cannot and should not slow speech down: a short dub is padded with the original's pauses. For `fixed_length`, the final file is no longer than the original if the speech fits.

## 5. How to resolve `over_length[]` and the `duration` reason

| Situation | Decision |
|---|---|
| `fixed_length`, `ratio` slightly above 1.03, the text can be compressed | `shorten` with a `note`: "ratio 1.08, cut ~8% (≈2 syllables)" |
| regular dialogue, `ratio` 1.35–1.5 | the CLI assigns `shorten` itself (at most twice, then `keep_original`). If in this game the audio is not bound to timing (asynchronous instruments, the next line waits for the sound to end) and the meaning cannot bear shortening, the qa role may give `accept` with a `note`; put the reason in the report |
| a huge `ratio` (above 2) | almost always a model failure (repetition, looping): `regen` |
| `ratio` < 0.7 | the dub is noticeably shorter: usually fine (the original's pauses are kept); if it looks like skipped words, check `cer`, and with a high `cer` use `regen` |

How much to cut: syllables ≈ `budget_syll` × (1 − 1/`ratio`). For example, with a budget of 12 syllables and `ratio` 1.2, remove about 2 syllables.

## 6. What to say in the report

- How many lines `post` compressed and by how much (`stretch` in `post/post.jsonl`), how many the translator shortened, how many kept the original voice because of length.
- For cutscenes and videos: "check in the game that lines are not cut off and do not overlap the next ones". The behaviour of the running game is not checked automatically.
