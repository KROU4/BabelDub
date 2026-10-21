# Character tiers and choosing whom to dub

Read this before question 2 to the user (characters) and when configuring `cast.policy`.

## 1. Tiers (`tier`)

`babeldub cast` ranks characters by total speech duration (if durations are unknown, by number of lines):

| Tier | How it is computed | What it usually is |
|---|---|---|
| `main` | the smallest set of the most "talkative" characters that covers `cast.main_share` of all speech (default 0.7, i.e. 70%) | the hero, the sidekick, the main antagonist |
| `secondary` | the next characters with a speech share of at least 2% | prominent NPCs, merchants, quest givers |
| `minor` | everyone else | bit parts, passers-by, "barks" |

The `unknown` group (lines without a speaker that could not be compared by voice) takes no part in ranking: it is a mix of voices. Voice clusters merged into a named character (`references/merging.md`) count as that character's speech, so tiers are computed after the merge: a hero whose cutscenes have no speaker hint still ranks by all of his lines.

Example: in Low-Budget Repairs, according to the game analysis, 996 lines are tied to 34 characters; Zbyszek has 49.7 min of speech, Maciek 16.0 min, everyone else up to 3 min. With `main_share` = 0.7, two or three characters become main, and they account for most of the dub. This is only an illustration: another game will have its own distribution.

## 2. Selection policy (`cast.policy`)

| Policy | Whom we dub | When |
|---|---|---|
| `main` (default) | only `main` | a quick result with minimal defects; everyone else sounds in the original |
| `all` | everyone except `unknown` | full localization; longer synthesis and more lines to review |
| `list` | the characters in `cast.list` | the user named specific characters |

Commands:

```text
babeldub cast select --policy main
babeldub cast select --policy all
babeldub cast select --chars zbyszek,maciek
```

`--chars` takes `char_id` values or names separated by commas and switches the policy to `list`. The share for `main` is set beforehand: `babeldub set cast.main_share=0.8`, then `babeldub cast` (tiers are recomputed).

## 3. How to ask the user

The orchestrator asks the question (skill `babeldub-localize`, `references/questions-defaults.md`), in the user's language. Prepare a short table for it from the casting sheet:

| Character | Gender | Lines | Minutes | Tier |
|---|---|---|---|---|

and three options: "main (default, N characters, X% of speech)", "all (M characters)", "a list". Do not retell the technical details of clustering.

## 4. What to consider when choosing

- **Mixed dub.** With `main`, secondary characters sound in the original language. That is normal for a fan localization, but the report must say so.
- **Few lines mean worse cloning.** Bit-part characters have 2–10 lines: the reference is short or relaxed, and voice similarity is lower. With `all`, expect more `keep_original` among `minor` characters.
- **One actor, several characters.** A cluster may merge different characters voiced by the same actor: the timbre matches, but the name and speech style do not. Separating them is a job for labelling (`labels`), not for selection.
- **`unknown` is never dubbed as a whole.** If it holds a lot of speech, install `qc` and re-run `cast` instead of selecting it.
