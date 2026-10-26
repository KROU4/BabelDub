# Voice prompt strategy: dubbing in the original character's voice

Read this before the pilot, and whenever the metrics say "wrong voice", "the emotion is gone" or "there is an accent".

## 1. What a voice prompt is

BabelDub engines clone a voice zero-shot: the model is not trained on the character; it receives a sample of the character's speech, the **voice prompt**, and keeps speaking the same way, but with new text. VoxCPM2 (the default engine on 8 GB+ GPUs) takes a **timbre reference** (`reference_wav_path`, no transcript needed), an optional **continuation prompt** (`prompt_wav_path` + its exact `prompt_text`) whose language, pace and manner the model continues, and an optional short English **control text** in parentheses before the text it speaks: `(shouting, fast pace)Вон из моего гаража!`. It reads a control text only in timbre-only mode, so the CLI never sends a control text and a continuation prompt together. Qwen3-TTS uses ICL mode: `ref_audio` (the audio) and `ref_text` (what is said in it); according to the authors, its audio tokenizer "fully preserves paralinguistic information", and the Base model has no style instructions: emotion and manner come only from the prompt.

Hence the main conclusion: **as the prompt, so the dub.** A calm reference produces a calm voice even for a shout, unless a control text asks for the shout (VoxCPM2, section 2.2). A shout in the prompt produces a shout.

Chatterbox (the engine for pl, tr and others) clones from the prompt audio only, without a transcript; emotion also comes from the audio, and its strength is additionally set by the `exaggeration` parameter.

## 2. Strategies

| `tts.ref_strategy` | Prompt | What carries over | When |
|---|---|---|---|
| `tags` (`auto` with VoxCPM2) | the character's long original timbre reference, plus a short delivery control text from the line's delivery tags (measured on the original or set by the translator) | the actor's timbre for every line; the delivery of this line (shouting, whispering, calm, fast…) | the default dubbing chain (section 2.2) |
| `bootstrap` (explicit) | the character's long original timbre reference, plus a target-language prompt from the character's accepted dubs once there are enough | the actor's timbre with native pronunciation in the target language | VoxCPM2 when the pilot shows an accent (sections 2.3 and 6); the default of v0.2 |
| `line` | the original recording of this same line + its original text | timbre **and** emotion, pace, loudness of delivery, breathing, the manner of this exact phrase | Qwen3-TTS / Chatterbox: the main mode |
| `char` | the character's best clean reference (`cast/refs.jsonl`, `chosen`) + its text | the character's timbre in a neutral delivery | short lines, interjections, noise or music under the voice, no original text |
| `auto` (default) | VoxCPM2: `tags`; other engines: `line` if the line is usable, otherwise `char` | — | always, unless there is a reason to change it |

`tags` needs an engine that reads a control text and `bootstrap` an engine with a continuation prompt (both: VoxCPM2); set with another engine they work as `auto` and `synth` says so in `notes`. With VoxCPM2, `line` and `char` give the model that recording as its timbre reference only. An explicit `bootstrap`, `line` or `char` never sends delivery tags.

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

### 2.2 Tags: the original timbre plus the delivery of the line (the VoxCPM2 default)

Every line is voiced with the character's long original reference, which carries the timbre, and a short English control text, which carries how this line is delivered. Chosen in blind listening on the 38-line test set of Low-Budget Repairs (English → Russian): the long reference with delivery tags was preferred over v0.2's target-language prompt bank (section 2.3). The metrics were slightly lower than for the same long reference without tags (CER with numbers spelled out 0.030 vs 0.020, UTMOS 2.73 vs 2.81, single raw takes): a control phrase can cost some intelligibility, so a regen after a `cer` failure drops it (item 6).

1. **The long timbre reference.** 3–4 clean original lines of the character (4–7.5 s each, no flags, the cast references first; relaxed to 2–12 s) joined into ~15–20 s (`synth/prompts/<char_id>/src-*.wav`, the same files the bootstrap uses), never cut, without a transcript and without a target-language prompt (`ref_mode: tags`). `tts.tags_reference`:
   - `main` (default): the calm/main reference for every line, so the timbre of the role stays the same and the tags alone change the delivery;
   - `bucket`: the reference of the arousal bucket nearest to the line's original (calm, animated, loud, soft; `bucket` in `synth/results.jsonl`), for a character whose shouts or whispers sound like a different timbre; a bucket needs ≥ 8 s of clean material, else the nearest existing one is used.
