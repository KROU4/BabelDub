# Post-processing: how `babeldub post` turns synthesis into a "native" line

Read this for questions about loudness, pauses, sample rate and channels, on `post` errors, and for lines with effects (radio, phone, echo).

## 1. Order of operations (CLI v0.1)

For the latest successful attempt of every line (`audio/fit.py`):

1. **Speech spans.** The dub is resampled to the original's rate. The speech of both is found with an energy VAD: 20 ms frames within 42 dB of the loudest frame. The original's span is `target_s`.
2. **Fit, never slower.** A dub shorter than the original keeps its tempo; the rest is silence (`post.tempo_min` 1.0). A dub whose speech does not fit the room after the original's speech onset first loses part of its internal pauses (silences of 120 ms or more, each shortened down to max(80 ms, half of it); `pause_cut_s`), then speeds up without changing pitch to at most `post.tempo_max` (1.15); an overrun within `post.tolerance` (3%) is not sped up. A `fixed_length` line is split into chunks matched to the original's speech segments (`inventory/analysis.jsonl` `vad_segments` when `analyze` ran, else the energy VAD); each chunk starts where its segment starts (`onset_deltas`) and is fitted to that segment the same way, so it is never longer than the segment unless even 1.15 is not enough. Onsets are measured the same way on both sides: the first sample above the VAD threshold minus 30 ms (the pad of the Silero `vad_segments`). A chunk that would still run into the next segment's onset takes the time from itself first (its pauses, a speed-up up to `post.tempo_max`, even within `post.tolerance`), then from the silence before it (it starts up to 0.3 s early, a negative `onset_deltas` value); only what is left pushes the next chunk later. A group that would end early instead gets longer pauses between its own chunks (each up to twice its length, +0.4 s at most; `pause_add_s`), never slower speech. The tool is Rubber Band (R3), otherwise `rubberband` in ffmpeg, otherwise `atempo`; `stretch_method` says which.
3. **Smooth edges**: a 3 ms fade-in (short, so the attack is not smeared) and a 10 ms fade-out, so there are no clicks.
4. **Loudness matched to the original speech**, not to a general standard: the game mixes voices on its own buses. For speech of 0.4 s and longer, BS.1770 integrated loudness (LUFS); for shorter, RMS. The reference is the original's speech span, downmixed to mono.
5. **Placement and exact length.** The speech starts where the original's speech starts; the file has **exactly the original's sample count**, sample rate and channel count (a mono dub is duplicated). A dub that runs past the original's speech uses its trailing pause, then its leading pause. Speech is never cut: a dub that does not fit even at `tempo_max` makes the file longer (`exact: false`, listed in `not_exact[]`).
6. **Peak ceiling**: true peak no higher than `post.true_peak_db` (−1 dB).
7. **Channel match** (`post.match`, section 4): the dub takes the sound of the original's channel — its long-term spectrum (EQ), its bandwidth and, with `full`, a radio/phone chain — then its loudness is matched again. The fitted take before the match stays as `post/pre_match/<uid>.wav` (`pre_match_wav`).
8. Written as 16-bit PCM to `post/audio/<uid>.wav`. The container codec (FADPCM, PCM16, Vorbis…) is chosen at build time (skill `babeldub-pack`).

If there is no original audio, silence of the declared `audio.dur_s`/`sr`/`ch` stands in for it and the loudness target is −20 LUFS.

## 2. Fields of `post/post.jsonl`

`id`, `attempt`, `wav`, `dur_s` (whole file), `orig_dur_s`, `target_s`, `ratio` (dub speech / `target_s` after fitting), `ratio_raw` (before it), `tempo` (>1 faster, 1 = unchanged), `stretch` (= 1/`tempo`), `stretch_method`, `lufs_in`, `lufs_out`, `level_ref` (level of the original), `measure` (`lufs` or `rms`), `peak_db`, `lead_s`, `trail_s`, `pause_cut_s` (pause time removed), `pause_add_s` (silence added to the pauses of a `fixed_length` dub that would end early), `onset_deltas` (placed minus original onset per segment, s), `chunk_tempo` (`{n, min, max}`), `sr`, `ch`, `frames`, `exact`, `fixed_length`, `over_length`, `src_wav`, `src_key`, `match` (`{mode, applied, ref, eq_db, cutoff_hz, radio, radio_chain, low_hz, ltas_db: {before, after}}`, or `{mode, applied: false, reason}`), `pre_match_wav`. Look at them through `babeldub query`; do not read the whole file.

## 2.1 RVC timbre transfer (`babeldub vc`)

