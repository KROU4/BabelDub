# Post-processing: how `babeldub post` turns synthesis into a "native" line

Read this for questions about loudness, pauses, sample rate and channels, on `post` errors, and for lines with effects (radio, phone, echo).

## 1. Order of operations (CLI v0.1)

For the latest successful attempt of every line:

1. **Edges.** Silence at the start and end of the synthesis is trimmed to 30 ms.
2. **Length target.** The speech segment of the original (without edge pauses) is located; its duration is `target_s`.
3. **Compression**, if the dub's speech is longer than the target by more than `post.tolerance` (5%; for `fixed_length`, by more than `post.fixed_length_tolerance`, 3%): without changing pitch, no more than `post.max_stretch` (12%). The tool is Rubber Band (R3), otherwise `rubberband` in ffmpeg, otherwise `atempo`; which one worked is shown in the `stretch` field of the output and in `stretch_method` in `post/post.jsonl`.
4. **Smooth edges**: a short fade so there are no clicks.
5. **Loudness matched to the original line**, not to a general standard: the game mixes voices on its own buses, so relative loudness is what matters. For lines of 0.4 s and longer, BS.1770 integrated loudness (LUFS); for shorter ones, RMS. The reference is the speech part of the original, downmixed to mono.
6. **Peak ceiling**: true peak no higher than `post.true_peak_db` (−1 dB), checked again after resampling.
7. **Format of the original**: the original's sample rate and channel count (a mono dub is duplicated into the required number of channels), written as 16-bit PCM to `post/audio/<uid>.wav`. The container codec (FADPCM, PCM16, Vorbis, etc.) is chosen later, at build time (skill `babeldub-pack`).
8. **The original's pauses** at the start and end are restored. For `fixed_length`, the result is no longer than the original if the speech fits.

If there is no original audio, the target comes from `audio.dur_s` in `lines.jsonl`, loudness is −20 LUFS, and sample rate and channels come from the line's metadata.

## 2. Fields of `post/post.jsonl`

`id`, `attempt`, `wav`, `dur_s`, `target_s`, `ratio` (after fitting), `ratio_raw` (before it), `stretch` (factor; 1 means no compression), `stretch_method`, `lufs_in`, `lufs_out`, `level_ref` (level of the original), `measure` (`lufs` or `rms`), `peak_db`, `lead_s`, `trail_s`, `sr`, `ch`, `fixed_length`, `over_length`. Look at them through `babeldub query`; do not read the whole file.

## 3. Errors and what to do

| `post` output | Cause | Action |
|---|---|---|
| exit code 4, "rubberband not found" or "ffmpeg not found" | no stretching tool | `babeldub-doctor`: `babeldub setup --tools` or install ffmpeg on the PATH |
| `errors[]`: "synthesized audio is silent" | the synthesis is silence | QA gives `regen`; if it repeats, look at the prompt (`references/ref-strategy.md`) |
| `errors[]`: "synth file missing" | the attempt's file was deleted or never written | rerun `babeldub synth` for these `--ids` |
| `over_length[]` | did not fit even after compression | QA decides (`references/length-policy.md`) |
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
- Do not process `post/audio` files with your own scripts: only the CLI writes the canon, and QA checks exactly its files.