2. **Delivery tags.** At most 3 per line from a fixed vocabulary; each tag puts one English phrase into the control text, joined by ", ":

   | Tag | Phrase | | Tag | Phrase |
   |---|---|---|---|---|
   | `shouting` | shouting | | `soft` | soft voice |
   | `whispered` | whispering | | `loud` | loud voice |
   | `urgent` | urgent | | `quick` | fast pace |
   | `energetic` | energetic | | `slow` | slow pace |
   | `calm` | calm | | `expressive` | expressive intonation |
   | `sad` | sad | | `flat` | flat intonation |

3. **Measured tags.** `analyze` measured every original (F0 median, loudness, voiced share, pitch range, syllable rate). Each line is compared with the **same character's other lines**: a robust z-score (median and MAD, leave-one-out) per feature; the pitch range and the rate count only on lines with ≥ 1.5 s of speech. Only strong deviations become tags, for example `shouting` = pitch ≥ 2 and loudness ≥ 1 above the character's norm, `whispered` = loudness ≤ −2 with little or no voicing, `calm` = clearly lower arousal; a moderate `quick`/`slow` reads "slightly faster"/"slightly slower". Most lines get no tags: they are the character's normal delivery, which the reference already carries. A line gets no measured tags without its `analyze` row, when its character has fewer than 5 other comparable lines, for `nonverbal` lines, lines under 0.3 s of speech and the `unknown` group. The tags are computed whenever needed and never stored, so a new `analyze` or `cast` changes them.
4. **Agent tags.** Every translation batch line carries the measured tags as `delivery_hint`; the translator looks at the scene and returns `delivery` only when it disagrees (sarcasm read calm, crying, a whisper inside a loud scene; skill `babeldub-translate`, `references/batch-format.md`). Nobody asks the user about delivery.
5. **Precedence.** The `delivery` of the line's current translation, when present (even `[]`, which means explicitly neutral) > the measured tags > none. A `delivery` that repeats the measured tags exactly is the measured delivery (`tags_source: measured`, a moderate `quick`/`slow` still reads "slightly faster"/"slightly slower"). `tts.delivery_tags`: `on` (default) | `measured` (the translator's `delivery` is ignored) | `off` (no control text at all: the long reference alone). Each results row has `control` (the text sent, or null), `tags` and `tags_source` (`agent | measured | none | dropped`); the `synth` output counts them in `delivery{by_source, tags}` (the dry run too), `babeldub status` in `counts.delivery`.
6. **Regens.** A `regen` after a QA `cer` failure, and any `regen` from the 3rd take of the same text on (takes of the current dub text, counted like `tts.max_attempts`: a retranslation starts again at 1), is voiced **without** the control text (`tags_source: dropped`, `control: null`, `tags` keeps the dropped tags): the words matter more than the delivery. A `regen` after `flat` or `mos` keeps the tags with a new seed (with `tts.tags_reference=bucket` it also takes the next nearest bucket). A `reref` voices the line from a single original recording instead of the long reference (section 5), with its tags.
7. **A changed delivery is a new take.** The control text is part of the take's key: when the translator's `delivery`, the measured tags (a new `analyze`) or `tts.delivery_tags` change, the next `synth` voices the same text again (`reason: delivery`). It counts toward `tts.max_attempts` of that text (beyond it `synth --force`). A take whose tags a regen dropped stays done while the line's tags are the same. A take QA accepted is kept when only the measured tags change (a re-cast, more analysed lines); a new translator `delivery` or another `tts.delivery_tags` still voices it again. Takes made before v0.3 or with another strategy stay done; `synth --force` re-voices them.
8. **Other engines.** Qwen3-TTS and Chatterbox read no control text: the tags are ignored and `synth` adds one `control_ignored` note with the number of such lines.

When to change the settings (record the reason in the report):

- `tts.delivery_tags=measured`: the translators' `delivery` looks unreliable — most lines of a batch carry agent tags that contradict both the hint and the scene (`babeldub query --where char=<id> --fields id,text.src,translation.delivery --limit 50`), for example batches translated in a web chat by a model that ignored the rules.
- `tts.delivery_tags=off`: the pilot shows that the control text hurts a character: its tagged takes fail `cer` or `mos` much more often than its untagged ones, or the tags over-act a deliberately flat or stylised voice (a robot, a narrator). The long reference alone then carries the delivery.
- `tts.tags_reference=bucket`: a character's shouted or whispered lines sound like a different person from the calm reference in the pilot (`sim` low on them, normal elsewhere).
- Change them before the batch synthesis: afterwards every line whose control text changes is voiced again.

### 2.3 Bootstrap: the original timbre, then the character's own target-language voice

An explicit option (`babeldub set tts.ref_strategy=bootstrap`), the VoxCPM2 default of v0.2; it sends no delivery tags. A reference in the original language can make the target-language speech sound like a foreigner speaking it (section 6). The bootstrap removes the accent without losing the actor:

