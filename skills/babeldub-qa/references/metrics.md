# QA metrics: what they mean and where they go wrong

Read this the first time you review defects. The metrics are computed by the `qc` worker (environment `BABELDUB_HOME/envs/qc`) from the latest `post/audio/<uid>.wav` file of each line; the result is a row in `qa/qa.jsonl`.

## 1. Fields of `qa/qa.jsonl`

`id`, `attempt`, `char_id`, `wav`, `asr_text`, `asr_text_raw`, `cer`, `cer_raw`, `cer_folded`, `asr2_text`, `cer_asr2`, `asr_disagree`, `cer_skipped`, `sim`, `sim_line`, `sim_char`, `sim_ref`, `sim_ratio`, `sim_threshold`, `sim_rule`, `orig_sim`, `sim_rel`, `sim_skipped`, `mos`, `orig_mos`, `prosody_sim`, `f0_range_st`, `orig_f0_range_st`, `contour_corr`, `f0_median_hz`, `orig_f0_median_hz`, `pitch_shift_st`, `margin`, `closest_char`, `ratio`, `dur_s`, `peak_db`, `ltas_db`, `voice_wav`, `class`, `verdict` (`pass` | `fail`), `reasons[]`, `ts`. Inspect them with `babeldub query`; do not read the file whole. In `query` these fields are available with the `qa.` prefix (`--fields id,qa.cer,qa.sim,qa.reasons`), while `verdict` and `decision` use short names in `--where`.

## 2. Intelligibility: `cer`

