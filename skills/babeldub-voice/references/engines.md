# Synthesis engines: what v0.1 has, hardware tiers, licenses

Read this when choosing an engine for a language, when video memory is short, and for license questions. The source of truth about what is actually installed and available is `babeldub models list` and `babeldub doctor`; this page explains why the catalog looks the way it does.

Markers: **[v0.1]** means present in CLI v0.1 (engine, worker, model in the catalog); **[candidate]** means studied, but there is no worker in v0.1; "❓" means not verified.

## 1. What v0.1 can do

| Engine (`tts.engine`) | Catalog models | Languages | Weights license | Notes |
|---|---|---|---|---|
| `voxcpm` **[v0.1, default on NVIDIA GPUs with 8 GB+]** | `VoxCPM2` (openbmb/VoxCPM2, 2B, 48 kHz) | ar, my, zh, da, nl, en, fi, fr, de, el, he, hi, id, it, ja, km, ko, lo, ms, no, pl, pt, ru, es, sw, sv, tl, th, tr, vi | Apache-2.0 | timbre from `reference_wav_path` (the character's long original reference) + a target-language continuation prompt (`prompt_wav_path` + `prompt_text`): `ref_strategy=bootstrap`; CUDA only; plain text (catalog `stress: upper`: with `tts.stress=on` a homograph's stressed vowel in CAPITALS) |
| `applio` **[v0.1, stage `vc`]** | one RVC v2 model per character, trained by `babeldub vc train` | any | MIT (Applio, RVC) | not a TTS: converts the timbre of the finished dubs to the character's; pretrained HiFi-GAN 40 kHz, rmvpe f0 |
| `qwen3tts-fast` **[v0.1, GPUs without VoxCPM2]** | the `qwen3tts` models (same folders) | zh, en, ja, ko, de, fr, ru, pt, es, it | Apache-2.0 weights; runtime `faster-qwen3-tts` (MIT) | the same Qwen3-TTS through CUDA graphs: ~7× faster per line, identical output for the same seed; CUDA only, one line per call |
| `qwen3tts` **[v0.1, on the CPU and as the fallback]** | `Qwen3-TTS-12Hz-1.7B-Base` (also for `ru`); fallback `Qwen3-TTS-12Hz-0.6B-Base`; `qwen3-tts-12hz-1.7b-ru-stress-cf` only explicitly via `tts.model` | zh, en, ja, ko, de, fr, ru, pt, es, it | Apache-2.0 | official `qwen-tts` package; ICL cloning: prompt audio + its text; emotion comes from the prompt |
| `chatterbox` **[v0.1]** | `chatterbox-multilingual` (Multilingual V3) | ar, da, de, el, en, es, fi, fr, he, hi, it, ja, ko, ms, nl, no, pl, pt, ru, sv, sw, tr, zh | MIT | cloning from audio without a transcript; `exaggeration`; PerTh watermark |
| `dummy` | — | any | MIT | test engine: writes a tone of the required length; only for checking the pipeline |

Every engine has its own venv in `BABELDUB_HOME/envs/<engine>`, because the packages conflict (qwen-tts pins one transformers version, Chatterbox pins torch 2.6.0 and a different transformers). `babeldub setup --engines <engine>` installs them (skill `babeldub-doctor`).

Choosing: `babeldub set tts.engine=<engine>`, or for one run `babeldub synth --engine <engine>`. The model within the engine: `babeldub set tts.model=<name>`; without it the CLI takes the catalog model by tier (`tts.tier=auto`) and language.

`init` writes `tts.engine=auto`. `synth` then runs `voxcpm` when the device is CUDA, its env exists and it speaks the language; otherwise the first of `qwen3tts-fast`, `qwen3tts`, `chatterbox` that speaks it, and says why in `notes`. A CUDA-only engine falls back along its chain off CUDA (`voxcpm` → `qwen3tts-fast` → `qwen3tts`), and to the next installed engine of the chain when its env is missing. `setup` installs `voxcpm` and `applio` on GPUs with 8 GB+ (7.5 GB reported), `qwen3tts-fast` on smaller GPUs, and `qwen3tts` on the CPU. Lines already voiced by `qwen3tts-fast` count as done for `qwen3tts` and back.

## 2. Language → engine (permissive licenses only)

| Target language | 8 GB+ GPU | smaller GPU / CPU | Comment |
|---|---|---|---|
| ru, en, de, fr, es, pt, it, zh, ja, ko | `voxcpm` + RVC | `qwen3tts-fast` / `qwen3tts` | per-language prompts matter: the bootstrap prompt is in the target language |
| pl, tr, ar, da, el, fi, he, hi, ms, nl, no, sv, sw | `voxcpm` + RVC | `chatterbox` | Chatterbox puts a watermark in every file: tell the user |
| my, id, km, lo, tl, th, vi | `voxcpm` + RVC | none | VoxCPM2 only |
| uk and others | none | none | say so honestly: v0.1 has no engine with a permissive license; candidates are in section 6 |

