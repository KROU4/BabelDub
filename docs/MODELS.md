# Models and hardware

BabelDub dubs every line **in the voice of the original character**. All TTS engines in the catalog are zero-shot voice-cloning engines whose voice prompt is made of the character's own original recordings, and an RVC model trained on the character's lines transfers the timbre once more after synthesis. Quality control then checks the dub with speech recognition and speaker similarity.

This page explains which models v0.1 uses for each task and hardware tier, their licenses and what we measured. The source of truth for what is actually installed is `babeldub models list` and `babeldub doctor`; the catalog itself is [`runtime/src/babeldub/data/models.yaml`](../runtime/src/babeldub/data/models.yaml).

## The default dubbing chain

Chosen by the project owner in blind listening rounds on a real game (Low-Budget Repairs, the character Zbyszek, English → Russian), against Qwen3-TTS with and without RVC, Seed-VC and Qwen3-TTS fine-tunes:

1. **TTS: VoxCPM2** (`voxcpm`): 2B, 48 kHz, 30 languages, Apache-2.0.
2. **Bootstrap voice prompts** (`tts.ref_strategy=auto` → `bootstrap`): every line gets the character's ~15–20 s long timbre reference made of 3–4 clean original lines; the first 4 lines of each character are dubbed with it alone; once QA accepted 3 of them, their final audio and exact text become a ~20–26 s target-language prompt for the rest of the character's lines.
3. **Best of 3** (`tts.best_of`): three takes per line with different seeds; the one whose speech length (energy VAD at −42 dB) is closest to the original's wins.
4. **Exact fit** (`post`): tempo changes only within 0.93–1.20 (pitch kept), the rest is silence; the speech starts where the original's starts; the file has exactly the original's sample count, rate and channels; loudness follows the original speech.
5. **RVC timbre transfer** (`vc`, Applio, MIT): one RVC v2 model per main character, trained once on its clean original lines (`babeldub vc train`, 20 epochs on up to 30 minutes by default), converts every dub of that character in any target language.
6. **QA**: Whisper (with VAD) for CER and ECAPA speaker similarity with cross-language calibration.
7. **Translation**: with an official translation, `translation.mode=auto` adapts it for speech (numbers as words, Latin names transliterated, colloquial, length budget) instead of voicing the book text.

Measured on Low-Budget Repairs with the QA models: VoxCPM2 + RVC CER **0.045** (Qwen3-TTS + RVC: 0.050); ECAPA similarity to the original actor **~0.65–0.70**, where the originals among themselves reach 0.77. Findings of the rounds:

- **The per-language reference matters.** A reference in the source language alone gives the target-language speech the original's accent; the target-language prompt made of the character's own accepted dubs removes it while the long original reference keeps the actor's timbre.
- **Seed-VC was rejected:** zero-shot voice conversion hallucinated words and sounds.
- **Stress marks break Qwen3-TTS Base and VoxCPM2:** both voice U+0301 as extra sounds, so the CLI strips the marks for every model except an explicit stress model.

`tts.engine=auto` runs this chain when the GPU has 8 GB (≥ 7.5 GB reported) and the `voxcpm` env exists; otherwise `synth` falls back to Qwen3-TTS or Chatterbox (below) and says why in `notes`.

## Policy

- **GPU-first.** When an NVIDIA GPU is present, all neural compute (TTS, RVC, ASR, speaker embeddings, clustering on embeddings) runs on it. The project setting is `compute.device`: `auto` (default: the GPU if there is one) | `cuda` | `cpu`. `babeldub setup` installs CUDA builds of torch whenever it sees a GPU, even a small one; the hardware tier only picks model sizes. Each worker reports the device it actually loaded on, and a command warns when a GPU exists but the worker ended up on the CPU. Light DSP (resampling, loudness) stays on the CPU.
- **Permissive licenses by default.** The default catalog contains only Apache-2.0 and MIT models and engines.
- **Non-commercial models only with explicit consent.** Such a model is marked `commercial: false` and `opt_in`; `setup` and `models fetch` install it only after the user has seen the license and agreed (`--accept-license <name>`). v0.1 ships no non-commercial model.
- **No weights in BabelDub.** `babeldub setup` downloads them from Hugging Face into `BABELDUB_HOME/models` (default `~/.babeldub`, which is `C:\Users\<user>\.babeldub` on Windows) and shows each license first. RVC models are trained locally and stay in the project folder (`vc/<char_id>/`). The license of every model used ends up in `build/build_manifest.json` (`models[]`) and in the final report.
- **One model on the GPU at a time.** Training, synthesis, conversion and QA never run at the same time: the CLI holds `state/locks/gpu.lock`, and models load in separate processes in turn.

