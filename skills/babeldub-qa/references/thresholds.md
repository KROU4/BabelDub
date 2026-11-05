# QA thresholds: values, calibration, when to change them

Read this when there are suspiciously many or suspiciously few defects, and after the pilot.

## 1. Default values

| Setting (`project.json`) | Value | What it checks |
|---|---|---|
| `qa.max_cer` | 0.15 | intelligibility: share of character errors in recognition (the lowest of Whisper, Whisper with Russian vowels folded and, for Russian, GigaAM) |
| `qa.max_asr_disagreement` | 0.15 | Russian: Whisper's and GigaAM's CER differ more than this → `asr_disagree` (informational, never a failure) |
| `qa.min_sim_ratio_xlang` | 0.45 | voice similarity of a dub in another language than the original audio (the normal case): `sim ≥ 0.45 × sim_ref` |
| `qa.min_sim_ratio` | 0.75 | voice similarity of a same-language dub (`source_lang` = `target_lang`): `sim ≥ 0.75 × sim_ref` |
| `qa.min_sim_abs` | 0.25 | absolute floor: `sim_threshold = max(ratio × sim_ref, 0.25)` |
| `qa.short_sim_s` | 1.0 | an original with less speech than this (or below the bar itself) also passes a dub relatively ... |
| `qa.short_sim_ratio` | 0.8 | ... `sim_rel` (dub cosine to the target / `orig_sim`, the original's own) ≥ 0.8 (`sim_rule: relative`; `orig_sim` ≤ 0.05 keeps the absolute rule) |
| `qa.min_identity_margin` | −0.05 | identity: similarity to the own character's centroid minus the best other character's |
| `cast.merge_threshold` | 0.75 | two characters this similar in voice count as one voice in the identity check (and `cast` merges such clusters) |
| `qa.max_duration_ratio` | 1.35 | length of ordinary lines |
| `post.fixed_length_tolerance` | 0.03 | length of `fixed_length` lines (cutscenes, video): `ratio ≤ 1.03` |
| `post.true_peak_db` | −1.0 | peak ceiling; QA allows another 0.5 dB |
| `qa.silence_db` | −50 | below this the line counts as silence |
| `tts.max_attempts` | 3 | how many synthesis attempts before `keep_original` |
| `tts.short_word_attempts` | 2 | attempts of a `short_word` line ("Freeze!") before `keep_original` |
| `qa.mos_drop` | 1.0 | naturalness: `mos ≥ orig_mos − 1.0` (the median MOS of the character's originals, same model); also the MOS gate of the scored best-of |
| `qa.mos_model` | `distillmos` (new projects), `utmos22` (older ones) | which MOS model; the baseline is measured again when it changes, since the scales differ |
| `qa.min_f0_range_ratio` | 0.5 | monotony: the dub's F0 range ≥ 0.5 × the original's ... |
| `qa.flat_min_orig_st` | 3.0 | ... when the original moves at least 3 semitones |
| `qa.max_pitch_shift_st` | 3.0 | register: the dub's median F0 within ±3 semitones of the original line's (`pitch`) ... |
| `qa.pitch_min_voiced_s` | 0.5 | ... when both have at least 0.5 s of voiced speech |
| `qa.rank_weights` | prosody 0.25, mos 0.10, liveliness 0.20, length 0.15, sim 0.30, sim_rvc 0.05, length_fixed 0.30 | the scored best-of (not a QA check): which candidate take wins |
| `qa.max_onset_s` | 0.08 | `fixed_length`: chunk onset within ±80 ms of the original, end at most 80 ms late |

Lines flagged `nonverbal` (screams, laughs) never fail on similarity, identity or CER (`sim_skipped`, `cer_skipped` in `qa/qa.jsonl`), nor on MOS or prosody. The MOS and prosody thresholds are deliberately loose: they compare a dub with its character's own originals and flag only clear outliers; tighten them only after listening.

The CER, length and level values are starting points that nobody has checked on the voices of a particular game. The research BabelDub is built on also proposed stricter targets for Russian (CER up to 0.08 is "good", up to 0.20 is "regenerate"), but those are marked as needing calibration too. The similarity values are calibrated on one real run (below); treat them as a sound default, not as the truth for every engine and language pair.

### Calibration evidence for the voice checks

Low-Budget Repairs, a Russian Qwen3-TTS dub of 30 English prologue lines of the hero Zbyszek, ECAPA embeddings from the `qc` worker:

| Measurement | Value |
|---|---|
| Zbyszek's `sim_ref` (English originals against each other) | 0.76 |
| old bar, same-language ratio: 0.75 × 0.76 | 0.57 — failed 27 of 30 dubs on `sim` while the Whisper CER median was 0.0 |
| good dubs against Zbyszek's voice centroid | 0.38–0.66 |
| clear failures | 0.08–0.11, and closest to another character |
| borderline | 0.18–0.24; one is a scream flagged `nonverbal` whose own original scores only 0.09 against the centroid |
| new bar: 0.45 × 0.76, floor 0.25 | 0.34 |
| dubs closest to Zbyszek's centroid among the main characters (once his voice clusters were merged into him) | 27 of 30 |
| cluster centroid vs the metadata character: zbyszek ~ cluster_04, maciek ~ cluster_05 | 0.88, 0.89 (every other pair 0.06–0.38) |

Cross-language similarity is always lower than the same-language baseline, so the ratio for a dub into another language is lower; the floor and the identity margin keep a "foreign" voice from slipping through.

Mind the definitions when you compare similarity numbers: `sim` and `sim_ref` are line-to-line cosines, while "against the centroid" values (the good-dub range above, `sim_ref_loo` in `qa/baseline.json`) run higher because a centroid averages away each line's noise. Never put a centroid number next to `sim_ref` as if it were the same scale.

## 2. Calibration on the pilot

After the pilot (skill `babeldub-voice`, step 3), look at the metric distribution of the main characters:

```text
babeldub query --where char=<id> --fields id,qa.cer,qa.sim,qa.sim_ref,qa.sim_threshold,qa.margin,qa.closest_char,qa.ratio,verdict --limit 50
babeldub report
```

- **CER.** If lines whose `asr_text` matches `dub_text` in meaning (differences in names and numbers) systematically score CER above the threshold, the threshold is too strict for this game (many names and terms). Raise it: `babeldub set qa.max_cer=0.2`.
- **Similarity.** If almost all of a character's lines fail similarity, and `sim_line` and `sim_char` behave the same, check the reference and the attribution (casting) first, and only then the threshold. Check `sim_ratio` in the rows too: 0.75 on a dub into another language means `source_lang` is set to the target language by mistake. For a cross-language dub, lowering `min_sim_ratio_xlang` below 0.35 or `min_sim_abs` below 0.2 means letting "foreign" voices through; for a same-language dub the same holds for `min_sim_ratio` below 0.65.
- **Identity.** If many lines of one character fail `identity` with the same `closest_char`, that other character is not a synthesis accident: the reference or the attribution is wrong (skill `babeldub-cast`), or the two characters are one actor (then raise nothing; the identity check treats voices at least `cast.merge_threshold` similar as one voice). Loosen `min_identity_margin` (e.g. −0.1) only when the characters' voices are close by nature.
- **Register.** `pitch` failures across one character with shifts of about ±12 semitones mean the TTS jumps an octave for this voice (regens usually fix it); a steady shift of 3–5 semitones on every line of a character means its reference or RVC model is in the wrong register: check the reference first. Loosen `qa.max_pitch_shift_st` (e.g. 4) only for deliberately stylised voices.
- **Short lines.** `sim_rule: relative` rows pass when they reach the absolute bar or `sim_rel` ≥ `qa.short_sim_ratio` (`sim_rel` is already the ratio to the original's own similarity `orig_sim`). If short barks still fail, look at how far `sim_rel` is below `qa.short_sim_ratio` before touching the absolute bar.
- **Length.** If lines in the game are tightly bound to timing (synchronous instruments, the next dialogue starts on a timer), tighten it: `babeldub set qa.max_duration_ratio=1.15`.

Put every threshold change, with its reason, into the final report.

## 3. After changing a threshold

- The new threshold applies to subsequent checks. To recheck attempts that were already checked, run `babeldub qa --force` (the stage flag from the common `--force` set). It recomputes the metrics and auto-decisions; the CLI does not overwrite agent decisions for the same attempt.
- The `sim_ref` baseline is cached in `qa/baseline.json` and does not depend on thresholds. The voice centroids for the identity check are recomputed on every run from the cached embeddings (`cast/embeddings.npz`).

## 4. Signs that the threshold is not the problem

| Observation | Most likely | Where to go |
|---|---|---|
| defects for one character, none for the others | a bad reference, an attribution error, dirty originals | the voice and caster roles, skill `babeldub-cast` |
| `identity` defects of one character, all closest to the same other character | the reference or the lines belong to that other character | skill `babeldub-cast`: check the references and the merged clusters in the casting sheet |
| CER defects equal across all characters | the text: numbers as digits, Latin script, abbreviations | `retranslate`, glossary (`pronounce`) |
| widespread length defects | the translation is longer than the original | syllable budget, `adapt` mode, `shorten` |
| high CER only on lines under 1 s | ASR is unreliable on short audio | `accept` with a `note` if `asr_text` is close in meaning |
| `asr_disagree` with a low `cer_asr2` and a high Whisper CER | Whisper misheard (accent, a name), the words are right | nothing: the gate already uses the lower CER; listen if unsure |
| low similarity on all lines of one engine | the engine clones this language or language pair poorly | skill `babeldub-voice`, the section on accent |