1. **Long timbre references per arousal bucket.** `analyze` measured the prosody of every original (RMS level, F0 range, syllable rate). Relative to the character's own medians, each line is `calm`, `animated` (wider pitch range, faster), `loud` (louder, shouted) or `soft` (quieter, subdued); with the opt-in emotion model an emotion label moves a line prosody calls calm (sad → soft, angry → loud, happy/surprised/fearful → animated). Per character and bucket, 3–4 clean original lines of that bucket (4–7.5 s each, no flags, the cast references first; relaxed to 2–12 s) are joined with 0.35 s gaps into ~15–20 s: `synth/prompts/<char_id>/src-<hash>.wav` (calm) and `src-<bucket>-<hash>.wav`. Calm always exists (from all clean lines when the calm ones are too few); another bucket needs at least 8 s of clean material, else its lines use the nearest existing bucket. Each line gets the reference of the bucket nearest to its original (`bucket` in `synth/results.jsonl`). Without `analyze` every line is calm. The `unknown` group has none and uses `line`/`char`.
2. **Phase 1.** The first `tts.bootstrap_lines` (4) lines of the character — plain lines with text of 2.5–8 s first, in game order — are dubbed with the long reference alone (`ref_mode: bootstrap-src`). The other lines wait (`skipped.bootstrap_wait`; `synth` and `status` say to run `post`, `vc`, `qa` and `synth` again).
3. **Target-language prompt bank.** When QA accepted at least `tts.bootstrap_min_accepted` (3) lines of the character with CER within `qa.max_cer`, each bucket gets a 5–10 s prompt of 1–2 accepted dubs of lines from that bucket, taken from their final audio (after `vc` when the character has an RVC model) with the exact text they were synthesized from: `synth/prompts/<char_id>/tgt.json` (`bank`) + `tgt-<bucket>-<hash>.wav`. Dubs are ranked by naturalness (MOS) and by how well their intonation follows their original (`prosody_sim` of QA); CER is only a gate. The v0.1 rule "lowest CER first" picked the flattest, most synthetic-sounding lines and made the whole character monotone. A bucket's prompt is frozen once built; buckets without accepted dubs are added on a later run.
4. **Phase 2.** The remaining lines, and regenerated phase-1 lines, are dubbed with the long reference **and** the prompt of the bucket nearest to their original (`ref_mode: bootstrap-tgt`, `prompt_id` and `bucket` in `synth/results.jsonl`). A line QA sent back as `flat` or `mos` is regenerated with the next nearest bucket.
5. If every phase-1 line is settled (checked, `keep_original`/`skip`, or out of attempts) and fewer than 3 were accepted, the rest goes on with the long reference only; `ref_reason` says so. Look at such a character first: its references or texts are usually the problem.

To rebuild the prompts (for example after retraining the RVC model), delete `synth/prompts/<char_id>/tgt.json` and run `synth --force --chars <id>`.

### 2.4 Line classes and short words

- **Interjections and nonverbal lines** ("Hehe…", "Hmm.", a groan, `[laughs]`) are not translated or voiced: a cloned laugh sounds fake, the original is better. `synth` reports `skipped.keep_original` and writes an automatic `keep_original` decision. To dub one anyway, write an agent/human decision other than `keep_original` for it and translate it with `batch make --ids`.
- **Short words** ("Freeze!", "Yes, Chief!": less than 1.2 s of speech) carry almost no timbre in a long reference and lose their punch: the line itself is the reference (VoxCPM2: `reference_wav_path` = the original line, no target-language prompt), 6 candidates (`tts.short_word_best_of`), one regen at most (`tts.short_word_attempts` = 2), voiced through the character's own long reference instead of the line (steadier pronunciation), then the original is kept. With `tags` both takes carry the line's delivery tags (unless `tts.delivery_tags=off`), and the regen rules of section 2.2 apply.
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
- it used `char` → another character reference (if there is more than one), otherwise the line's original;
- it used the long reference (`tags`) → the character reference (`char`), or the line's original when the character has a single reference; the delivery tags stay.

There is no need to touch the global `tts.ref_strategy` setting.

## 6. Accent in cross-lingual cloning

A prompt in the original language (English, Polish) carries not only the voice but also the **phonetics of its language**. When synthesizing in another language, the model may carry over an accent: an "English" r, vowel reduction, intonation contours. The listening rounds on Low-Budget Repairs confirmed it: the per-language reference matters. With VoxCPM2 the default `tags` voices from the source-language reference alone, and it still won the blind round on Low-Budget Repairs (English → Russian); if the pilot shows an accent, the explicit bootstrap (section 2.3) adds a target-language prompt made of the character's own accepted dubs. With Qwen3-TTS and Chatterbox the steps below apply. The accent is checked in the pilot.

