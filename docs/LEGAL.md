# Responsible use of BabelDub

BabelDub is a tool for personal localization of games you have legally acquired. It runs entirely on your computer and changes only the files of your copy of the game. This document explains where the limits are. It is not legal advice: laws differ between countries, and you are responsible for how you use the tool.

## In short

- Use BabelDub only with a **legal copy** of the game and only for **personal use** or **non-commercial fan translations**.
- **Do not distribute game files**: neither the original nor the rebuilt banks and archives, nor extracted audio, nor text tables.
- **Do not distribute synthesized actor voices** without the consent of the actors and of the rights holder.
- You may share **instructions and patch scripts**: a description of how to get the result on your own copy of the game, a glossary and your own translation, if the rights holder does not object.
- Respect **model licenses**: some allow only non-commercial use.

## The game's license agreement (EULA)

Many EULAs forbid modifying, decompiling or distributing modified files, and online games forbid any tampering with the client. Before localizing a game:

1. Read the game's EULA and modding rules. Many developers explicitly allow fan translations and mods; some do not.
2. Do not use BabelDub with online games, anti-cheat systems or competitive modes: modified files can get your account banned.
3. BabelDub does not bypass copy protection or DRM and does not run the game's executables. If the game is protected by encryption that would have to be cracked, the tool is not meant for it.

Installation always makes a backup: `babeldub uninstall` restores the original files and checks their checksums. A Steam file integrity check also restores the originals; after it, `babeldub reapply` installs the localization again.

## Actor voices and consent

BabelDub clones the voices of the original characters, which means the voices of real people: voice actors. In many countries a person's voice is protected by personality and publicity rights, by personal data laws and by the actors' contracts with the publisher.

- **Personal use.** Re-voicing your copy of a game for yourself is BabelDub's main scenario. The synthesized files stay with you.
- **Publishing.** Publish videos, streams and builds with synthesized actor voices only when the game's rights holder, the actors themselves and the platform rules allow it. Label such content as AI-generated.
- **Not acceptable:** passing synthesis off as a real recording of the actor; using a cloned voice to create lines that were not in the game for deception, defamation, advertising or political material; using the voices to train other models.
- Some model licenses explicitly forbid imitating the voice of a recognisable person without consent or using the outputs to train other models. These terms apply in addition to the law.

If you are an actor or a rights holder and believe BabelDub is being used against your rights, write in [issues](https://github.com/KROU4/BabelDub/issues) or privately through [Security Advisories](https://github.com/KROU4/BabelDub/security/advisories/new).

## What you may and may not distribute

| You may (unless the rights holder forbids it) | You may not |
|---|---|
| A description of which BabelDub commands to run for a particular game and version | Original game files, extracted banks, audio and texts |
| Your translation as a patch or table that is applied to the user's own copy | Rebuilt game files (`.bank`, `.pak`, `.assets` and so on) |
| A glossary, a style guide, project settings without game data | Synthesized lines in actors' voices without their consent |
| Compatibility reports for games and formats | Ready-made "dubs" for download |

The BabelDub project follows these rules itself: the repository, the releases and the tests contain no game files and no actor voices, and the tests run on synthetic data.

## Model licenses

BabelDub does not distribute model weights: `babeldub setup` downloads them from Hugging Face and shows each model's license. The default models and engines (VoxCPM2, Applio/RVC, Qwen3-TTS, Chatterbox Multilingual, faster-whisper, SpeechBrain ECAPA) are released under permissive licenses (Apache-2.0, MIT). The RVC voice models are trained on your machine from the game's own lines (`babeldub vc train`), stay in the project folder and must not be distributed: they reproduce the voice actors' timbre. Models with non-commercial licenses (for example, CC-BY-NC) are not in the default set and are installed only with your explicit consent; their output must not be used commercially.

Some models have extra terms:

- Chatterbox (Resemble AI) embeds an inaudible PerTh watermark in every file; do not remove it;
- the Russian stress fine-tune of Qwen3-TTS was trained partly on texts without a declared license; the author asks you to check this before commercial use.

The full list with licenses is in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), in [docs/MODELS.md](https://github.com/KROU4/BabelDub/blob/main/docs/MODELS.md) and in the output of `babeldub models list`.

## External tools

ffmpeg, vgmstream and Rubber Band are separate programs with their own licenses (LGPL/GPL, ISC-style, GPL). BabelDub does not include them in its code and does not distribute them: it calls the programs installed on your machine or downloads the official builds into `BABELDUB_HOME`.

## Disclaimer

BabelDub is distributed under the MIT license "as is", without any warranty. The authors are not liable for damaged game files, banned accounts, EULA violations or infringement of third-party rights resulting from the use of the tool.
