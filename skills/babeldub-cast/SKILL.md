---
name: babeldub-cast
description: "Cast a game for dubbing in the original characters' own voices: group voice lines by speaker (game metadata or voice clusters), label characters with name, gender and age, rank them into main, secondary and minor tiers, pick clean voice references and decide who gets a new voice. Use for the cast stage, questions about the game's characters and voice-reference problems. Example requests: 'who speaks in this game', 'list the characters', 'casting sheet', 'which characters should we dub', 'voice references', 'main characters only', 'кто говорит', 'персонажи игры', 'лист кастинга', 'голосовые референсы', 'кого озвучивать', 'главные герои', 'quién habla en el juego', 'qué personajes doblar', 'quels personnages doubler'."
license: MIT
compatibility: "Local agent with shell access; babeldub CLI 0.1.0 (install via the babeldub-doctor skill). Voice clustering and typical-timbre references need the qc engine (babeldub setup --engines qc) and preferably an NVIDIA GPU; without it speakers come from game metadata only."
metadata:
  babeldub-cli: "0.3.0"
---

# BabelDub: casting — who speaks, and in whose voice

This skill covers the `cast` stage. Casting decides the most important thing in a dub: every line must sound **in the voice of its own original character**. A misattribution produces a "wrong voice" or a gender switch in the middle of a scene. That is the worst defect of all, and neither translation nor post-processing can fix it.

The skill complements the `caster` role of the `babeldub-localize` skill (`references/roles/caster.md`); the orchestrator asks the user about characters (`references/questions-defaults.md` in the same skill).

Talk to the user in the user's language. Keep commands, field names and identifiers as they are.

## What `babeldub cast` does

```text
babeldub cast [--refs 1..3] [--min-ref-s 3] [--max-ref-s 12]
```

1. **Characters from metadata.** The speaker comes from the line's `speaker_hint`: the FMOD event folder (`event:/Dialogue/Act1/<Character>/...`), a name token in the key, the game's dialogue data. These characters are reliable (`source: metadata`, confidence 1.0).
2. **Voice clusters** for lines without a speaker, if the `qc` worker is installed (ECAPA embeddings, agglomerative clustering by cosine similarity, threshold 0.35; short lines are attached to the nearest cluster afterwards). A cluster gets the name `cluster_NN` and a confidence; clusters with confidence below 0.6 count as uncertain. Embeddings run on the NVIDIA GPU when one is present (project `compute.device`, default `auto`; override per run with `--device auto|cuda|cpu`).
   **Merging into named characters.** A cluster whose voice centroid is at least `cast.merge_threshold` (default 0.75) similar to the voice centroid of a character from metadata *is* that character — usually his cutscene lines that carry no event-folder hint. Its lines go to him (`conf` in `cast/assign.jsonl` = that cosine), the character keeps `source: metadata` and lists `merged_clusters` in `cast/characters.jsonl`, and the cluster disappears (the other cluster numbers do not change). Clusters are never merged with each other beyond the clustering itself. Details and calibration: `references/merging.md`.
3. **The `unknown` group** holds lines without a speaker that could not be compared (no `qc` worker, or the file could not be read). It is not one voice: it is not ranked and never dubbed as a character.
4. **Tiers** (`tier`): `main`, `secondary`, `minor`, by minutes of speech (`references/tiers.md`).
5. **Selection** by `cast.policy`: `main` (default), `all` or `list`.
6. **References** for the selected characters: up to `--refs` clean original lines of 3–12 s with known text (`cast/refs.jsonl`, copies in `cast/refs/<char_id>/`). Selection rules: `references/ref-selection.md`. After `analyze`, each reference row also carries the line's arousal `bucket` (calm, animated, loud, soft): the voice stage builds the character's long timbre references from its clean lines (the calm/main one, plus one per bucket with enough material); by default every line uses the calm/main one and the delivery tags carry a shout, while `tts.tags_reference=bucket` voices a shout from shouted originals.
7. **Casting sheet** `cast/casting_sheet.md`: a table of characters, their references and warnings.

Output: `characters`, `selected`, `main[]`, `uncertain[]`, `no_ref[]`, `clusters` (left after merging), `merged`, `merged_into{char_id: [cluster_id]}`, `refs`, `warnings[]`, `next`. Exit code 3 means `uncertain[]` or `no_ref[]` is not empty.

Labels the agent has already set survive a repeated `cast` (they carry over by `char_id`).

## Steps

