# Third-party components and licenses

BabelDub code is distributed under the MIT license (see [LICENSE](LICENSE)). The repository and the release archives contain **no** third-party binaries, model weights or game files: the user obtains everything listed below themselves, either through `uv` and PyPI when installing the CLI or with `babeldub setup`, which downloads tools and models into `BABELDUB_HOME` (default `~/.babeldub`) and shows each model's license before the download.

This file is a reference, not legal advice. The licenses at the linked sources apply; if they differ from this file, they prevail. `babeldub models list` prints the current list of models with their licenses (catalog: `runtime/src/babeldub/data/models.yaml`).

## Python dependencies of the CLI

Installed from PyPI together with `babeldub`; not part of the repository.

| Package | License | Note |
|---|---|---|
| [NumPy](https://numpy.org/) | BSD-3-Clause | |
| [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause | the wheels contain libsndfile (LGPL-2.1-or-later), linked dynamically |
| [PyYAML](https://pyyaml.org/) | MIT | |
| [UnityPy](https://github.com/K0lb3/UnityPy) | MIT | reads Unity assets; pulls its own dependencies (Pillow, texture2ddecoder, etcpak, fmod-toolkit and others) with their own licenses |
| [fmod-toolkit](https://pypi.org/project/fmod-toolkit/) / [pyfmodex](https://github.com/tyrylu/pyfmodex) | wrappers: MIT; the FMOD libraries: proprietary FMOD Engine license (Firelight Technologies) | arrive as UnityPy dependencies; BabelDub does not distribute them. FMOD is a trademark of Firelight Technologies Pty Ltd |
| [pytest](https://pytest.org/), [ruff](https://docs.astral.sh/ruff/) | MIT | development only |

## External tools

The CLI calls them as separate programs. BabelDub does not vendor them; `babeldub setup --tools` downloads the official builds into `BABELDUB_HOME/tools`, and the user installs the rest.

| Tool | License | How it is used |
|---|---|---|
| [vgmstream](https://github.com/vgmstream/vgmstream) (`vgmstream-cli`) | its own permissive ISC-style license; builds include third-party decoders (including FFmpeg, LGPL) | decoding game audio (FSB5, Wwise and others) |
| [FFmpeg](https://ffmpeg.org/) (`ffmpeg`, `ffprobe`) | LGPL-2.1-or-later or GPL, depending on the build | conversion, `atempo` when fitting duration |
| [Rubber Band](https://breakfastquay.com/rubberband/) (`rubberband` CLI) | **GPL-2.0-or-later** (commercial license available from the authors) | high-quality time stretching; **not vendored**, used only if the user installed it, otherwise the CLI falls back to `ffmpeg atempo` |
| [uv](https://github.com/astral-sh/uv) | MIT or Apache-2.0 | installing the CLI and the engine environments |
| [PyTorch](https://pytorch.org/) | BSD-3-Clause | in the separate engine venvs (`BABELDUB_HOME/envs`) |

## Models (downloaded on demand)

Weights are not part of BabelDub. A model's license also governs the audio it generates, to the extent its terms say so. Models with non-commercial licenses are not in the default catalog and are enabled only with the user's explicit consent. See [docs/MODELS.md](docs/MODELS.md) for tiers and hardware.

### Catalog of version 0.1

| Model | Purpose | Weights license | Commercial use |
|---|---|---|---|
| [Qwen3-TTS-12Hz-1.7B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base), [0.6B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) (`faster-qwen3-tts` on NVIDIA GPUs, the official `qwen-tts` package otherwise) | voice cloning, default engine | Apache-2.0 | yes |
| [siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf](https://huggingface.co/siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf) | Russian stress marks for Qwen3-TTS (opt-in, not installed by default) | Apache-2.0 | yes, but the author warns that part of the training texts comes from a dataset without a declared license: check before commercial use |
| [Chatterbox Multilingual V3](https://huggingface.co/ResembleAI/chatterbox) (Resemble AI) | cloning for pl, tr and other languages | MIT | yes; every file carries an inaudible PerTh watermark |
| [faster-whisper large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)) | speech recognition for QA | MIT | yes |
| [SpeechBrain ECAPA-TDNN](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) ([SpeechBrain](https://github.com/speechbrain/speechbrain)) | voice similarity to the original, voice clustering | Apache-2.0 | yes |

Environment packages: `qwen-tts` (Apache-2.0), `faster-qwen3-tts` (MIT) with `qwen-tts-hf` (Apache-2.0), `chatterbox-tts` (MIT), `faster-whisper` (MIT), `speechbrain` (Apache-2.0), `jiwer` (Apache-2.0), `num2words` (LGPL-2.1).

### Models that may appear as options

For reference: the BabelDub research also looked at other models. If any of them is added, it will be with the license below and, for non-commercial ones, only with explicit consent.

| Model | Weights license |
|---|---|
| OmniVoice (k2-fsa) | code Apache-2.0, **weights CC-BY-NC** (non-commercial) |
| VoxCPM2, FireRedTTS3, Fun-CosyVoice3, MOSS-TTS | Apache-2.0 |
| GigaAM-v3 (ASR for Russian), RUAccent (stress marks), UTMOSv2 | MIT |
| F5-TTS and its Russian fine-tunes | code MIT, **weights CC-BY-NC** |
| XTTS-v2 | **Coqui Public Model License (non-commercial)** |
| Fish Audio S2-Pro | **Fish Audio Research License (non-commercial)** |
| Seed-VC | **GPL-3.0** |

## References and prototypes

Format parsing (FSB5, FADPCM, FMOD banks, Unity Localization tables) is implemented from public descriptions and the project's own research. No GPL or AGPL code from other projects was copied into BabelDub.

## Trademarks

Unity, Unreal Engine, FMOD, Wwise, Godot, Ren'Py, Steam and game titles are trademarks of their owners. BabelDub is not affiliated with or endorsed by them.
