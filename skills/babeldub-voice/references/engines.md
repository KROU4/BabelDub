# Synthesis engines: what v0.1 has, hardware tiers, licenses

Read this when choosing an engine for a language, when video memory is short, and for license questions. The source of truth about what is actually installed and available is `babeldub models list` and `babeldub doctor`; this page explains why the catalog looks the way it does.

Markers: **[v0.1]** means present in CLI v0.1 (engine, worker, model in the catalog); **[candidate]** means studied, but there is no worker in v0.1; "❓" means not verified.

## 1. What v0.1 can do

| Engine (`tts.engine`) | Catalog models | Languages | Weights license | Notes |
|---|---|---|---|---|
| `qwen3tts` **[v0.1, default]** | `Qwen3-TTS-12Hz-1.7B-Base` (also for `ru`); fallback `Qwen3-TTS-12Hz-0.6B-Base`; `qwen3-tts-12hz-1.7b-ru-stress-cf` only explicitly via `tts.model` | zh, en, ja, ko, de, fr, ru, pt, es, it | Apache-2.0 | ICL cloning: prompt audio + its text; emotion comes from the prompt |
| `chatterbox` **[v0.1]** | `chatterbox-multilingual` (Multilingual V3) | ar, da, de, el, en, es, fi, fr, he, hi, it, ja, ko, ms, nl, no, pl, pt, ru, sv, sw, tr, zh | MIT | cloning from audio without a transcript; `exaggeration`; PerTh watermark |
| `dummy` | — | any | MIT | test engine: writes a tone of the required length; only for checking the pipeline |

Every engine has its own venv in `BABELDUB_HOME/envs/<engine>`, because the packages conflict (qwen-tts pins one transformers version, Chatterbox pins torch 2.6.0 and a different transformers). `babeldub setup --engines <engine>` installs them (skill `babeldub-doctor`).

Choosing: `babeldub set tts.engine=<engine>`, or for one run `babeldub synth --engine <engine>`. The model within the engine: `babeldub set tts.model=<name>`; without it the CLI takes the catalog model by tier (`tts.tier=auto`) and language.

## 2. Language → engine (8 GB VRAM, permissive licenses only)

| Target language | v0.1 engine | Comment |
|---|---|---|
| ru | `qwen3tts` (1.7B-Base; `ru-stress-cf` only explicitly) | stress via the U+0301 mark (see section 3) |
| en, de, fr, es, pt, it, zh, ja, ko | `qwen3tts` | Apache-2.0 |
| pl, tr, and also ar, da, el, fi, he, hi, ms, nl, no, sv, sw | `chatterbox` | MIT; watermark in every file: tell the user |
| uk and others | none | say so honestly: v0.1 has no engine with a permissive license; candidates are in section 5 |

## 3. Qwen3-TTS: what matters

