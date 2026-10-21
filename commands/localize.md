---
description: Localize a game and dub it in the voices of its original characters (BabelDub)
argument-hint: "<path to game> [language]"
---

Activate the `babeldub-localize` skill and run it for these arguments: $ARGUMENTS

- The first argument is the path to the game folder (a path with spaces may arrive unquoted: the path is everything before the language code). The second, optional one is the target language (`ru`, `en`, `de` …).
- If the path is missing, ask for it. If the language is missing, that is question 1 of the skill.
- From here on follow the `babeldub-localize` skill strictly: exactly two questions to the user (target language; which characters: all / main / a list), answers in the user's language, everything else automatic; GPU-first; dub in the original hero's voice; the game folder stays read-only until `babeldub install`.