How to recognize it from metrics (you cannot hear audio):

- `cer` rises for all of a character's lines while `sim` is normal: the model pronounces the target language "with an accent", and ASR makes mistakes;
- in the QA report (`babeldub report`), the character has a high median CER with normal voice similarity.

What to do, in order:

1. **Check the text.** A high CER is most often not an accent but Latin script, numbers in digits, abbreviations, rare names. That calls for `retranslate` and `pronounce` in the glossary (skill `babeldub-translate`).
2. **Stress for Russian.** VoxCPM2 and Qwen3-TTS Base do not read the U+0301 stress mark: they turn it into extra sounds (and VoxCPM2 reads `+` as the word "plus"), so the CLI strips marks from the text for every model except a stress model. Fix a wrong stress by rewording or with the glossary `pronounce` (the CLI puts it into the dub text; a stress mark in it reaches the engine in the form the engine reads). An A/B on VoxCPM2 found a CAPITAL stressed vowel nearly free (+0.02 CER) and plain text best overall: `babeldub set tts.stress=on` (off by default) marks only homographs with RUAccent (qc env) as capital vowels; try it on the pilot and keep it only when the stress errors are real. Only the Qwen3-TTS fine-tune `qwen3-tts-12hz-1.7b-ru-stress-cf` follows the mark, but it preserved the original hero's voice noticeably worse, so it is neither installed nor selected automatically: enable it only for widespread stress errors and after a pilot that checks `sim` (`babeldub models fetch qwen3-tts-12hz-1.7b-ru-stress-cf`, `babeldub set tts.engine=qwen3tts-fast tts.model=qwen3-tts-12hz-1.7b-ru-stress-cf`).
3. **Compare prompt languages.** If the game is voiced in several languages (Low-Budget Repairs has en-US and pl-PL), run the pilot on both: the accents of different languages sound different in the target language. In v0.1 the CLI takes the originals of the locale that `extract` extracted; comparing languages is a manual experiment, and its outcome is recorded in the report.
4. **VoxCPM2: `bootstrap`.** When the text is clean and the accent is real, `babeldub set tts.ref_strategy=bootstrap` (section 2.3) gives the character a target-language prompt from its accepted dubs; the delivery tags are then not used. Name the trade-off in the report.
5. **`char` with a more neutral delivery** (Qwen3-TTS, Chatterbox). A calm reference usually carries less "foreign" intonation than an emotional line. For a character with a strong accent you can switch to `char` at the cost of some emotion; this is a trade-off and must be named in the report.
6. **For Chatterbox** with a prompt in another language, the authors recommend `cfg_weight=0` to pull the accent less. The v0.1 worker does this itself when the prompt language differs from the target. Chatterbox's emotion strength is set by `exaggeration` (default 0.5); engine parameters are passed through `tts.params` in `project.json`: `babeldub set tts.params.exaggeration=0.7`. Do not edit `project.json` by hand.

Two former ideas became part of the chain: the character's first accepted target-language lines as the prompt (the bootstrap, the default of v0.2 and an explicit option since v0.3, section 2.3) and a voice-conversion post-pass (RVC per main character, stage `vc`, which also pulls the timbre back to the original after the prompt). Zero-shot VC (Seed-VC) was rejected: it hallucinated words and sounds. Embedding-only mode (`x_vector_only`) as a remedy for accent is not verified; do not apply it without an explicit decision by the user.

## 7. How QA checks that the voice is the same

- `sim_line` is the cosine similarity of ECAPA embeddings between the dub and the original line; `sim_char` is between the dub and the character reference; `sim` is their mean.
- `sim_ref` is the character's baseline: the mean pairwise similarity of the character's own clean original lines (up to the 12 longest). Absolute similarity values differ from voice to voice, and cross-lingual similarity is lower than same-language similarity, so the comparison is made against this baseline.
- Threshold: `sim ≥ max(ratio × sim_ref, qa.min_sim_abs)` with ratio `qa.min_sim_ratio_xlang` (0.45) for a dub in another language than the original audio, `qa.min_sim_ratio` (0.75) for a same-language dub, floor 0.25; the identity margin also fails a dub closer to another character's voice than to its own (reason `identity`). Below the threshold, the reason is `sim`: the CLI auto-decides `regen` until attempts run out, then `keep_original`. The qa role may assign `reref` instead if the defect looks like a prompt problem (shouting, noise, an effect in the original).

Details: skill `babeldub-qa`.