1. **Run** `babeldub cast`. Precondition: the `extract` stage is done (otherwise exit code 2).
2. **Read the output**, not whole files. Read the casting sheet section by section.
3. **If `warnings` contains "qc worker unavailable"** and many lines have no speaker (a large `unknown` group), recommend in the report installing `qc` (`babeldub setup --engines qc`, skill `babeldub-doctor`) and re-running `cast`. Without clusters these lines keep the original voice.
4. **Uncertain characters** (`uncertain[]`, plus unnamed clusters): the caster role labels them.
   - Look at up to 20 lines of the character: `babeldub query --where char=<char_id> --fields id,entry.key,scene,text.src,audio.dur_s --limit 20`.
   - Write `cast/labels.agent.jsonl` (format in `references/labels-format.md`).
   - Run `babeldub cast apply-labels` → output `updated`, `uncertain[]`. Exit code 2 means an invalid line: fix it from `errors[]` (up to 3 iterations).
5. **Check the merges** (`merged_into`, section "Voice clusters merged into characters" of the casting sheet): a merged cluster must really be that character. If its lines address someone else by name or use the other grammatical gender, raise the bar (`babeldub set cast.merge_threshold=0.85`) and rerun `babeldub cast` (`references/merging.md`).
6. **Check the references of the main characters** (caster role): the reference text for timbre must be neutral, with no shouting, whispering, laughter or music. Record a bad reference in the character's `notes` and in the report (`references/ref-selection.md`, section 4).
7. **Question 2 to the user** (the orchestrator asks it): all / main / a list. Show the table from the casting sheet: name, gender, lines, minutes of speech, tier. The default is main.
8. **Apply the answer:**
   - `babeldub cast select --policy main` or `--policy all`;
   - `babeldub cast select --chars <char_id or name>,<...>` for a list.
   Output: `selected[]`. Characters that are not selected keep their original voice.

## How to tell casting succeeded

- Every selected character has at least one reference (`no_ref[]` is empty).
- `uncertain[]` is empty, or every remaining uncertain character is explained in `human_pending` (for example, a cluster mixed two speakers).
- The main characters have names both in the original language and in the target language (`name_tgt`); the glossary takes them over (skill `babeldub-translate`).
- Every main character has a gender. It drives the grammar of the translation (gender of past-tense verbs and adjectives) and the "gender switch" check in QA.

## Errors and what to do

| Symptom | Cause | Action |
|---|---|---|
| exit code 2 "no voiced lines with audio" | `extract` produced no lines with audio | go back to recon (skill `babeldub-recon`) |
| `no_ref[]` is not empty | the character has no usable lines even after relaxing the rules | note it in the report; the character's lines are voiced from their own originals (`line`) where those are usable |
| `warnings`: "no ideal reference, rules relaxed" | the reference is shorter than 3 s, longer than 12 s, or has no text | check it in the casting sheet; if the timbre is bad, record it in `notes` and the report |
| `warnings`: "cast.list names not found" | a name from the user's list did not match | `babeldub cast select --chars` with the `char_id` from the casting sheet |
| one "character" speaks in different voices | the metadata is wrong (event folder ≠ speaker) or a cluster merged several actors | `notes` + `human_pending`; do not split the canon by hand |
| a merged cluster is not that character (`merged_into`, casting sheet) | two voices are close (one actor, family members) | `babeldub set cast.merge_threshold=0.85`, rerun `babeldub cast` |
| a named character has many unhinted lines in a separate cluster that was not merged | its centroid scored below `cast.merge_threshold` (noisy or emotional lines) | label the cluster with the character's name (`labels.agent.jsonl`), or lower the bar slightly (not below 0.5) and rerun `cast` |

## Rules

1. The game folder is read-only.
2. Only the CLI writes the canon (`cast/characters.jsonl`, `cast/assign.jsonl`, `cast/refs.jsonl`, `cast/casting_sheet.md`). The agent writes only `cast/labels.agent.jsonl`.
3. Do not read project files whole: use `query` with `--fields` and `--limit` (up to 20 lines per character), and read the casting sheet section by section.
4. You cannot hear audio. Determine gender and age from text (forms of address, grammar of the official translation, keys), not "by ear".
5. Do not swap a reference by copying files manually: choosing references is the job of `cast`.

## References

- How the CLI picks references, what makes a good reference, where the delivery of a line comes from: [references/ref-selection.md](references/ref-selection.md). Read it when checking references and when `no_ref[]` is not empty.
- Character tiers, `cast.policy`, `main_share`, what to tell the user about the choice: [references/tiers.md](references/tiers.md). Read it before question 2.
- Format of `cast/labels.agent.jsonl`, rules for names, gender, age and notes: [references/labels-format.md](references/labels-format.md). Read it before labelling.
- How voice clusters are merged into named characters, the measured numbers, how to tune `cast.merge_threshold`: [references/merging.md](references/merging.md). Read it when `merged` > 0 or a named character seems to have too few lines.
