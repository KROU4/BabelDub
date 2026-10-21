# Models and hardware

BabelDub dubs every line **in the voice of the original character**. All TTS engines in the catalog are zero-shot voice-cloning engines: the voice prompt is the original line itself (its audio plus its source text), so the timbre, emotion and delivery of that exact phrase carry over to the target language. Quality control then checks the dub with speech recognition and speaker similarity.

This page explains which models v0.1 uses for each task and hardware tier, their licenses and what we measured. The source of truth for what is actually installed is `babeldub models list` and `babeldub doctor`; the catalog itself is [`runtime/src/babeldub/data/models.yaml`](../runtime/src/babeldub/data/models.yaml).

## Policy

- **GPU-first.** When an NVIDIA GPU is present, all neural compute (TTS, ASR, speaker embeddings, clustering on embeddings) runs on it. The project setting is `compute.device`: `auto` (default: the GPU if there is one) | `cuda` | `cpu`. `babeldub setup` installs CUDA builds of torch whenever it sees a GPU, even a small one; the hardware tier only picks model sizes. Each worker reports the device it actually loaded on, and a command warns when a GPU exists but the worker ended up on the CPU. Light DSP (resampling, loudness) stays on the CPU.
- **Permissive licenses by default.** The default catalog contains only Apache-2.0 and MIT models.
- **Non-commercial models only with explicit consent.** Such a model is marked `commercial: false` and `opt_in`; `setup` and `models fetch` install it only after the user has seen the license and agreed (`--accept-license <name>`). v0.1 ships no non-commercial model.
- **No weights in BabelDub.** `babeldub setup` downloads them from Hugging Face into `BABELDUB_HOME/models` (default `~/.babeldub`, which is `C:\Users\<user>\.babeldub` on Windows) and shows each license first. The license of every model used ends up in `build/build_manifest.json` (`models[]`) and in the final report.
- **One model on the GPU at a time.** Synthesis and QA never run at the same time: the CLI holds `state/locks/gpu.lock`, and models load in separate processes in turn.

## Models per task (v0.1 catalog)

