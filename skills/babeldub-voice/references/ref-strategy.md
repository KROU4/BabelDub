# Voice prompt strategy: dubbing in the original character's voice

Read this before the pilot, and whenever the metrics say "wrong voice", "the emotion is gone" or "there is an accent".

## 1. What a voice prompt is

BabelDub engines clone a voice zero-shot: the model is not trained on the character; it receives a sample of the character's speech, the **voice prompt**, and keeps speaking the same way, but with new text. VoxCPM2 (the default engine on 8 GB+ GPUs) takes two separate inputs: a **timbre reference** (`reference_wav_path`, no transcript needed) and an optional **continuation prompt** (`prompt_wav_path` + its exact `prompt_text`) whose language, pace and manner the model continues. Qwen3-TTS uses ICL mode: `ref_audio` (the audio) and `ref_text` (what is said in it); according to the authors, its audio tokenizer "fully preserves paralinguistic information", and the Base model has no style instructions: emotion and manner come only from the prompt.

Hence the main conclusion: **as the prompt, so the dub.** A calm reference produces a calm voice even for a shout. A shout in the prompt produces a shout.

Chatterbox (the engine for pl, tr and others) clones from the prompt audio only, without a transcript; emotion also comes from the audio, and its strength is additionally set by the `exaggeration` parameter.

## 2. Strategies

| `tts.ref_strategy` | Prompt | What carries over | When |
|---|---|---|---|
| `bootstrap` (`auto` with VoxCPM2) | the character's long original timbre reference, plus a target-language prompt from the character's accepted dubs once there are enough | the actor's timbre with native pronunciation in the target language | the default dubbing chain (section 2.2) |
| `line` | the original recording of this same line + its original text | timbre **and** emotion, pace, loudness of delivery, breathing, the manner of this exact phrase | Qwen3-TTS / Chatterbox: the main mode |
| `char` | the character's best clean reference (`cast/refs.jsonl`, `chosen`) + its text | the character's timbre in a neutral delivery | short lines, interjections, noise or music under the voice, no original text |
| `auto` (default) | VoxCPM2: `bootstrap`; other engines: `line` if the line is usable, otherwise `char` | — | always, unless there is a reason to change it |

`bootstrap` needs an engine with a continuation prompt (VoxCPM2); set with another engine it works as `auto` and `synth` says so in `notes`. With VoxCPM2, `line` and `char` give the model that recording as its timbre reference only.

The `auto` rule for Qwen3-TTS and Chatterbox. A line is usable for `line` if all of the following hold:

- there is a decoded original (`audio/orig/<uid>.wav`) and original text (`text.src`);
- the duration is at least `tts.line_ref_min_s` (1.6 s) and at most 15 s;
- there are no `has_bed` (music or noise under the voice) or `nonverbal` (laughter, a sigh, a wordless scream) flags.

Otherwise the character reference is used. If the character has no reference at all, the CLI still takes the short or noisy original so the line is not lost. If there is none either, the line goes to `skipped.no_ref` and `attention`.

The CLI trims a prompt longer than 15 s at its quietest point. The trimmed audio no longer matches its text, so such a prompt goes without a transcript (cloning from the voice embedding only, `x_vector_only` mode). That works worse, so for long lines prefer `char`.

### 2.1 ICL needs an exact transcript

ICL cloning reads `ref_text` as the literal content of `ref_audio`. If the transcript does not match the audio, the model loses its alignment and can loop: on Low-Budget Repairs one alternative take in ICL mode produced 55.9 s of a repeated syllable for a 3.8 s line, while the same take in timbre-only mode gave a perfect read (Whisper CER 0.0). The CLI protects against this in three ways:

