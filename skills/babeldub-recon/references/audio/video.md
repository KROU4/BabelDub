# Video clips and video audio (Bink, USM, MP4/WebM, `video_*` in banks)

Read this if the game has video files (`*.bik`, `*.bk2`, `*.usm`, `*.mp4`, `*.webm`) or an audio container holds video soundtracks (in Low-Budget Repairs: subsounds `video_Cutscene_Intro`, 45 s, and `video_DemoCommercial`, 77 s, inside the dialogue bank).

## Level in BabelDub v0.1

**Outside the scope of v0.1.** The CLI does not dub video. Video audio inside shared containers gets a non-voiced `kind` (`music`, `sfx` or `unknown`) and stays original. Players often complain about AI dubs that skip the cutscenes, so state in the report which videos stayed untranslated.

## Why this is a separate task

- Video audio is usually mixed down: speech + music + effects in one track.
- The timeline is rigid: speech is tied to the picture, and each phrase has a fixed length.
- Container codecs (Bink, USM) need their own tools.

## How it is done (plan for later versions)

1. Demux: pull out the audio track.
2. Separate voice from background (python-audio-separator, BS-Roformer models; only for such tracks).
3. Phrase timings: WhisperX or Qwen3-ForcedAligner.
4. Dub the voice to the timing with the cutscene policy (length ≤ 1.03 of the original).
5. Mix the new voice with the background.
6. Remux into the original codec and container.

## Tools

- CRIWARE USM: CriCodecs (see `audio/criware.md`).
- Bink (`.bik`, `.bk2`): tools were not examined in the BabelDub research (not verified).
- MP4/WebM: ffmpeg (demux and remux).

## Pitfalls

- Mixed-down audio: without separation the dub lands on top of the original speech.
- Video subtitles often live apart from the dialogue tables.
- The codec and parameters on remux must match what the game's player expects.