- **How.** ASR transcribes the dub in the target language (in the v0.1 catalog — faster-whisper large-v3-turbo); CER is the share of character edits between the normalized `dub_text` and the recognized text. Normalization: lower case, no punctuation or markup, «ё» → «е», digits spelled out as words on both sides (num2words, in the CLI or in the qc worker), so «25» heard for «двадцать пять» is no error.
- **What it catches:** skipped and repeated words, model hallucinations (extra phrases), "swallowed" word endings, wrong readings (Latin script, numbers), a strong accent, silence instead of speech.
- **Where it goes wrong:**
  - ASR makes mistakes too, especially on short lines («Эй!», «Хм»): one wrong character out of three is already CER 0.33;
  - numbers: both sides are spelled out, but an ordinal or a case form («25-го», «двадцать пятого») can still differ from what num2words writes;
  - ASR spells names and invented game words as it hears them («Збышек» → «збышик»);
  - placeholders stay in `dub_text` (the CLI strips them only before synthesis), but they are not in the speech;
  - ASR recognizes interjections and emotional exclamations poorly;
  - Whisper invents text on silence or noise ("Продолжение следует...", "Subtitles by..."). The worker runs it with a voice-activity filter and without conditioning on previous text, and strips known credit/outro tails that `dub_text` does not contain (`asr_text`; Whisper's own output stays in `asr_text_raw`). If `asr_text` still ends with a phrase that is not in `dub_text` while the rest matches, it is an ASR artefact, not a dub defect.
  - Russian vowel reduction: Whisper writes what it hears («закрита», «пасмотрим»). `cer_folded` folds the reducible vowels on both sides (о → а, е/э/я → и), and `cer` (the gate) is the lower value, so a correct reduced pronunciation does not fail; `cer_raw` is the plain CER.
- **Second recognizer (Russian).** GigaAM-v3 CTC transcribes the dub too (`asr2_text`, `cer_asr2`); `cer` is the lowest of all CERs. `asr_disagree: true` when Whisper and GigaAM differ by more than `qa.max_asr_disagreement` (0.15): one of them misheard; it never fails a line on its own. Without the model (or in an old qc env) the fields are null and `qa` warns once.
- **How to read it:** compare `dub_text` and `asr_text` (and `asr2_text`) by eye. If the difference is in a name or a number and the rest matches, it is not a pronunciation defect: `accept` with a `note`, or `retranslate` (numbers as words). If ASR returned completely different text or only half of it, it is a real defect: `regen`.

## 3. Is it the same voice: `sim`, `sim_line`, `sim_char`, `sim_ref`

- **How.** SpeechBrain ECAPA (voxceleb) voice embeddings for the dub, for the original recording of this line and for the character's main reference; cosine similarity. When `post.match` changed the take (the post row has `pre_match_wav`), the dub's embedding — like `margin`, `mos` and the prosody — comes from the take **before** the channel match (`voice_wav` in the QA row): an EQ or a radio chain is the channel, not the voice. ASR/CER and the levels use the final file.
  - `sim_line` — the dub against its own original (if the original is clean: at least 1 s long, without the `has_bed`, `nonverbal`, `alt_take`, `no_audio` flags);
  - `sim_char` — the dub against the character's reference (if the reference is not this same line);
  - `sim` — the mean of whatever could be computed.
- **The `sim_ref` baseline.** How similar the character's own original lines are to each other: the mean pairwise similarity of up to 12 of their longest clean originals. If there are fewer than two clean originals — the median over other characters, and without it — 0.5. Cache — `qa/baseline.json`.
- **Why relative to the baseline, and why two ratios.** Absolute similarity values differ between voices, so the bar follows the character's baseline: `sim ≥ sim_threshold = max(sim_ratio × sim_ref, qa.min_sim_abs)`. The baseline is measured within one language (English originals against each other), while a dub crosses a language boundary (Russian dub against the English original), and ECAPA always scores that lower. So a dub in another language than the original audio (`source_lang` ≠ `target_lang`, the normal case) uses `qa.min_sim_ratio_xlang` = 0.45; a same-language dub keeps `qa.min_sim_ratio` = 0.75. The absolute floor `qa.min_sim_abs` = 0.25 stops a character with a low baseline from letting anything through. Each row records the ratio it used (`sim_ratio`) and the resulting bar (`sim_threshold`).
- **Calibration evidence** (Low-Budget Repairs, Russian Qwen3-TTS dub of 30 English lines of the hero, ECAPA): the hero's `sim_ref` was 0.76, so the same-language bar was 0.75 × 0.76 = 0.57, and it failed 27 of 30 dubs on `sim` although the Whisper CER median was 0.0. Measured against the hero's voice centroid, good dubs scored 0.38–0.66, clear failures 0.08–0.11 (and were closest to another character), borderline lines 0.18–0.24 (one of them a scream flagged `nonverbal` whose own original scores only 0.09 against the centroid). With 0.45 × 0.76 = 0.34 and the 0.25 floor, the good dubs pass and the failures do not.
- **One definition.** `sim` and `sim_ref` are both line-to-line cosines (`qa/baseline.json` records the definition). A cosine against a centroid (the calibration values above, `sim_ref_loo` in the baseline file) runs higher, because the centroid averages many lines: never compare a centroid number with `sim_ref`.
- **Short or atypical originals: `sim_rule`.** An original with under `qa.short_sim_s` (1 s) of speech, or whose own similarity to the character's voice is below the bar (a whisper, a radio line), cannot be matched by any dub. Then QA measures the original's own similarity to the same target (`orig_sim`) and the dub passes `sim` when it reaches the absolute bar (`sim ≥ sim_threshold`, cross-language ratio included) or when `sim_rel` = the dub's cosine to that target / `orig_sim` ≥ `qa.short_sim_ratio` (0.8): `sim_rule: "relative"`. A dub that passes the absolute bar always passes. Otherwise, and when `orig_sim` ≤ 0.05 (a ratio to about zero would pass any voice), `sim_rule: "absolute"` and `sim_rel` is null.
- **What it catches:** a "foreign" voice, a gender swap, a "narrator" timbre instead of the hero, heavy voice distortion.
- **Where it goes wrong:**
  - emotional delivery (shouting, whispering) changes the embedding: a dubbed shout can be "similar" to the original shout but not to the neutral reference — then `sim_line` is high and `sim_char` is low;
  - short lines (under 1–1.5 s) give noisy embeddings;
  - effects in the original (radio, phone, echo) lower `sim_line` even when the timbre is right;
  - a character voiced by several actors, or one whose voice changes over the story, gets a low `sim_ref`.
- **How to read the pair:**

| `sim_line` | `sim_char` | Likely cause | Decision |
|---|---|---|---|
| high | high | normal | `accept` |
| low | high | the original line has an effect, noise or extreme delivery; the `line` prompt carried over the extra | `reref` (`char` prompt) |
| high | low | an emotional line: the dub resembles its original but not the neutral reference | usually `accept` with a `note` |
| low | low | the voice "drifted" entirely | `regen`; on repeat `reref`; widespread for a character — a reference or casting problem |

## 4. Whose voice is it: `margin`, `closest_char`

- **How.** Every checked character and every selected or `main` character with at least two clean originals gets a voice centroid (the mean ECAPA embedding of up to 12 of their longest clean originals, the same lines as the baseline; cached in `cast/embeddings.npz`). `margin` = similarity of the dub to its own character's centroid − the best similarity to another character's centroid; `closest_char` is the character whose centroid is nearest.
- **The check:** `margin ≥ qa.min_identity_margin` (−0.05), otherwise the reason `identity`. A small negative margin is tolerated: a cross-language dub sits between voices more than an original does. On Low-Budget Repairs 27 of 30 dubs were closest to the hero's centroid among the main characters, and the clear failures were closest to someone else.
- **Same voice is not "another character".** A character whose centroid is at least `cast.merge_threshold` (0.75) similar to the own one counts as the same voice: a voice cluster that `cast` merged into the character (its lines are the character's lines anyway), a cluster left from an older cast, one actor in two roles. Such a character can be `closest_char` without failing the line.
- **No margin** (`null`) when fewer than two characters have centroids, or the line's own character has none (for example the mixed `unknown` group).
- **What it catches:** a dub that sounds like the sidekick instead of the hero, a gender swap towards another character's voice, a reference that belongs to the wrong character (then many lines of one character are closest to the same other character: a casting problem, not a synthesis one).

## 5. Lines flagged `nonverbal`

Screams, laughs, groans: their embedding says little about the timbre (the original scream itself may sit far from the character's centroid) and their transcript says nothing about the words. Such lines are measured and recorded, but never fail on `sim`, `identity` or `cer`: the row has `sim_skipped: "nonverbal"` and `cer_skipped: "nonverbal"`. Length, peaks and silence are still checked. If the dub of a scream sounds wrong, that is a listening question: decide `keep_original` when in doubt.

## 5a. Naturalness: `mos`, `orig_mos`

- **How.** Distill-MOS v7 (`qa.mos_model=distillmos`, the default of new projects) or UTMOS22 (`utmos22`, older projects and the fallback when Distill-MOS is missing) predicts a 1–5 naturalness score of the dub. It only runs when its checkpoint is downloaded (`babeldub setup --engines qc` fetches it). `orig_mos` is the median score of up to 8 longest clean originals of the same character by the same model (`qa/mos_baseline.json` records the model; measured once, again when the model changes; `qa --rebaseline` measures again). The two models' scales differ: compare MOS values only within one model.
- **Rule.** Reason `mos` when `mos < orig_mos − qa.mos_drop` (1.0). MOS depends on the voice, the language and the recording, so it is compared with the character's own originals and only clear outliers fail: a robotic, buzzing or broken take.
- **Where it goes wrong:** game recordings with effects (radio, megaphone) score low even when the dub is fine; short lines (< 1 s) score erratically.

## 5b. Intonation: `prosody_sim`, `f0_range_st`, `contour_corr`

- **How.** The qc worker tracks F0 (pyin) on the dub and on the original in two passes: the second searches only one octave either side of the character's median F0 (from `analyze`) or of a coarse first pass, so octave jumps no longer inflate the range (v0.3 saw 26–36 semitone "ranges" on originals); on the dub the search reaches two octaves above the character's median, so a take voiced an octave up shows as +12 semitones instead of its subharmonic. `f0_range_st` is the 10–90 % pitch range in semitones; `contour_corr` the correlation of the two voiced pitch contours (both resampled to 32 points; null when either is too short or their lengths differ more than twice); `prosody_sim` = 0.6 × range ratio (smaller / larger) + 0.4 × (`contour_corr` + 1) / 2.
- **Rule.** Reason `flat` when the original moves at least `qa.flat_min_orig_st` (3) semitones and the dub's range is below `qa.min_f0_range_ratio` (0.5) of it: the monotone "machine" read that the v0.2 baseline found in every group (the dub's range narrower than the original's).
- **Register: `pitch`.** `pitch_shift_st` = 12 · log2(`f0_median_hz` / `orig_f0_median_hz`). Reason `pitch` when both have at least `qa.pitch_min_voiced_s` (0.5 s) voiced and |shift| > `qa.max_pitch_shift_st` (3 semitones): the dub speaks in another register (an octave jump of the TTS, a man voiced like a boy). It is regenerated like `cer`; the same shift on all of a character's lines points at the reference or the RVC model.
- **Where it goes wrong:** creaky or whispered originals have little voiced pitch (then `pitch_shift_st` is null); a different sentence structure in the translation moves the stresses, so a low `contour_corr` alone is no defect (it never fails a line).

## 5b'. Channel: `ltas_db`

- **How.** The long-term average spectrum of the speech frames of the final take and of the original line in 1/3-octave bands (80 Hz – 16 kHz); `ltas_db` is the mean absolute band difference in dB after removing the level offset (`qa.metrics.ltas_distance_db`). About 1–3 dB: the same kind of channel; 5 dB and more: a clean studio take inside a radio, phone or band-limited line (or the other way round).
- **Rule.** None: it is informational and never fails a line. A high value on lines that sound "pasted in" is a reason to check `post.match` (`full` for baked-in radio/phone lines; skill `babeldub-voice`, `references/post.md` section 4), not to regenerate the take. The `post` row's `match.ltas_db` shows the same distance before and after the match.

## 5c. Timing of `fixed_length` lines

`post` places each chunk of a cutscene line on its original segment and records `onset_deltas` and `offset_deltas` (seconds). Reason `timing` when a chunk starts more than `qa.max_onset_s` (80 ms) off its segment or ends more than 80 ms after it. Ending early is fine. Usually the translation is too long for one segment: `shorten` after a failed `regen`.

## 6. Length: `ratio`

From `post/post.jsonl`: the speech duration of the dub after time compression divided by the speech duration of the original (without edge pauses). The limit is `qa.max_duration_ratio` (1.35), or 1.03 for `fixed_length`. A huge `ratio` (above 2) is usually the model looping, not a long translation. Details — skill `babeldub-voice`, `references/length-policy.md`.

## 7. Level: `peak_db`, clipping, silence

- A peak above `post.true_peak_db` + 0.5 dB (−0.5 dB by default) or more than 0.05 % clipped samples gives the reason `peak`. Usually a rare synthesis outlier: `regen`.
- Speech quieter than −50 dB gives the reason `silent`; a file shorter than 0.1 s gives `empty`. The model produced silence: `regen`.

## 8. Service reasons

| Reason | Meaning | What to do |
|---|---|---|
| `missing_file` | the `post` file is missing | `babeldub post`, then `babeldub qa` |
| `qc_error` | the `qc` worker could not process the file | rerun `babeldub qa`; if it repeats — the log and `troubleshooting.md` of the `babeldub-localize` skill |
| `no_dub_text` | the line has no translation | go back to translation |

The CLI makes no decisions for these reasons: the line goes into `attention[]`.

## 9. What the metrics do not see

Naturalness of intonation, correct stress in individual words (if ASR understood the word), whether the emotion fits, the mix with the music in the game, lip sync. A human checks these in the game; in the final report, name 3–5 lines of the main characters to listen to and ask the user to check the cutscenes.