For characters with an RVC model (`babeldub vc train`), `vc` runs after `post`: Applio converts `post/audio/<uid>.wav` (index rate `vc.index_rate` 0.5, consonant protection `vc.protect` 0.33, rmvpe f0; Applio's `--proposed-pitch` is not used because it moves every line's median F0 to a fixed 155 Hz; `vc.pitch` = `keep` (default: the dub keeps its own F0), `char` (one shift per character to the median F0 of its originals) or `line` (each take shifted to its own original line's median F0), ±12 semitones at most), then the CLI resamples the 40 kHz result back to the original rate, pads or trims it to the post file's exact sample count and channels, and matches the speech level again. RVC always converts the take before the channel match (`pre_match_wav`), and `vc` matches the channel again on the converted take (its own `match`, `vc/pre_match/<uid>.wav`). The result is `vc/audio/<uid>.wav`; a new `post/post.jsonl` row (`stage: "vc"`, `vc: {char_id, model_key, …}`, `pre_vc_wav`) makes QA check it and the build pack it. Characters without a model pass through. `vc` is idempotent; another model or other settings convert again from `pre_vc_wav`. `post --force` writes a fresh pre-conversion row, so `vc` converts it again.

## 3. Errors and what to do

| `post` output | Cause | Action |
|---|---|---|
| exit code 4, "rubberband not found" or "ffmpeg not found" | no stretching tool | `babeldub-doctor`: `babeldub setup --tools` or install ffmpeg on the PATH |
| `errors[]`: "synthesized audio is silent" | the synthesis is silence | QA gives `regen`; if it repeats, look at the prompt (`references/ref-strategy.md`) |
| `errors[]`: "synth file missing" | the attempt's file was deleted or never written | rerun `babeldub synth` for these `--ids` |
| `over_length[]`, `not_exact[]` | did not fit even at `post.tempo_max` | QA decides (`references/length-policy.md`) |
| `vc` exit code 4 | no `applio` env | `babeldub setup --engines applio` |
| `vc` `errors[]`: "no output" | Applio failed on these files | read the `log` and `tail`; rerun `babeldub vc` |
| `match.applied: false`, "too little speech to measure" | the dub or the original (and the character's other originals) has under 0.3 s of speech | nothing to do: the fitted take is used as it is |
| `orphans` | synthesis exists, but the line is not in `lines.jsonl` (the inventory was rebuilt) | no need to resynthesize; find out why the inventory changed (`babeldub status`) |

`post` is idempotent: attempts already processed are skipped (`up_to_date`); `--force` processes them again.

## 4. The original's channel: EQ, bandwidth, radio and phone (`post.match`)

A clean studio TTS take inside a line recorded through a radio, a phone or a band-limited bank sounds pasted in, and naturalness metrics do not hear it. `post.match` makes the dub take the original's channel after the fit (and again after `vc`):

- `eq` (default for new projects): the long-term average spectrum of the speech frames of the dub and of the original line in 1/3-octave bands from 80 Hz to 16 kHz; the difference relative to the speech band (315 Hz – 3.15 kHz) becomes a smooth EQ clipped to ±9 dB (`eq_db`), applied as one minimum-phase filter. Plus **bandwidth**: when the original has almost nothing above some frequency (an 8 kHz bank, a phone line), the dub is low-passed there (`cutoff_hz`). A line with under 1 s of speech takes its EQ from the character's other originals as well (`ref: "character"`).
- `full`: `eq`, plus for a **radio/phone original** (`radio: true`: highs cut at 4.5 kHz or lower and the lows under ~250 Hz missing) a band-pass at the original's edges and a mild saturation (`radio_chain: true`). With `eq` such a line is only band-limited and EQ'd (`radio: true`, `radio_chain: false`).
- `off`: no match (a project created before v0.4 without the setting keeps `off`).

`match.ltas_db` shows the spectral distance to the original before and after (dB; lower is closer); QA reports the final one as `ltas_db` (informational, never a fail reason). QA measures the voice (`sim`, the identity margin, MOS, prosody) on `pre_match_wav`, the take before the match, and the words (ASR/CER) on the final file.

When to change it (record the reason in the report):

- `babeldub set post.match=full` when the game has baked-in radio or phone lines (police radio, intercom, a phone call) and their dubs sound too clean, then `babeldub post --ids <...> && babeldub vc && babeldub qa`. Changing the mode makes every line stale for `post`.
- `babeldub set post.match=off` when a game applies its effects itself (FMOD or Wwise buses, snapshots, the engine's mixer): the source files are dry, so a match only copies small differences of the recordings.

What the match does not do: reverb, the background bed of a cutscene (music, effects) or noise are not reproduced. If a line with a reverb tail or a bed matters to the user, mark it in the report as "needs manual processing"; do not process `post/audio` files with your own scripts.

## 5. What not to do

- Do not normalize everything to one loudness level such as −23 LUFS: the loudness of lines in the game is set by the developer's mix.
- Do not change the channel count or sample rate to "better" ones: the container and the engine expect the original ones.
- Do not trim the original's pauses: some events and subtitles are tied to the edges of the sound.
- Do not process `post/audio` or `vc/audio` files with your own scripts: only the CLI writes the canon, and QA checks exactly its files.
