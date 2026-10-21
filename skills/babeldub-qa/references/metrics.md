# QA metrics: what they mean and where they go wrong

Read this the first time you review defects. The metrics are computed by the `qc` worker (environment `BABELDUB_HOME/envs/qc`) from the latest `post/audio/<uid>.wav` file of each line; the result is a row in `qa/qa.jsonl`.

## 1. Fields of `qa/qa.jsonl`

`id`, `attempt`, `char_id`, `wav`, `asr_text`, `cer`, `cer_skipped`, `sim`, `sim_line`, `sim_char`, `sim_ref`, `sim_ratio`, `sim_threshold`, `sim_skipped`, `margin`, `closest_char`, `ratio`, `dur_s`, `peak_db`, `verdict` (`pass` | `fail`), `reasons[]`, `ts`. Inspect them with `babeldub query`; do not read the file whole. In `query` these fields are available with the `qa.` prefix (`--fields id,qa.cer,qa.sim,qa.reasons`), while `verdict` and `decision` use short names in `--where`.

## 2. Intelligibility: `cer`

- **How.** ASR transcribes the dub in the target language (in the v0.1 catalog — faster-whisper large-v3-turbo); CER is the share of character edits between the normalized `dub_text` and the recognized text. Normalization: lower case, no punctuation or markup, «ё» → «е», digits stay digits.
- **What it catches:** skipped and repeated words, model hallucinations (extra phrases), "swallowed" word endings, wrong readings (Latin script, numbers), a strong accent, silence instead of speech.
- **Where it goes wrong:**
  - ASR makes mistakes too, especially on short lines («Эй!», «Хм»): one wrong character out of three is already CER 0.33;
  - numbers: if a digit stayed in `dub_text` and the model spoke it as words, ASR returns words or digits at its own discretion — CER grows without a real defect;
  - ASR spells names and invented game words as it hears them («Збышек» → «збышик»);
  - placeholders stay in `dub_text` (the CLI strips them only before synthesis), but they are not in the speech;
  - ASR recognizes interjections and emotional exclamations poorly;
  - Whisper invents text on silence or noise ("Продолжение следует...", "Subtitles by..."). The worker runs it with a voice-activity filter and without conditioning on previous text, which removes most such tails; if `asr_text` still ends with a phrase that is not in `dub_text` while the rest matches, it is an ASR artefact, not a dub defect.
- **How to read it:** compare `dub_text` and `asr_text` by eye. If the difference is in a name or a number and the rest matches, it is not a pronunciation defect: `accept` with a `note`, or `retranslate` (numbers as words). If ASR returned completely different text or only half of it, it is a real defect: `regen`.

For Russian, the BabelDub research recommends GigaAM-v3 as more accurate recognition; v0.1 does not have it — it is a candidate for the next version.

## 3. Is it the same voice: `sim`, `sim_line`, `sim_char`, `sim_ref`

- **How.** SpeechBrain ECAPA (voxceleb) voice embeddings for the dub, for the original recording of this line and for the character's main reference; cosine similarity.
  - `sim_line` — the dub against its own original (if the original is clean: at least 1 s long, without the `has_bed`, `nonverbal`, `alt_take`, `no_audio` flags);
  - `sim_char` — the dub against the character's reference (if the reference is not this same line);
  - `sim` — the mean of whatever could be computed.
- **The `sim_ref` baseline.** How similar the character's own original lines are to each other: the mean pairwise similarity of up to 12 of their longest clean originals. If there are fewer than two clean originals — the median over other characters, and without it — 0.5. Cache — `qa/baseline.json`.
- **Why relative to the baseline, and why two ratios.** Absolute similarity values differ between voices, so the bar follows the character's baseline: `sim ≥ sim_threshold = max(sim_ratio × sim_ref, qa.min_sim_abs)`. The baseline is measured within one language (English originals against each other), while a dub crosses a language boundary (Russian dub against the English original), and ECAPA always scores that lower. So a dub in another language than the original audio (`source_lang` ≠ `target_lang`, the normal case) uses `qa.min_sim_ratio_xlang` = 0.45; a same-language dub keeps `qa.min_sim_ratio` = 0.75. The absolute floor `qa.min_sim_abs` = 0.25 stops a character with a low baseline from letting anything through. Each row records the ratio it used (`sim_ratio`) and the resulting bar (`sim_threshold`).
- **Calibration evidence** (Low-Budget Repairs, Russian Qwen3-TTS dub of 30 English lines of the hero, ECAPA): the hero's `sim_ref` was 0.76, so the same-language bar was 0.75 × 0.76 = 0.57, and it failed 27 of 30 dubs on `sim` although the Whisper CER median was 0.0. Measured against the hero's voice centroid, good dubs scored 0.38–0.66, clear failures 0.08–0.11 (and were closest to another character), borderline lines 0.18–0.24 (one of them a scream flagged `nonverbal` whose own original scores only 0.09 against the centroid). With 0.45 × 0.76 = 0.34 and the 0.25 floor, the good dubs pass and the failures do not.
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
