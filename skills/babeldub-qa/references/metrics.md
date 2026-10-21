# QA metrics: what they mean and where they go wrong

Read this the first time you review defects. The metrics are computed by the `qc` worker (environment `BABELDUB_HOME/envs/qc`) from the latest `post/audio/<uid>.wav` file of each line; the result is a row in `qa/qa.jsonl`.

## 1. Fields of `qa/qa.jsonl`

`id`, `attempt`, `char_id`, `wav`, `asr_text`, `cer`, `sim`, `sim_line`, `sim_char`, `sim_ref`, `ratio`, `dur_s`, `peak_db`, `verdict` (`pass` | `fail`), `reasons[]`, `ts`. Inspect them with `babeldub query`; do not read the file whole. In `query` these fields are available with the `qa.` prefix (`--fields id,qa.cer,qa.sim,qa.reasons`), while `verdict` and `decision` use short names in `--where`.

## 2. Intelligibility: `cer`

- **How.** ASR transcribes the dub in the target language (in the v0.1 catalog — faster-whisper large-v3-turbo); CER is the share of character edits between the normalized `dub_text` and the recognized text. Normalization: lower case, no punctuation or markup, «ё» → «е», digits stay digits.
- **What it catches:** skipped and repeated words, model hallucinations (extra phrases), "swallowed" word endings, wrong readings (Latin script, numbers), a strong accent, silence instead of speech.
- **Where it goes wrong:**
  - ASR makes mistakes too, especially on short lines («Эй!», «Хм»): one wrong character out of three is already CER 0.33;
  - numbers: if a digit stayed in `dub_text` and the model spoke it as words, ASR returns words or digits at its own discretion — CER grows without a real defect;
  - ASR spells names and invented game words as it hears them («Збышек» → «збышик»);
  - placeholders stay in `dub_text` (the CLI strips them only before synthesis), but they are not in the speech;
  - ASR recognizes interjections and emotional exclamations poorly.
- **How to read it:** compare `dub_text` and `asr_text` by eye. If the difference is in a name or a number and the rest matches, it is not a pronunciation defect: `accept` with a `note`, or `retranslate` (numbers as words). If ASR returned completely different text or only half of it, it is a real defect: `regen`.

For Russian, the BabelDub research recommends GigaAM-v3 as more accurate recognition; v0.1 does not have it — it is a candidate for the next version.

## 3. Is it the same voice: `sim`, `sim_line`, `sim_char`, `sim_ref`

- **How.** SpeechBrain ECAPA (voxceleb) voice embeddings for the dub, for the original recording of this line and for the character's main reference; cosine similarity.
  - `sim_line` — the dub against its own original (if the original is clean: at least 1 s long, without the `has_bed`, `nonverbal`, `alt_take`, `no_audio` flags);
  - `sim_char` — the dub against the character's reference (if the reference is not this same line);
  - `sim` — the mean of whatever could be computed.
- **The `sim_ref` baseline.** How similar the character's own original lines are to each other: the mean pairwise similarity of up to 12 of their longest clean originals. If there are fewer than two clean originals — the median over other characters, and without it — 0.6. Cache — `qa/baseline.json`.
- **Why relative to the baseline.** Absolute similarity values differ between voices, and similarity across a language boundary (English original — Russian dub) is always lower than within one language. Hence the rule: `sim ≥ qa.min_sim_ratio × sim_ref`.
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

## 4. Length: `ratio`

From `post/post.jsonl`: the speech duration of the dub after time compression divided by the speech duration of the original (without edge pauses). The limit is `qa.max_duration_ratio` (1.35), or 1.03 for `fixed_length`. A huge `ratio` (above 2) is usually the model looping, not a long translation. Details — skill `babeldub-voice`, `references/length-policy.md`.

## 5. Level: `peak_db`, clipping, silence

- A peak above `post.true_peak_db` + 0.5 dB (−0.5 dB by default) or more than 0.05 % clipped samples gives the reason `peak`. Usually a rare synthesis outlier: `regen`.
- Speech quieter than −50 dB gives the reason `silent`; a file shorter than 0.1 s gives `empty`. The model produced silence: `regen`.

## 6. Service reasons

| Reason | Meaning | What to do |
|---|---|---|
| `missing_file` | the `post` file is missing | `babeldub post`, then `babeldub qa` |
| `qc_error` | the `qc` worker could not process the file | rerun `babeldub qa`; if it repeats — the log and `troubleshooting.md` of the `babeldub-localize` skill |
| `no_dub_text` | the line has no translation | go back to translation |

The CLI makes no decisions for these reasons: the line goes into `attention[]`.

## 7. What the metrics do not see

Naturalness of intonation, correct stress in individual words (if ASR understood the word), whether the emotion fits, the mix with the music in the game, lip sync. A human checks these in the game; in the final report, name 3–5 lines of the main characters to listen to and ask the user to check the cutscenes.
