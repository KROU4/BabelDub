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
- The translation budget in syllables: `budget_syll` = **speech span** of the original (`speech_s`, not the file length) × the character's measured pace (`rate_syl_s`: the median over its accepted dubs; until it has 3 of them, the language's pace: 5.5 syllables per second for Russian, 5.8 for English, 7.2 for Spanish). In Russian the number of syllables equals the number of vowels.
- `pause_cut_s`: how much of the dub's internal pauses `post` removed; `onset_deltas`: placed minus original speech onset (per segment for `fixed_length`); `chunk_tempo`: the tempo range of the placed chunks.

## 3. Default limits

| Line type / step | Limit | Setting |
|---|---|---|
| `fixed_length` (cutscenes, videos, timelines) | `ratio` ≤ 1.03 and the speech fits into the original file | `post.fixed_length_tolerance` = 0.03 |
| everything else (`dialogue`, `bark`) | `ratio` ≤ 1.35 | `qa.max_duration_ratio` = 1.35 |
| overrun that is not sped up | within 3% of the room | `post.tolerance` = 0.03 |
| tempo range of `post` | 1.0 (never slower) … 1.15 (faster) | `post.tempo_min`, `post.tempo_max` |
| pause shortening before any speed-up | each pause down to max(80 ms, half of it) | fixed |

Every file has exactly the original's sample count when the fitted speech fits into it (`exact: true`); otherwise the file runs longer and the line is in `not_exact[]`.

The 1.35 limit for dialogue is soft: it catches obvious failures (model looping, extra words), not every long phrase. For a game where dialogue is tightly bound to timing, you can tighten it: `babeldub set qa.max_duration_ratio=1.15`. Research on dubbing pipelines suggests targets of about 1.15 for dialogue and 1.3 for "barks", but this has not been verified on games ❓.

## 4. The fitting cascade

1. **Syllable budget during translation** (skill `babeldub-translate`). The translator aims for `budget_syll`; for `fixed_length` the budget is mandatory. If the full translation does not fit, they provide up to two shorter `variants`, and `batch submit` takes the fullest one that fits.
2. **Best of N.** `synth` generates `tts.best_of` (3) takes per line and keeps the one whose speech length is closest to the original's (log ratio of the speech spans).
3. **Fit in `post`**, never slower: a dub shorter than the original keeps its tempo and the rest is silence. A dub that does not fit the room after the original's speech onset first loses part of its internal pauses (each down to max(80 ms, half of it)), then speeds up without changing pitch to at most `post.tempo_max` (1.15). For `fixed_length` lines the dub is split into chunks that are matched to the original's speech segments (from `inventory/analysis.jsonl` when `analyze` ran, else the energy VAD); each chunk starts where its segment starts and is fitted to that segment (shorter is fine). Tool: Rubber Band (R3 engine) if the user installed it; otherwise the `rubberband` filter in ffmpeg; otherwise `atempo` in ffmpeg. Rubber Band is distributed under the GPL and is not part of BabelDub.
4. **Regeneration.** The model sometimes speaks slower or inserts pauses; a new attempt (`regen`) is often shorter. The automatic QA decision for length is not `regen` but `shorten`; however, if a length failure coincides with an intelligibility failure, `regen` comes first.
5. **Shortening the text** (`shorten`): the line goes back to the translator with the actual `ratio`. The CLI allows two shortenings in a row; if the line still does not fit after them, it becomes `keep_original`: the line keeps the original voice and is listed in the report.

`post` never slows a short dub down (`post.tempo_min` 1.0; slow speech sounds unnatural) and pads the rest with silence; the final file keeps the original's exact length whenever the speech fits.

## 5. How to resolve `over_length[]` and the `duration` reason

| Situation | Decision |
|---|---|
| `fixed_length`, `ratio` slightly above 1.03, the text can be compressed | `shorten` with a `note`: "ratio 1.08, cut ~8% (≈2 syllables)" |
| regular dialogue, `ratio` 1.35–1.5 | the CLI assigns `shorten` itself (at most twice, then `keep_original`). If in this game the audio is not bound to timing (asynchronous instruments, the next line waits for the sound to end) and the meaning cannot bear shortening, the qa role may give `accept` with a `note`; put the reason in the report |
| a huge `ratio` (above 2) | almost always a model failure (repetition, looping): `regen` |
| `ratio` < 0.7 | the dub is noticeably shorter: usually fine (the original's pauses are kept); if it looks like skipped words, check `cer`, and with a high `cer` use `regen` |

How much to cut: syllables ≈ `budget_syll` × (1 − 1/`ratio`). For example, with a budget of 12 syllables and `ratio` 1.2, remove about 2 syllables.

## 6. What to say in the report

- How many lines `post` compressed and by how much (`pause_cut_s` and `tempo` in `post/post.jsonl`), for cutscenes the largest `onset_deltas`, how many the translator shortened, how many kept the original voice because of length.
- For cutscenes and videos: "check in the game that lines are not cut off and do not overlap the next ones". The behaviour of the running game is not checked automatically.