1. **Alt takes and duplicates are not voiced.** `link` flags extra recordings of one subtitle key as `alt_take`/`duplicate`. Their audio often differs from the subtitle (an ad-lib, a cut, a different reading). Only the leader of the group (the take named exactly like the text key, else an unflagged take, else `_BEST`, else the first) goes through `synth`, `post` and `qa`; `build` gives every follower the leader's accepted dub. `synth` reports them as `skipped.follows`, `babeldub query --where follows!=null` lists them.
2. **ICL pre-check.** Before a `line` prompt is used, the CLI divides the syllables of the transcript by the speech seconds of the original. Outside 1.2–8.5 syllables per second the transcript cannot belong to that audio, so the line uses the character reference (`char`), or, if the character has none, the line in timbre-only mode (`line-xvec`). The decision is in `ref_reason` and `src_rate` of `synth/results.jsonl`.
3. **Runaway guard.** Every line gets its own generation cap from the longer of the estimated dub length and the original. A take that hits the cap or is far longer than plausible is discarded and the line is retried alone: timbre-only on the same prompt (`line-xvec`), then the character reference in ICL mode (`char`). The kept row says `retry_reason: "runaway"` and lists the `discarded` takes; if all tries loop, the row fails with `error: "runaway"` and the next `synth` tries a new seed. Takes that ran to the cap in runs before this guard (`hit_token_limit` on an `ok` row) are not used: the next `synth` voices those lines again and `post` skips them.

Timbre-only mode keeps the voice but loses some of the delivery of the exact phrase, so it is a fallback, not a default.

### 2.2 Bootstrap: the original timbre, then the character's own target-language voice

A reference in the original language makes the target-language speech sound like a foreigner speaking it (section 6). The bootstrap removes the accent without losing the actor:

1. **Long timbre references per arousal bucket.** `analyze` measured the prosody of every original (RMS level, F0 range, syllable rate). Relative to the character's own medians, each line is `calm`, `animated` (wider pitch range, faster), `loud` (louder, shouted) or `soft` (quieter, subdued); with the opt-in emotion model an emotion label moves a line prosody calls calm (sad → soft, angry → loud, happy/surprised/fearful → animated). Per character and bucket, 3–4 clean original lines of that bucket (4–7.5 s each, no flags, the cast references first; relaxed to 2–12 s) are joined with 0.35 s gaps into ~15–20 s: `synth/prompts/<char_id>/src-<hash>.wav` (calm) and `src-<bucket>-<hash>.wav`. Calm always exists (from all clean lines when the calm ones are too few); another bucket needs at least 8 s of clean material, else its lines use the nearest existing bucket. Each line gets the reference of the bucket nearest to its original (`bucket` in `synth/results.jsonl`). Without `analyze` every line is calm. The `unknown` group has none and uses `line`/`char`.
2. **Phase 1.** The first `tts.bootstrap_lines` (4) lines of the character — plain lines with text of 2.5–8 s first, in game order — are dubbed with the long reference alone (`ref_mode: bootstrap-src`). The other lines wait (`skipped.bootstrap_wait`; `synth` and `status` say to run `post`, `vc`, `qa` and `synth` again).
3. **Target-language prompt bank.** When QA accepted at least `tts.bootstrap_min_accepted` (3) lines of the character with CER within `qa.max_cer`, each bucket gets a 5–10 s prompt of 1–2 accepted dubs of lines from that bucket, taken from their final audio (after `vc` when the character has an RVC model) with the exact text they were synthesized from: `synth/prompts/<char_id>/tgt.json` (`bank`) + `tgt-<bucket>-<hash>.wav`. Dubs are ranked by naturalness (MOS) and by how well their intonation follows their original (`prosody_sim` of QA); CER is only a gate. The v0.1 rule "lowest CER first" picked the flattest, most synthetic-sounding lines and made the whole character monotone. A bucket's prompt is frozen once built; buckets without accepted dubs are added on a later run.
4. **Phase 2.** The remaining lines, and regenerated phase-1 lines, are dubbed with the long reference **and** the prompt of the bucket nearest to their original (`ref_mode: bootstrap-tgt`, `prompt_id` and `bucket` in `synth/results.jsonl`). A line QA sent back as `flat` or `mos` is regenerated with the next nearest bucket.
5. If every phase-1 line is settled (checked, `keep_original`/`skip`, or out of attempts) and fewer than 3 were accepted, the rest goes on with the long reference only; `ref_reason` says so. Look at such a character first: its references or texts are usually the problem.

To rebuild the prompts (for example after retraining the RVC model), delete `synth/prompts/<char_id>/tgt.json` and run `synth --force --chars <id>`.

