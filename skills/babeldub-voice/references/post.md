# Post-processing: how `babeldub post` turns synthesis into a "native" line

Read this for questions about loudness, pauses, sample rate and channels, on `post` errors, and for lines with effects (radio, phone, echo).

## 1. Order of operations (CLI v0.1)

For the latest successful attempt of every line (`audio/fit.py`):

1. **Speech spans.** The dub is resampled to the original's rate. The speech of both is found with an energy VAD: 20 ms frames within 42 dB of the loudest frame. The original's span is `target_s`.
2. **Tempo fit**, only when the dub's speech differs from `target_s` by more than `post.tolerance` (3%): the tempo changes toward the original without changing pitch, but only within `post.tempo_min`..`post.tempo_max` (0.93–1.20: at most ~7% slower, 20% faster). The rest of the difference stays as silence. The tool is Rubber Band (R3), otherwise `rubberband` in ffmpeg, otherwise `atempo`; `stretch_method` says which.
3. **Smooth edges**: a 10 ms fade so there are no clicks.
4. **Loudness matched to the original speech**, not to a general standard: the game mixes voices on its own buses. For speech of 0.4 s and longer, BS.1770 integrated loudness (LUFS); for shorter, RMS. The reference is the original's speech span, downmixed to mono.
5. **Placement and exact length.** The speech starts where the original's speech starts; the file has **exactly the original's sample count**, sample rate and channel count (a mono dub is duplicated). A dub that runs past the original's speech uses its trailing pause, then its leading pause. Speech is never cut: a dub that does not fit even at `tempo_max` makes the file longer (`exact: false`, listed in `not_exact[]`).
6. **Peak ceiling**: true peak no higher than `post.true_peak_db` (−1 dB).
7. Written as 16-bit PCM to `post/audio/<uid>.wav`. The container codec (FADPCM, PCM16, Vorbis…) is chosen at build time (skill `babeldub-pack`).

If there is no original audio, silence of the declared `audio.dur_s`/`sr`/`ch` stands in for it and the loudness target is −20 LUFS.

## 2. Fields of `post/post.jsonl`

`id`, `attempt`, `wav`, `dur_s` (whole file), `orig_dur_s`, `target_s`, `ratio` (dub speech / `target_s` after fitting), `ratio_raw` (before it), `tempo` (>1 faster, 1 = unchanged), `stretch` (= 1/`tempo`), `stretch_method`, `lufs_in`, `lufs_out`, `level_ref` (level of the original), `measure` (`lufs` or `rms`), `peak_db`, `lead_s`, `trail_s`, `sr`, `ch`, `frames`, `exact`, `fixed_length`, `over_length`, `src_wav`, `src_key`. Look at them through `babeldub query`; do not read the whole file.

## 2.1 RVC timbre transfer (`babeldub vc`)

For characters with an RVC model (`babeldub vc train`), `vc` runs after `post`: Applio converts `post/audio/<uid>.wav` (index rate `vc.index_rate` 0.5, consonant protection `vc.protect` 0.33, rmvpe f0, proposed pitch), then the CLI resamples the 40 kHz result back to the original rate, pads or trims it to the post file's exact sample count and channels, and matches the speech level again. The result is `vc/audio/<uid>.wav`; a new `post/post.jsonl` row (`stage: "vc"`, `vc: {char_id, model_key, …}`, `pre_vc_wav`) makes QA check it and the build pack it. Characters without a model pass through. `vc` is idempotent; another model or other settings convert again from `pre_vc_wav`. `post --force` writes a fresh pre-conversion row, so `vc` converts it again.

## 3. Errors and what to do

| `post` output | Cause | Action |
|---|---|---|
| exit code 4, "rubberband not found" or "ffmpeg not found" | no stretching tool | `babeldub-doctor`: `babeldub setup --tools` or install ffmpeg on the PATH |
| `errors[]`: "synthesized audio is silent" | the synthesis is silence | QA gives `regen`; if it repeats, look at the prompt (`references/ref-strategy.md`) |
| `errors[]`: "synth file missing" | the attempt's file was deleted or never written | rerun `babeldub synth` for these `--ids` |
| `over_length[]`, `not_exact[]` | did not fit even at `post.tempo_max` | QA decides (`references/length-policy.md`) |
| `vc` exit code 4 | no `applio` env | `babeldub setup --engines applio` |
| `vc` `errors[]`: "no output" | Applio failed on these files | read the `log` and `tail`; rerun `babeldub vc` |
| `orphans` | synthesis exists, but the line is not in `lines.jsonl` (the inventory was rebuilt) | no need to resynthesize; find out why the inventory changed (`babeldub status`) |

`post` is idempotent: attempts already processed are skipped (`up_to_date`); `--force` processes them again.

## 4. Effects in the original: radio, phone, echo

CLI v0.1 does **not add** effects. First, work out whether the effect is baked into the file:

- if the game applies the effect itself (FMOD or Wwise buses and snapshots, the engine's mixer), the source files are dry, and nothing needs doing: the dub gets the same effect in the game;
- if the effect is baked into the file (the line has a "narrow" frequency band or a reverb tail that the character's other lines lack), the `line` prompt carries it into the timbre partially and unpredictably.

What to do in v0.1: such lines usually fail voice similarity; the qa role may assign `reref` (a `char` prompt, a clean voice without the effect) and note in `note` that the effect is not reproduced. Adding the effect with your own code (filters, a reverb) is out of scope for v0.1; if it is critical to the user, mark the lines in the report as "needs manual processing".

## 5. What not to do

- Do not normalize everything to one loudness level such as −23 LUFS: the loudness of lines in the game is set by the developer's mix.
- Do not change the channel count or sample rate to "better" ones: the container and the engine expect the original ones.
- Do not trim the original's pauses: some events and subtitles are tied to the edges of the sound.
- Do not process `post/audio` or `vc/audio` files with your own scripts: only the CLI writes the canon, and QA checks exactly its files.