- **Checkpoints.** 1.7B-Base (≈4.5 GB) and 0.6B-Base (≈2.5 GB); only the 12 Hz variants are published. The CustomVoice and VoiceDesign variants (presets and voice from a description) are not needed for dubbing: they cannot clone a specific character.
- **Cloning.** `ref_audio` + `ref_text`. Without a transcript (`x_vector_only_mode`) the model clones only the timbre from the embedding and sounds worse; the CLI enables this mode itself when the prompt has no text or the prompt was trimmed.
- **Emotions.** Base has no text style instructions: emotion and paralinguistics come from the prompt. Hence the `line` strategy (`references/ref-strategy.md`).
- **Duration.** There is no explicit duration parameter. Length is held by the syllable budget during translation, compression in `post` and shortening of the text (`references/length-policy.md`).
- **Quality as self-reported by the authors** (MiniMax Multilingual set): Russian WER 3.21 and SIM 0.792 for 1.7B; 4.46 and 0.781 for 0.6B. These are the model authors' numbers, not an independent measurement; for the voices of a specific game, look at the pilot metrics.
- **Russian stress.** The base model follows the U+0301 stress mark in 65% of cases on the dictionary vowel and in 17% against the dictionary; the `ru-stress-cf` fine-tune follows it in 100% and 96% (measured by the fine-tune's author). But on three Low-Budget Repairs lines it preserved the original actor's voice worse (ECAPA similarity to the original 0.05–0.37 versus 0.37–0.51 for 1.7B-Base), so `setup` does not install it and `synth` never picks it by itself: enable it explicitly (`babeldub models fetch …`, `babeldub set tts.model=…`) and check `sim` in the pilot. A caveat from the model card: the weights are Apache-2.0, but part of the training text comes from a dataset with no declared license; check this before commercial use.
- **Speed.** The official `qwen-tts` package is slow: about 0.23× real time on an RTX 4060 under Windows. The `faster-qwen3-tts` accelerator (CUDA Graphs) on the same card gives 1.83× real time for 1.7B and 2.26× for 0.6B (measured by the accelerator's author). The v0.1 catalog uses the official `qwen-tts`; `faster-qwen3-tts` is a **[candidate]**: it needs a different transformers version and cannot be installed into the same venv. On an RTX 3060 Ti (8 GB, official `qwen-tts`, 1.7B-Base), BabelDub measured `rtf` ≈ 4.5 for a single line and ≈ 1.4–2.1 for a batch of 3–4 lines, with a VRAM peak of 4.3–5.5 GB; check `rtf` in the `synth` output.
- **Windows.** FlashAttention 2 is not shipped for Windows; `sdpa` is used.

## 4. Video-memory tiers

| Tier | What `setup` installs by default | What to expect |
|---|---|---|
| CPU | `Qwen3-TTS-12Hz-0.6B-Base` | works, but many times slower than real time; warn that dubbing the game will take hours ❓ |
| 8 GB | 1.7B-Base, Chatterbox V3 | the main scenario; one model on the GPU at a time; on `CUDA out of memory`, use 0.6B |
| 12 GB | the same | memory headroom; a second engine for comparison is a **[candidate]**: VoxCPM2 |
| 16–24 GB | the same | **[candidates]** VoxCPM2, FireRedTTS3 (best published SIM for Russian), MOSS-TTS with a duration parameter |

VRAM estimates from the catalog: 1.7B ≈ 6 GB, 0.6B ≈ 4 GB, Chatterbox ≈ 7 GB (a community figure for the original 2025 English Chatterbox; not verified for V3 ❓). On Windows the desktop takes part of the memory. See the actual peak in `vram_peak_gb` of the `synth` output.

On one GPU, synthesis and QA do not run at the same time: the CLI holds `state/locks/gpu.lock`, and models load in separate processes one after another.

## 5. Candidates for future versions (not in v0.1)

| Model | Why it is interesting | Limitation |
|---|---|---|
| OmniVoice | `duration=` parameter in seconds (exact length for cutscenes), 600+ languages including uk | **CC-BY-NC weights** (non-commercial); the author explicitly warns that the accent of the prompt language carries over |
| VoxCPM2 | Apache-2.0, 30 languages, 48 kHz; ru SIM 81.1 in the authors' table | ~8 GB VRAM: a tight fit on an 8 GB card; seed only in the GitHub version |
| FireRedTTS3 | Apache-2.0, 24 languages including uk; best published ru SIM (84.7) | weights 8.5 GB + codec 3.8 GB, most likely does not fit into 8 GB ❓; Russian number normalization must be done by us |
| MOSS-TTS v1.5 | Apache-2.0, duration via `tokens=` | 8B; an 8 GB mode officially exists only for version 1.0 |
| IndexTTS-2 / 2.5 | best emotion control (separate emotion reference, emotion vector) | no Russian; bilibili's own license |
| Fish Audio S2-Pro, Higgs TTS 3 | reference-grade quality | non-commercial licenses, ≥24 GB, Linux/WSL |
| MOSS-TTS-Nano-100M | cloning on the CPU (ONNX), 20 languages including ru, pl, tr | lower quality |

There is no ready-made speech-to-speech translation that preserves the voice for Russian (SeamlessExpressive, Hibiki-Zero and Index-Echo-S2ST do not support Russian), so BabelDub uses a cascade: original text → translation → voice-cloning TTS.

## 6. Licenses and consent

1. **By default, only Apache-2.0 and MIT.** Everything in section 1 qualifies.
2. **Non-commercial (NC) models** are not part of v0.1. When they arrive, enable them only after showing the license to the user and getting an explicit "yes"; the catalog will mark them `commercial: false`, and `setup` will install such a model only with a separate confirmation.
3. **The license of every model used** goes into `build/build_manifest.json` (`models[]`) and into the final report.
4. **The Chatterbox watermark** (PerTh) is inaudible but present in every file; tell the user if the engine was used.
5. **Actors' voices.** Cloning the voices of voice actors is restricted by the laws of many countries and by the terms of several models; the game's EULA may prohibit modification. BabelDub is a tool for personal fan localization; built game files are not distributed.
6. BabelDub does not distribute weights: `babeldub setup` downloads them to the user's machine, and the user accepts the model terms.