### 2.3 Line classes and short words

- **Interjections and nonverbal lines** ("Hehe…", "Hmm.", a groan, `[laughs]`) are not translated or voiced: a cloned laugh sounds fake, the original is better. `synth` reports `skipped.keep_original` and writes an automatic `keep_original` decision. To dub one anyway, write an agent/human decision other than `keep_original` for it and translate it with `batch make --ids`.
- **Short words** ("Freeze!", "Yes, Chief!": less than 1.2 s of speech) carry almost no timbre in a long reference and lose their punch: the line itself is the reference (VoxCPM2: `reference_wav_path` = the original line, no target-language prompt), 6 candidates (`tts.short_word_best_of`), one regen at most (`tts.short_word_attempts` = 2), then the original is kept.
- **Mixed lines** (words with a laugh or groan) are voiced; QA accepts them with attention so a person listens whether the nonverbal part survived.

## 3. Why the original line as a prompt carries the emotion

1. **The context matches.** The prompt line is the same phrase in the same scene: the same emotional temperature, distance to the microphone, fatigue, smile in the voice. The model continues exactly that state.
2. **The pace matches.** The speaking rate and pauses of the prompt carry over into the synthesis. So a `line` dub naturally fits the duration of the original, while a neutral reference often gives a "narrator" pace.
3. **The transcript aligns the prompt.** `ref_text` tells the model which sounds in the prompt are speech and which are delivery. A wrong transcript (the line was rewritten, the subtitle text does not match the voice-over, an alternative take) confuses the model: artefacts, cut-offs, repetitions and endless loops appear. That is why `line` requires the original text, and lines whose text diverges from the audio are voiced with `char` or timbre-only (section 2.1).
4. **The translation must match the emotional shape.** The prompt sets the intonation; if the translation is calmer than the original (Russian example: «Стой!» ("Stop!") → «Пожалуйста, остановитесь» ("Please stop")), the model pulls the phrase in two directions. That is why the translation rules (skill `babeldub-translate`) require keeping exclamations, questions, hesitations and cut-offs.

Other dubbing pipelines do the same (SVR_TTS, YouDub, VideoLingo): the reference is the original line itself.

## 4. When `char` is needed

| Sign | Why `line` is bad | What to do |
|---|---|---|
| the line is shorter than 1.6 s ("Yes", "Hey!", "Hmm") | too little audio: the timbre is unstable, the model "guesses" the voice | `auto` picks `char` itself |
| `has_bed` flag, music or ambience under the voice | the model clones the background too: noise, music, hum | `auto` picks `char` itself; v0.1 has no voice/background separation |
| `nonverbal` (laughter, wordless scream, breathing) | nothing to align with the text | `auto` picks `char` itself |
| no original text | no transcript for ICL | `char`; in v0.1 such lines are usually not translated at all |
| an effect in the original: radio, phone, echo, distortion | the effect becomes part of the timbre and does not match other lines | `reref` for these lines, or `char` for the character (section 6) |
| shouting or whispering from a character with few lines | extreme delivery distorts the timbre | `reref` (switch to `char`) by QA decision |

`babeldub cast` picks the `char` reference: an original of 3–12 s with known text, without the `has_bed`/`nonverbal`/`alt_take` flags, without clipping and not quiet, closest to the character's "typical" voice (the embedding centroid, if the `qc` worker is installed). The caster role checks that the reference text is neutral (no shouting, whispering or laughter). Details: skill `babeldub-cast`.

## 5. How `reref` works

The QA decision `reref` changes the prompt for a specific line on the next `babeldub synth --attempt-next`:

- the attempt used `line` or `line-xvec` → the next one uses `char`;
- it used `char` → another character reference (if there is more than one), otherwise the line's original.

There is no need to touch the global `tts.ref_strategy` setting.

## 6. Accent in cross-lingual cloning

A prompt in the original language (English, Polish) carries not only the voice but also the **phonetics of its language**. When synthesizing in another language, the model may carry over an accent: an "English" r, vowel reduction, intonation contours. The listening rounds on Low-Budget Repairs confirmed it: the per-language reference matters. With VoxCPM2 the bootstrap (section 2.2) is the remedy; with Qwen3-TTS and Chatterbox the steps below apply. The accent is checked in the pilot.