## 3. VoxCPM2 and RVC: what matters

- **Why this chain.** Blind listening rounds on Low-Budget Repairs (Zbyszek, English → Russian) chose VoxCPM2 + bootstrap prompts + best of 3 + RVC ("V-ru") over Qwen3-TTS + RVC, Seed-VC and fine-tunes. Measured with the QA models: CER 0.045 (Qwen3-TTS + RVC 0.050); ECAPA similarity to the originals ~0.65–0.70, where the originals among themselves reach 0.77.
- **Per-language reference.** A source-language reference alone gives the target-language speech the original's accent; the target-language continuation prompt (the character's own accepted dubs) removes it while the long original reference keeps the timbre. That is the bootstrap (`references/ref-strategy.md`, section 1).
- **Plain text.** VoxCPM2 must get text without U+0301 stress marks (it voices them) and without `+` (it reads "plus"); the CLI strips them. Its catalog capability `stress: upper` lets `tts.stress=on` send a CAPITAL stressed vowel for homographs (A/B: +0.02 CER, plain text still best on average, so it is off by default). Engines declare `stress: none | upper | acute` in `models.yaml`; the `ru-stress-cf` model is `acute`.
- **VRAM and speed.** Peak 6.5 GB on an RTX 3060 Ti (8 GB): one model on the GPU at a time. `best_of=3` triples the synthesis time; `--best-of 1` gives a faster draft. In `tts.best_of_mode=score` (the default with the qc env) the TTS model and the QA models never share the GPU: `synth` keeps every candidate, the TTS worker exits, then the qc worker scores the candidates (`synth score`).
- **RVC (Applio).** One model per main character, trained on 2–20 s clean originals (up to 30 min): 20 epochs by default (50 epochs on 30 min took 37 min on the 3060 Ti). It transfers the timbre to every dub of the character in any language. Training needs the GPU; conversion runs on the GPU when present.
- **Rejected.** Seed-VC (zero-shot VC) hallucinated words and sounds; Qwen3-TTS Base inserts sounds for stress marks; the `ru-stress-cf` fine-tune keeps the original voice worse.

## 4. Qwen3-TTS: what matters

