# Merging voice clusters into named characters

Read this when `babeldub cast` reports `merged` > 0, when a merge looks wrong, or when a named character seems to have too few lines.

## 1. Why clusters are merged

Game metadata names the speaker only where the game keeps it: an FMOD event folder (`event:/Dialogue/Act1/Zbyszek/...`), a name in the key, dialogue data. Cutscene audio, timelines and video often have no such hint. Those lines go to voice clustering and come out as `cluster_NN` groups, although they are spoken by the same main characters. Left as clusters, they would get their own references, their own tiers and their own voice baseline, and QA would count them as "another character".

So after clustering, `cast` compares every cluster with every character from metadata:

1. The character's voice centroid is the mean ECAPA embedding of up to 12 of his longest clean originals (at least 1 s, no `has_bed`, `nonverbal`, `alt_take`, `no_audio` flags; at least 2 such lines). These are the same lines QA uses for the character's baseline, so the embeddings are computed once (`cast/embeddings.npz`).
2. The cluster's centroid is the mean embedding of its lines.
3. If the cosine between the cluster's centroid and the closest character's centroid is at least `cast.merge_threshold` (default 0.75), the cluster **is** that character.

Clusters are not merged with each other here: whether two groups of unhinted lines are one voice was already decided by the clustering threshold (0.35, average linkage).

## 2. What a merge changes

| File | Effect |
|---|---|
| `cast/assign.jsonl` | the cluster's lines get the character's `char_id`; `conf` is the cluster-to-character centroid cosine (not 1.0, so they stay distinguishable from metadata lines) |
| `cast/characters.jsonl` | the character's `n_lines` and `speech_s` grow (tiers are computed after the merge); `source` stays `metadata`; `merged_clusters: [{cluster_id, cos, n_lines, speech_s}]` records each merge; the `cluster_NN` row disappears |
| `cast/casting_sheet.md` | the Source column says "metadata + N voice clusters"; the section "Voice clusters merged into characters" lists every merge with its cosine |
| `cast` output | `merged` (count), `merged_into` (`{char_id: [cluster_id, ...]}`), `clusters` (clusters left after merging) |

Cluster numbers are assigned before merging, by speech, so the numbers of the clusters that stay do not change and labels written for them earlier still apply. The merged lines become candidates for the character's references and count in his baseline and centroid in QA (the QA identity check treats them as the character, never as "another character").

## 3. Calibration

Low-Budget Repairs (ECAPA from the `qc` worker): `cast` without merging produced 50 clusters for lines without speaker metadata. Centroid cosines between metadata characters and clusters:

| Pair | Cosine |
|---|---|
| `zbyszek` ~ `cluster_04` | 0.88 |
| `maciek` ~ `cluster_05` | 0.89 |
| every other character-cluster pair | 0.06–0.38 |

`cluster_04` and `cluster_05` were the cutscene lines of Zbyszek and Maciek. The gap between 0.38 and 0.88 is wide; 0.75 sits safely inside it. For comparison, different lines of one actor score 0.44–0.65 against each other, and different actors −0.16–0.28: centroids average the noise of single lines away, so the same voice scores much higher centroid-to-centroid.

## 4. Checking and tuning

- **Check every merge of a main character.** Look at a few of the merged lines: `babeldub query --where char=<char_id>,kind=cutscene --fields id,entry.key,scene,text.src --limit 20` (or without `kind` when the merged lines are dialogue). Forms of address, the grammatical gender of the official translation and the scene must fit the character.
- **A wrong merge** (the lines address the character by name, the gender differs, two family members sound alike, one actor voices two roles): raise the bar, `babeldub set cast.merge_threshold=0.85`, then `babeldub cast`. The cluster comes back as `cluster_NN` for labelling.
- **A missed merge** (a cluster's lines are clearly one named character, but it was not merged: emotional, noisy or very short lines pull the centroid away): label the cluster with the character's name in `cast/labels.agent.jsonl` and say so in the report, or lower the bar slightly (not below 0.5: unrelated character-cluster pairs reached 0.38 on Low-Budget Repairs) and rerun `cast`.
- Every threshold change goes into the final report with its reason.
