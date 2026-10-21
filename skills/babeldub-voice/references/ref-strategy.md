# Voice prompt strategy: dubbing in the original character's voice

Read this before the pilot, and whenever the metrics say "wrong voice", "the emotion is gone" or "there is an accent".

## 1. What a voice prompt is

BabelDub engines clone a voice zero-shot: the model is not trained on the character; it receives a short sample of the character's speech, the **voice prompt**, and keeps speaking the same way, but with new text. In Qwen3-TTS (the default engine) this is ICL mode: the input is `ref_audio` (the audio) and `ref_text` (what is said in it). According to the authors, the model's audio tokenizer "fully preserves paralinguistic information", and the Base model has no style instructions: emotion and manner come only from the prompt.

Hence the main conclusion: **as the prompt, so the dub.** A calm reference produces a calm voice even for a shout. A shout in the prompt produces a shout.

Chatterbox (the engine for pl, tr and others) clones from the prompt audio only, without a transcript; emotion also comes from the audio, and its strength is additionally set by the `exaggeration` parameter.

## 2. Three strategies

| `tts.ref_strategy` | Prompt | What carries over | When |
|---|---|---|---|
| `line` | the original recording of this same line + its original text | timbre **and** emotion, pace, loudness of delivery, breathing, the manner of this exact phrase | the main dubbing mode |
| `char` | the character's best clean reference (`cast/refs.jsonl`, `chosen`) + its text | the character's timbre in a neutral delivery | short lines, interjections, noise or music under the voice, no original text |
| `auto` (default) | `line` if the line is usable; otherwise `char` | — | always, unless there is a reason to change it |

The `auto` rule in CLI v0.1. A line is usable for `line` if all of the following hold:

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

A prompt in the original language (English, Polish) carries not only the voice but also the **phonetics of its language**. When synthesizing in another language, the model may carry over an accent: an "English" r, vowel reduction, intonation contours. The OmniVoice authors say so explicitly; for other models it is expected; the quality of the EN→RU pair on game voices has not been measured in published tables. So the accent is checked in the pilot.

How to recognize it from metrics (you cannot hear audio):

- `cer` rises for all of a character's lines while `sim` is normal: the model pronounces the target language "with an accent", and ASR makes mistakes;
- in the QA report (`babeldub report`), the character has a high median CER with normal voice similarity.

What to do, in order:

1. **Check the text.** A high CER is most often not an accent but Latin script, numbers in digits, abbreviations, rare names. That calls for `retranslate` and `pronounce` in the glossary (skill `babeldub-translate`).
2. **Stress for Russian.** By default `Qwen3-TTS-12Hz-1.7B-Base` does the voicing: the stress fine-tune `qwen3-tts-12hz-1.7b-ru-stress-cf` follows the U+0301 mark more reliably, but on the test lines it preserved the original hero's voice noticeably worse, so it is neither installed nor selected automatically. Enable it only for widespread stress errors and only after a pilot that checks `sim`: `babeldub models fetch qwen3-tts-12hz-1.7b-ru-stress-cf`, then `babeldub set tts.model=qwen3-tts-12hz-1.7b-ru-stress-cf`. Put the stress mark in `dub_text` on words where an error is likely (homographs, names), via the glossary's `pronounce`.
3. **Compare prompt languages.** If the game is voiced in several languages (Low-Budget Repairs has en-US and pl-PL), run the pilot on both: the accents of different languages sound different in the target language. In v0.1 the CLI takes the originals of the locale that `extract` extracted; comparing languages is a manual experiment, and its outcome is recorded in the report.
4. **`char` with a more neutral delivery.** A calm reference usually carries less "foreign" intonation than an emotional line. For a character with a strong accent you can switch to `char` at the cost of some emotion; this is a trade-off and must be named in the report.
5. **For Chatterbox** with a prompt in another language, the authors recommend `cfg_weight=0` to pull the accent less. The v0.1 worker does this itself when the prompt language differs from the target. Chatterbox's emotion strength is set by `exaggeration` (default 0.5); engine parameters are passed through `tts.params` in `project.json`: `babeldub set tts.params.exaggeration=0.7`. Do not edit `project.json` by hand.

Ideas that are **not verified** and not implemented in v0.1: using the character's first successful line in the target language as the new prompt (with timbre-drift control by similarity to the original); embedding-only mode (`x_vector_only`) as a remedy for accent; a voice-conversion (VC) post-pass. Do not apply them without an explicit decision by the user.

## 7. How QA checks that the voice is the same

- `sim_line` is the cosine similarity of ECAPA embeddings between the dub and the original line; `sim_char` is between the dub and the character reference; `sim` is their mean.
- `sim_ref` is the character's baseline: the mean pairwise similarity of the character's own clean original lines (up to the 12 longest). Absolute similarity values differ from voice to voice, and cross-lingual similarity is lower than same-language similarity, so the comparison is made against this baseline.
- Threshold: `sim ≥ qa.min_sim_ratio × sim_ref` (default 0.75). Below it, the reason is `sim`: the CLI auto-decides `regen` until attempts run out, then `keep_original`. The qa role may assign `reref` instead if the defect looks like a prompt problem (shouting, noise, an effect in the original).

Details: skill `babeldub-qa`.