How to recognize it from metrics (you cannot hear audio):

- `cer` rises for all of a character's lines while `sim` is normal: the model pronounces the target language "with an accent", and ASR makes mistakes;
- in the QA report (`babeldub report`), the character has a high median CER with normal voice similarity.

What to do, in order:

1. **Check the text.** A high CER is most often not an accent but Latin script, numbers in digits, abbreviations, rare names. That calls for `retranslate` and `pronounce` in the glossary (skill `babeldub-translate`).
2. **Stress for Russian.** VoxCPM2 and Qwen3-TTS Base do not read the U+0301 stress mark: they turn it into extra sounds (and VoxCPM2 reads `+` as the word "plus"), so the CLI strips marks from the text for every model except a stress model. Fix a wrong stress by rewording or with the glossary `pronounce` (the CLI puts it into the dub text; a stress mark in it reaches the engine in the form the engine reads). An A/B on VoxCPM2 found a CAPITAL stressed vowel nearly free (+0.02 CER) and plain text best overall: `babeldub set tts.stress=on` (off by default) marks only homographs with RUAccent (qc env) as capital vowels; try it on the pilot and keep it only when the stress errors are real. Only the Qwen3-TTS fine-tune `qwen3-tts-12hz-1.7b-ru-stress-cf` follows the mark, but it preserved the original hero's voice noticeably worse, so it is neither installed nor selected automatically: enable it only for widespread stress errors and after a pilot that checks `sim` (`babeldub models fetch qwen3-tts-12hz-1.7b-ru-stress-cf`, `babeldub set tts.engine=qwen3tts-fast tts.model=qwen3-tts-12hz-1.7b-ru-stress-cf`).
3. **Compare prompt languages.** If the game is voiced in several languages (Low-Budget Repairs has en-US and pl-PL), run the pilot on both: the accents of different languages sound different in the target language. In v0.1 the CLI takes the originals of the locale that `extract` extracted; comparing languages is a manual experiment, and its outcome is recorded in the report.
4. **`char` with a more neutral delivery.** A calm reference usually carries less "foreign" intonation than an emotional line. For a character with a strong accent you can switch to `char` at the cost of some emotion; this is a trade-off and must be named in the report.
5. **For Chatterbox** with a prompt in another language, the authors recommend `cfg_weight=0` to pull the accent less. The v0.1 worker does this itself when the prompt language differs from the target. Chatterbox's emotion strength is set by `exaggeration` (default 0.5); engine parameters are passed through `tts.params` in `project.json`: `babeldub set tts.params.exaggeration=0.7`. Do not edit `project.json` by hand.

Two former ideas are now the default chain: the character's first accepted target-language lines as the prompt (the bootstrap, section 2.2) and a voice-conversion post-pass (RVC per main character, stage `vc`, which also pulls the timbre back to the original after the prompt). Zero-shot VC (Seed-VC) was rejected: it hallucinated words and sounds. Embedding-only mode (`x_vector_only`) as a remedy for accent is not verified; do not apply it without an explicit decision by the user.

## 7. How QA checks that the voice is the same

- `sim_line` is the cosine similarity of ECAPA embeddings between the dub and the original line; `sim_char` is between the dub and the character reference; `sim` is their mean.
- `sim_ref` is the character's baseline: the mean pairwise similarity of the character's own clean original lines (up to the 12 longest). Absolute similarity values differ from voice to voice, and cross-lingual similarity is lower than same-language similarity, so the comparison is made against this baseline.
- Threshold: `sim ≥ max(ratio × sim_ref, qa.min_sim_abs)` with ratio `qa.min_sim_ratio_xlang` (0.45) for a dub in another language than the original audio, `qa.min_sim_ratio` (0.75) for a same-language dub, floor 0.25; the identity margin also fails a dub closer to another character's voice than to its own (reason `identity`). Below the threshold, the reason is `sim`: the CLI auto-decides `regen` until attempts run out, then `keep_original`. The qa role may assign `reref` instead if the defect looks like a prompt problem (shouting, noise, an effect in the original).

Details: skill `babeldub-qa`.