- **Checkpoints.** 1.7B-Base (≈4.5 GB) and 0.6B-Base (≈2.5 GB); only the 12 Hz variants are published. The CustomVoice and VoiceDesign variants (presets and voice from a description) are not needed for dubbing: they cannot clone a specific character.
- **Cloning.** `ref_audio` + `ref_text` (ICL). The transcript must be exact: with a transcript that does not match the audio (an alternative take, a rewritten subtitle) generation can loop until the token cap. Without a transcript (`x_vector_only_mode`, `line-xvec` in the results) the model clones only the timbre from the embedding and loses some delivery; the CLI uses this mode when the prompt has no text, was trimmed, fails the transcript-rate check, or ran away in ICL mode (`references/ref-strategy.md`, section 2.1).
- **Emotions.** Base has no text style instructions: emotion and paralinguistics come from the prompt. Hence the `line` strategy (`references/ref-strategy.md`).
- **Duration.** There is no explicit duration parameter. Length is held by the syllable budget during translation, compression in `post` and shortening of the text (`references/length-policy.md`).
- **Quality as self-reported by the authors** (MiniMax Multilingual set): Russian WER 3.21 and SIM 0.792 for 1.7B; 4.46 and 0.781 for 0.6B. These are the model authors' numbers, not an independent measurement; for the voices of a specific game, look at the pilot metrics.
- **Russian stress.** The base model follows the U+0301 stress mark in 65% of cases on the dictionary vowel and in 17% against the dictionary; the `ru-stress-cf` fine-tune follows it in 100% and 96% (measured by the fine-tune's author). But on three Low-Budget Repairs lines it preserved the original actor's voice worse (ECAPA similarity to the original 0.05–0.37 versus 0.37–0.51 for 1.7B-Base), so `setup` does not install it and `synth` never picks it by itself: enable it explicitly (`babeldub models fetch …`, `babeldub set tts.model=…`) and check `sim` in the pilot. A caveat from the model card: the weights are Apache-2.0, but part of the training text comes from a dataset with no declared license; check this before commercial use.
- **Speed.** The official `qwen-tts` package is slow. On an RTX 3060 Ti (8 GB, 1.7B-Base, Low-Budget Repairs lines) BabelDub measured: `qwen3tts-fast` (`faster-qwen3-tts` 0.5.4, CUDA graphs) `rtf` ≈ 0.52 per line with a VRAM peak ≈ 4.5 GB and ~15 s of load and warm-up per run; the official `qwen3tts` ≈ 3.7–4.5 for a single line and ≈ 1.2–1.4 for a batch of 4 (VRAM peak up to 5.5 GB). Same audio for the same seed, same Whisper CER (0.0 and 0.074 on the test lines). `qwen3tts-fast` needs its own venv because it pins `transformers==5.15.1` (5.20.x breaks the codec config). Check `rtf` in the `synth` output.
- **Runaways.** One looping line in a batch of the official package forces the whole batch to the token cap (VRAM 7.8 of 8 GB, RTF ~4 in one LBR batch). `synth` caps every line from its own estimate and the original, groups batches by estimated length, and retries a looping line alone (`references/ref-strategy.md`, section 2.1).
- **Windows.** FlashAttention 2 is not shipped for Windows; `sdpa` is used.

## 5. Video-memory tiers

| Tier | What `setup` installs by default | What to expect |
|---|---|---|
| CPU | `qwen3tts` with `Qwen3-TTS-12Hz-0.6B-Base` | works, but many times slower than real time; no RVC; warn that dubbing the game will take hours ❓ |
| GPU under 7.5 GB | `qwen3tts-fast` with 1.7B-Base, Chatterbox V3 for its languages | VoxCPM2 (6.5 GB peak) does not fit; no RVC |
| 8 GB | `voxcpm` with VoxCPM2, `applio` (RVC), `qc` | the main scenario; one model on the GPU at a time |
| 12–24 GB | the same | memory headroom; **[candidates]** FireRedTTS3, MOSS-TTS with a duration parameter |

VRAM from the catalog and measurements: VoxCPM2 6.5 GB (measured), Qwen3-TTS 1.7B ≈ 6 GB, 0.6B ≈ 4 GB, Chatterbox ≈ 7 GB (a community figure for the 2025 English Chatterbox; not verified for V3 ❓). On Windows the desktop takes part of the memory. See the actual peak in `vram_peak_gb` of the `synth` output.

On one GPU, synthesis, conversion and QA do not run at the same time: the CLI holds `state/locks/gpu.lock`, and models load in separate processes one after another.

## 6. Candidates for future versions (not in v0.1)

| Model | Why it is interesting | Limitation |
|---|---|---|
| OmniVoice | `duration=` parameter in seconds (exact length for cutscenes), 600+ languages including uk | **CC-BY-NC weights** (non-commercial); the author explicitly warns that the accent of the prompt language carries over |
| FireRedTTS3 | Apache-2.0, 24 languages including uk; best published ru SIM (84.7) | weights 8.5 GB + codec 3.8 GB, most likely does not fit into 8 GB ❓; Russian number normalization must be done by us |
| MOSS-TTS v1.5 | Apache-2.0, duration via `tokens=` | 8B; an 8 GB mode officially exists only for version 1.0 |
| IndexTTS-2 / 2.5 | best emotion control (separate emotion reference, emotion vector) | no Russian; bilibili's own license |
| Fish Audio S2-Pro, Higgs TTS 3 | reference-grade quality | non-commercial licenses, ≥24 GB, Linux/WSL |
| MOSS-TTS-Nano-100M | cloning on the CPU (ONNX), 20 languages including ru, pl, tr | lower quality |

There is no ready-made speech-to-speech translation that preserves the voice for Russian (SeamlessExpressive, Hibiki-Zero and Index-Echo-S2ST do not support Russian), so BabelDub uses a cascade: original text → translation → voice-cloning TTS.

## 7. Licenses and consent

1. **By default, only Apache-2.0 and MIT.** Everything in section 1 qualifies (VoxCPM2 Apache-2.0, Applio/RVC MIT; the RVC models are trained by the user on the game's own lines and stay on the user's machine).
2. **Non-commercial (NC) models** are not part of v0.1. When they arrive, enable them only after showing the license to the user and getting an explicit "yes"; the catalog will mark them `commercial: false`, and `setup` will install such a model only with a separate confirmation.
3. **The license of every model used** goes into `build/build_manifest.json` (`models[]`) and into the final report.
4. **The Chatterbox watermark** (PerTh) is inaudible but present in every file; tell the user if the engine was used.
5. **Actors' voices.** Cloning the voices of voice actors is restricted by the laws of many countries and by the terms of several models; the game's EULA may prohibit modification. BabelDub is a tool for personal fan localization; built game files are not distributed.
6. BabelDub does not distribute weights: `babeldub setup` downloads them to the user's machine, and the user accepts the model terms.