## Models per task (v0.1 catalog)

| Task | Engine (env) | Model | Languages | License | Download | VRAM | Installed by default |
|---|---|---|---|---|---|---|---|
| Voice cloning, default | `voxcpm` | [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) (2B, 48 kHz) | ar, my, zh, da, nl, en, fi, fr, de, el, he, hi, id, it, ja, km, ko, lo, ms, no, pl, pt, ru, es, sw, sv, tl, th, tr, vi | Apache-2.0 | 4.7 GB | 6.5 GB (measured) | GPUs with 8 GB+ |
| Timbre transfer (stage `vc`) | `applio` (checkout of [Applio](https://github.com/IAHispano/Applio) at a pinned commit) | RVC v2, one model per character trained by `vc train` (pretrained HiFi-GAN 40 kHz, rmvpe, contentvec) | any | MIT | env ~8 GB + prerequisites | training and conversion fit into 8 GB | GPUs with 8 GB+ |
| Voice cloning, GPUs under 8 GB | `qwen3tts-fast` (`qwen3tts` on the CPU) | [Qwen3-TTS-12Hz-1.7B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) | zh, en, ja, ko, de, fr, ru, pt, es, it | Apache-2.0 | 4.54 GB | ~6 GB (measured 4.5–5.5 GB) | 8gb, 12gb, 24gb tiers when VoxCPM2 is not installed |
| Voice cloning, smaller fallback | `qwen3tts-fast` / `qwen3tts` | [Qwen3-TTS-12Hz-0.6B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) | same 10 languages | Apache-2.0 | 2.52 GB | ~4 GB | cpu (runs on every tier) |
| Russian stress marks (**opt-in**) | `qwen3tts-fast` / `qwen3tts` | [qwen3-tts-12hz-1.7b-ru-stress-cf](https://huggingface.co/siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf) | ru | Apache-2.0, see the caveat below | 4.58 GB | ~6 GB | none: only when set explicitly |
| Voice cloning for pl, tr and other languages without VoxCPM2 | `chatterbox` | [chatterbox-multilingual](https://huggingface.co/ResembleAI/chatterbox) (Multilingual V3) | ar, da, de, el, en, es, fi, fr, he, hi, it, ja, ko, ms, nl, no, pl, pt, ru, sv, sw, tr, zh | MIT; every file carries an inaudible PerTh watermark | 3.21 GB | ~7 GB (not verified for V3) | when it is the engine for the language |
| ASR for QA (CER of the dub) | `qc` | [faster-whisper-large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) | any | MIT | 1.62 GB | ~2 GB | every tier |
| Speaker embeddings: voice similarity, clustering | `qc` | [spkrec-ecapa-voxceleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) (SpeechBrain ECAPA-TDNN) | any | Apache-2.0 | 0.09 GB | ~0.5 GB | every tier |
| Pipeline tests only | `dummy` | none (writes a tone of the expected length) | any | MIT | — | — | — |

Each engine has its own venv in `BABELDUB_HOME/envs/<engine>` because the packages conflict: `voxcpm`, `qwen3tts`, `qwen3tts-fast` and `qc` use torch 2.8.0 (CUDA 12.8 wheels), `applio` torch 2.11.0 (CUDA 12.8), `chatterbox` torch 2.6.0 (CUDA 12.4) and another transformers version. `voxcpm` installs the `voxcpm` package from GitHub. `applio` is a git engine: `setup` fetches the Applio repository into `BABELDUB_HOME/engines/applio` at the pinned commit, installs its `requirements-engine.txt` without the torch lines, and runs `core.py prerequisites --pretraineds-hifigan --models --no-exe`. `qwen3tts-fast` is `faster-qwen3-tts` 0.5.4 with `transformers==5.15.1` pinned: 5.20.2 breaks the codec (`'MimiConfig' object has no attribute 'rope_theta'`). Approximate venv sizes: `voxcpm` 4.5 GB, `applio` 8.5 GB, `qwen3tts` 4.0 GB, `qwen3tts-fast` 4.0 GB, `chatterbox` 3.5 GB, `qc` 5.0 GB. On GPU tiers `qc` also gets `nvidia-cublas-cu12` and `nvidia-cudnn-cu12==9.*` for faster-whisper.

**Engine choice (`tts.engine=auto`, what `init` writes).** `synth` runs `voxcpm` when the device is CUDA, the `voxcpm` env exists and it speaks the target language; otherwise the first of `qwen3tts-fast`, `qwen3tts`, `chatterbox` that speaks it. CUDA-only engines fall back along their chain on the CPU (`voxcpm` → `qwen3tts-fast` → `qwen3tts`), and to the next installed engine of the chain when their own env is missing; every fallback is reported in `notes`. Takes made by `qwen3tts-fast` count as done for `qwen3tts` and back (same models, same output for the same seed).

## Measured on an RTX 3060 Ti (8 GB)

- **VoxCPM2:** peak VRAM 6.5 GB. With `best_of=3` every line is generated three times; use `--best-of 1` for a fast draft.
- **RVC (Applio):** 50 epochs on 30 minutes of a character's speech took 37 minutes; the default of 20 epochs takes about 15 minutes per character.
- **Qwen3-TTS-12Hz-1.7B-Base** on Low-Budget Repairs lines:

| Engine (runtime) | Mode | RTF | Peak VRAM |
|---|---|---|---|
| `qwen3tts-fast` (`faster-qwen3-tts` 0.5.4, CUDA graphs) | one line per call (it does not batch) | ≈ 0.52 | ≈ 4.5–4.7 GB |
| `qwen3tts` (official `qwen-tts`) | one line per call | ≈ 3.7–4.5 | ≈ 4.3 GB |
| `qwen3tts` | batches of 3–4 lines (`tts.batch_size`, default 4) | ≈ 1.2–2.1 | ≈ 5.5 GB |

The two Qwen runtimes give identical audio for the same seed, and the same quality (Whisper CER 0.0 and 0.074 on the same test lines). `qwen3tts-fast` loads in ~15 s including the CUDA-graph warm-up, once per `synth` run. Batches with the official package can be dragged to the token cap by one runaway line; the per-line cap and the runaway guard of `synth` limit that. RTF is generation time divided by the duration of the generated audio (lower is faster; 1.0 is real time). `vram_peak_gb` is PyTorch's peak allocated memory; the process also needs some memory for the CUDA context. `synth` prints `rtf` and `vram_peak_gb` for your own GPU, so check them on the pilot.

Not measured yet: Qwen3-TTS 0.6B, Chatterbox Multilingual V3 and the `qc` worker on this card. FlashAttention 2 is not shipped for Windows; the workers use `sdpa`. Quality reported by the Qwen3-TTS authors (MiniMax Multilingual set): Russian WER 3.21 and SIM 0.792 for 1.7B, 4.46 and 0.781 for 0.6B (the authors' numbers, not an independent measurement).

## Hardware tiers

`doctor` picks the tier from the VRAM of the largest NVIDIA GPU (`tier_for_vram`): no CUDA GPU or under 5.5 GB → `cpu`; under 11 GB → `8gb`; under 20 GB → `12gb`; otherwise `24gb`. Override it with `tts.tier` or `setup --tier`.

`babeldub setup` without flags installs the TTS engine, the RVC engine on GPUs with 8 GB+, `qc`, the models marked as default for the tier and the target language, and vgmstream.

| Hardware | Installed by default | What to expect |
|---|---|---|
| no NVIDIA GPU (`cpu`) | `qwen3tts` with Qwen3-TTS 0.6B-Base, faster-whisper, ECAPA | Works, but many times slower than real time: warn the user that a whole game can take hours (not measured). No RVC |
| GPU under 7.5 GB (`8gb` tier from 5.5 GB) | `qwen3tts-fast` with Qwen3-TTS 1.7B-Base (or Chatterbox V3 for its languages), faster-whisper, ECAPA | VoxCPM2 does not fit; no RVC |
| 8 GB (e.g. RTX 3060 Ti, the main scenario) | `voxcpm` with VoxCPM2, `applio`, faster-whisper, ECAPA | The default chain; one model on the GPU at a time |
| 12–24 GB | the same | Headroom; candidates below |

## Target language → engine (permissive licenses only)

| Target language | 8 GB+ GPU | Smaller GPU / CPU |
|---|---|---|
| ru, en, de, fr, es, pt, it, zh, ja, ko | `voxcpm` + RVC | `qwen3tts-fast` / `qwen3tts` |
| pl, tr, ar, da, el, fi, he, hi, ms, nl, no, sv, sw | `voxcpm` + RVC | `chatterbox` (watermark; `cfg_weight=0` when the reference is in another language) |
| my, id, km, lo, tl, th, vi | `voxcpm` + RVC | none |
| uk and other languages | none in v0.1 | none: `babeldub init` returns code 3 |

Choose an engine with `babeldub set tts.engine=<engine>` or once with `babeldub synth --engine <engine>`. Choose a model inside the engine with `babeldub set tts.model=<name>`; otherwise `synth` takes the downloaded general (not language-specific) model of the engine for the tier and language, the larger one first.

## Why the Russian stress fine-tune is opt-in

`qwen3-tts-12hz-1.7b-ru-stress-cf` follows the stress mark U+0301 far better than the base model (100 % of cases on the dictionary vowel and 96 % against the dictionary, versus 65 % and 17 % for the base model; the fine-tune author's measurement). But on three test lines from Low-Budget Repairs it preserved the original actor's voice much worse: ECAPA similarity to the original was 0.05–0.37, versus 0.37–0.51 for 1.7B-Base. BabelDub dubs in the original hero's voice, so `setup` does not install this model and `synth` never picks it on its own. It is the only model that keeps the stress marks in the text.

To try it: `babeldub models fetch qwen3-tts-12hz-1.7b-ru-stress-cf`, then `babeldub set tts.engine=qwen3tts-fast tts.model=qwen3-tts-12hz-1.7b-ru-stress-cf`, and compare `sim` on a pilot. Caveat from the model card: the weights are Apache-2.0, but part of the stress-training texts comes from a dataset without a declared license; check this before any commercial use.

## Quality control models

The `qc` worker loads both QA models in one process:

- **faster-whisper large-v3-turbo** (with VAD) transcribes the dub; `qa` computes the CER against `dub_text` (`qa.max_cer`, default 0.15).
- **SpeechBrain ECAPA** compares the dub's voice with the original of this line and with the character's chosen reference. The check is relative to the character's own baseline (the mean pairwise similarity of their originals): `sim ≥ max(ratio · sim_ref, qa.min_sim_abs)`, with ratio `qa.min_sim_ratio_xlang` = 0.45 for a dub in another language than the original audio (cross-language similarity is always lower), `qa.min_sim_ratio` = 0.75 for a same-language dub, and a floor of 0.25. The identity margin compares the dub with the voice centroids of the other characters. The same embeddings cluster unlabelled voices in `cast` and merge a cluster into the named character it sounds like.

QA checks each line's final take (after `vc` when the character has an RVC model). Duration (`qa.max_duration_ratio`, default 1.35) and peaks are checked without a model. A line that fails on CER, similarity, identity, peaks or silence gets an automatic `regen` decision until `tts.max_attempts` is reached, then `keep_original`; `synth --attempt-next` re-voices it, and a `reref` decision also switches the voice prompt.

## Candidates for later versions (not in v0.1)

| Model | Why it is interesting | Limitation |
|---|---|---|
| OmniVoice | `duration=` in seconds (exact length for cutscenes), 600+ languages including uk | **weights CC-BY-NC** (non-commercial): only with explicit consent; the author warns about the prompt language's accent carrying over |
| FireRedTTS3 | Apache-2.0, 24 languages including uk; the best published ru SIM (84.7) | 8.5 GB of weights + a 3.8 GB codec, probably does not fit in 8 GB (not verified); Russian number normalisation must be done separately |
| MOSS-TTS v1.5 | Apache-2.0, duration through `tokens=` | 8B; an 8 GB mode officially exists only for version 1.0 |
| IndexTTS-2 / 2.5 | the best emotion control (a separate emotion reference, an emotion vector) | no Russian; bilibili's own license |
| Fish Audio S2-Pro, Higgs TTS 3 | reference-grade quality | non-commercial licenses, ≥ 24 GB, Linux/WSL |
| MOSS-TTS-Nano-100M | cloning on the CPU (ONNX), 20 languages including ru, pl, tr | lower quality |

Other licenses worth knowing: F5-TTS and its Russian fine-tunes (weights CC-BY-NC), XTTS-v2 (Coqui Public Model License, non-commercial), Seed-VC (GPL-3.0; also rejected in the listening rounds); GigaAM-v3 (Russian ASR), RUAccent and UTMOSv2 are MIT. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

There is no ready speech-to-speech translation that keeps the voice for Russian (SeamlessExpressive, Hibiki-Zero and Index-Echo-S2ST do not support it), so BabelDub uses a cascade: original text → translation → voice-cloning TTS → RVC.

## Useful commands

```text
babeldub doctor                         GPU, VRAM, driver, tier, installed envs and models
babeldub setup [--engines voxcpm,applio,qc] [--tier 8gb] [--lang ru] [--dry-run]
babeldub models list [--engine voxcpm]
babeldub vc train --chars <ids> [--epochs 20] [--minutes 30]   RVC models of the main characters
babeldub synth --best-of 1              a fast draft (one take per line)
babeldub set tts.engine=qwen3tts-fast   force Qwen3-TTS (CUDA); qwen3tts = the official package
babeldub set tts.engine=chatterbox
babeldub set compute.device=auto        auto | cuda | cpu
babeldub models fetch <name> [--accept-license <name>]
```