| Task | Engine (env) | Model | Languages | Weights license | Download | VRAM (catalog estimate) | Installed by default on tiers |
|---|---|---|---|---|---|---|---|
| Voice cloning, default | `qwen3tts` | [Qwen3-TTS-12Hz-1.7B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) | zh, en, ja, ko, de, fr, ru, pt, es, it | Apache-2.0 | 4.54 GB | ~6 GB | 8gb, 12gb, 24gb |
| Voice cloning, smaller fallback | `qwen3tts` | [Qwen3-TTS-12Hz-0.6B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) | same 10 languages | Apache-2.0 | 2.52 GB | ~4 GB | cpu (runs on every tier) |
| Russian stress marks (**opt-in**) | `qwen3tts` | [qwen3-tts-12hz-1.7b-ru-stress-cf](https://huggingface.co/siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf) | ru | Apache-2.0, see the caveat below | 4.58 GB | ~6 GB | none: only when set explicitly |
| Voice cloning for pl, tr and other languages | `chatterbox` | [chatterbox-multilingual](https://huggingface.co/ResembleAI/chatterbox) (Multilingual V3) | ar, da, de, el, en, es, fi, fr, he, hi, it, ja, ko, ms, nl, no, pl, pt, ru, sv, sw, tr, zh | MIT; every file carries an inaudible PerTh watermark | 3.21 GB | ~7 GB (not verified for V3) | 8gb, 12gb, 24gb |
| ASR for QA (CER of the dub) | `qc` | [faster-whisper-large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) | any | MIT | 1.62 GB | ~2 GB | every tier |
| Speaker embeddings: voice similarity, clustering | `qc` | [spkrec-ecapa-voxceleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) (SpeechBrain ECAPA-TDNN) | any | Apache-2.0 | 0.09 GB | ~0.5 GB | every tier |
| Pipeline tests only | `dummy` | none (writes a tone of the expected length) | any | MIT | — | — | — |

Each engine has its own venv in `BABELDUB_HOME/envs/<engine>` because the packages conflict: `qwen3tts` and `qc` use torch 2.8.0 (CUDA 12.8 wheels), `chatterbox` uses torch 2.6.0 (CUDA 12.4 wheels) and another transformers version. Approximate venv sizes: `qwen3tts` 4.0 GB, `chatterbox` 3.5 GB, `qc` 5.0 GB. On GPU tiers `qc` also gets `nvidia-cublas-cu12` and `nvidia-cudnn-cu12==9.*` for faster-whisper.

The VRAM column is the catalog estimate. The Chatterbox figure (6–7 GB) is a community number for the original English Chatterbox of 2025 and has not been verified for Multilingual V3. On Windows the desktop also takes part of the video memory.

## Measured on an RTX 3060 Ti (8 GB)

BabelDub's own measurements with the official `qwen-tts` package and Qwen3-TTS-12Hz-1.7B-Base:

| Mode | RTF |
|---|---|
| one line per call | ≈ 4.5 |
| batches of 3–4 lines | ≈ 1.4–2.1 |
| batch of 4 lines (`tts.batch_size`, default 4) | ≈ 1.4, peak VRAM ≈ 5.5 GB |

Peak VRAM (`vram_peak_gb`) across these runs was 4.3–5.5 GB, so the default model and batch fit an 8 GB card. RTF here is generation time divided by the duration of the generated audio (lower is faster; 1.0 is real time). `vram_peak_gb` is PyTorch's peak allocated memory; the process also needs some memory for the CUDA context. Batching wins because generation is dominated by per-step overhead, not by batch width. `synth` prints `rtf` and `vram_peak_gb` for your own GPU, so check them on the pilot.

Not measured yet: Qwen3-TTS 0.6B, Chatterbox Multilingual V3 and the `qc` worker (faster-whisper large-v3-turbo + ECAPA) on this card. The research estimate for faster-whisper large-v3-turbo in fp16 is about 1.5–2 GB.

Speed notes:

- The official `qwen-tts` package is slow. The `faster-qwen3-tts` accelerator (CUDA Graphs) reaches 1.83× real time for 1.7B and 2.26× for 0.6B on an RTX 4060 under Windows (the accelerator author's numbers). It is a candidate, not part of v0.1: it needs a different transformers version and cannot share the venv.
- FlashAttention 2 is not shipped for Windows; the workers use `sdpa`.
- Quality reported by the Qwen3-TTS authors (MiniMax Multilingual set): Russian WER 3.21 and SIM 0.792 for 1.7B, 4.46 and 0.781 for 0.6B. These are the model authors' numbers, not an independent measurement.

## Hardware tiers

`doctor` picks the tier from the VRAM of the largest NVIDIA GPU (`tier_for_vram`): no CUDA GPU or under 5.5 GB → `cpu`; under 11 GB → `8gb`; under 20 GB → `12gb`; otherwise `24gb`. Override it with `tts.tier` or `setup --tier`.

`babeldub setup` without flags installs the project's TTS engine and `qc`, the models marked as default for the tier and the target language, and vgmstream. Chatterbox is installed only when it is the project's engine (for example, for Polish or Turkish) or when you ask for it with `--engines chatterbox`.

| Tier | Installed by default | What to expect |
|---|---|---|
| `cpu` | Qwen3-TTS 0.6B-Base, faster-whisper large-v3-turbo, ECAPA | Works, but many times slower than real time: warn the user that a whole game can take hours (not measured). A small GPU in this tier still gets CUDA torch, and every model runs on it |
| `8gb` (the main scenario, e.g. RTX 3060 Ti) | Qwen3-TTS 1.7B-Base (or Chatterbox V3 for its languages), faster-whisper, ECAPA | One model on the GPU at a time; on `CUDA out of memory` switch to 0.6B (`babeldub set tts.model=Qwen3-TTS-12Hz-0.6B-Base`) |
| `12gb` | the same | Headroom; a second engine for comparison (VoxCPM2) is a candidate |
| `24gb` (16–24 GB and more) | the same | Candidates: VoxCPM2, FireRedTTS3, MOSS-TTS with a duration parameter |

## Target language → engine (8 GB tier, permissive licenses only)

| Target language | Engine | Note |
|---|---|---|
| ru | `qwen3tts` (1.7B-Base) | `ru-stress-cf` only when set explicitly (see below) |
| en, de, fr, es, pt, it, zh, ja, ko | `qwen3tts` | Apache-2.0 |
| pl, tr, and ar, da, el, fi, he, hi, ms, nl, no, sv, sw | `chatterbox` | MIT; tell the user about the watermark. When the reference is in another language than the target, the worker sets `cfg_weight=0` itself (upstream advice) |
| uk and other languages | none in v0.1 | `babeldub init` returns code 3; there is no permissively licensed engine for them yet (see the candidates) |

Choose an engine with `babeldub set tts.engine=<engine>` or once with `babeldub synth --engine <engine>`. Choose a model inside the engine with `babeldub set tts.model=<name>`; otherwise `synth` takes the downloaded general (not language-specific) model of the engine for the tier and language, the larger one first.

## Why the Russian stress fine-tune is opt-in

`qwen3-tts-12hz-1.7b-ru-stress-cf` follows the stress mark U+0301 far better than the base model (100 % of cases on the dictionary vowel and 96 % against the dictionary, versus 65 % and 17 % for the base model; the fine-tune author's measurement). But on three test lines from Low-Budget Repairs it preserved the original actor's voice much worse: ECAPA similarity to the original was 0.05–0.37, versus 0.37–0.51 for 1.7B-Base. BabelDub dubs in the original hero's voice, so `setup` does not install this model and `synth` never picks it on its own.

To try it: `babeldub models fetch qwen3-tts-12hz-1.7b-ru-stress-cf`, then `babeldub set tts.model=qwen3-tts-12hz-1.7b-ru-stress-cf`, and compare `sim` on a pilot. Caveat from the model card: the weights are Apache-2.0, but part of the stress-training texts comes from a dataset without a declared license; check this before any commercial use.

## Quality control models

The `qc` worker loads both QA models in one process:

- **faster-whisper large-v3-turbo** transcribes the dub; `qa` computes the CER against `dub_text` (`qa.max_cer`, default 0.15).
- **SpeechBrain ECAPA** compares the dub's voice with the original of this line and with the character's chosen reference. The check is relative to the character's own baseline (the mean pairwise similarity of their originals): `sim ≥ qa.min_sim_ratio · sim_ref` (default 0.75). The same embeddings cluster unlabelled voices in `cast`.

Duration (`qa.max_duration_ratio`, default 1.35) and peaks are checked without a model. A line that fails on CER, similarity, peaks or silence gets an automatic `regen` decision until `tts.max_attempts` is reached, then `keep_original`; `synth --attempt-next` re-voices it, and a `reref` decision also switches the voice prompt (`line` → `char` or another reference).

## Candidates for later versions (not in v0.1)

| Model | Why it is interesting | Limitation |
|---|---|---|
| OmniVoice | `duration=` in seconds (exact length for cutscenes), 600+ languages including uk | **weights CC-BY-NC** (non-commercial): only with explicit consent; the author warns about the prompt language's accent carrying over |
| VoxCPM2 | Apache-2.0, 30 languages, 48 kHz; ru SIM 81.1 in the authors' table | ~8 GB VRAM: tight on an 8 GB card; seed only in the GitHub version |
| FireRedTTS3 | Apache-2.0, 24 languages including uk; the best published ru SIM (84.7) | 8.5 GB of weights + a 3.8 GB codec, probably does not fit in 8 GB (not verified); Russian number normalisation must be done separately |
| MOSS-TTS v1.5 | Apache-2.0, duration through `tokens=` | 8B; an 8 GB mode officially exists only for version 1.0 |
| IndexTTS-2 / 2.5 | the best emotion control (a separate emotion reference, an emotion vector) | no Russian; bilibili's own license |
| Fish Audio S2-Pro, Higgs TTS 3 | reference-grade quality | non-commercial licenses, ≥ 24 GB, Linux/WSL |
| MOSS-TTS-Nano-100M | cloning on the CPU (ONNX), 20 languages including ru, pl, tr | lower quality |

Other licenses worth knowing: F5-TTS and its Russian fine-tunes (weights CC-BY-NC), XTTS-v2 (Coqui Public Model License, non-commercial), Seed-VC (GPL-3.0); GigaAM-v3 (Russian ASR), RUAccent and UTMOSv2 are MIT. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

There is no ready speech-to-speech translation that keeps the voice for Russian (SeamlessExpressive, Hibiki-Zero and Index-Echo-S2ST do not support it), so BabelDub uses a cascade: original text → translation → voice-cloning TTS.

## Useful commands

```text
babeldub doctor                         GPU, VRAM, driver, tier, installed envs and models
babeldub setup [--engines qwen3tts,qc] [--tier 8gb] [--lang ru] [--dry-run]
babeldub models list [--engine qwen3tts]
babeldub models fetch <name> [--accept-license <name>]
babeldub set compute.device=auto        auto | cuda | cpu
babeldub set tts.engine=chatterbox
babeldub set tts.model=Qwen3-TTS-12Hz-0.6B-Base
```
