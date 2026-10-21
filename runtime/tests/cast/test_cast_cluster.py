from __future__ import annotations

from itertools import combinations
from pathlib import Path

import numpy as np
import pytest

from babeldub.cast.cluster import (
    EmbeddingStore,
    agglomerative,
    cluster_voices,
    l2_normalize,
    wav_signature,
)


def _naive_average_linkage(emb: np.ndarray, threshold: float) -> list[set[int]]:
    """Reference implementation: repeatedly merge the most similar pair (average cosine)."""
    x = l2_normalize(emb)
    sim = x @ x.T
    clusters = [{i} for i in range(len(x))]
    while len(clusters) > 1:
        best, pair = -np.inf, None
        for a, b in combinations(range(len(clusters)), 2):
            s = np.mean([sim[i, j] for i in clusters[a] for j in clusters[b]])
            if s > best:
                best, pair = s, (a, b)
        if best < threshold:
            break
        a, b = pair
        clusters[a] |= clusters[b]
        del clusters[b]
    return clusters


def _partition(labels: np.ndarray) -> set[frozenset[int]]:
    groups: dict[int, set[int]] = {}
    for i, lab in enumerate(labels.tolist()):
        groups.setdefault(lab, set()).add(i)
    return {frozenset(g) for g in groups.values()}


def _speakers(n_per: list[int], spread: float, seed: int = 1, dim: int = 64) -> tuple[np.ndarray, list[int]]:
    rng = np.random.default_rng(seed)
    rows, truth = [], []
    for spk, n in enumerate(n_per):
        c = rng.standard_normal(dim)
        c /= np.linalg.norm(c)
        for _ in range(n):
            noise = rng.standard_normal(dim)
            rows.append(c + spread * noise / np.linalg.norm(noise))
            truth.append(spk)
    return np.stack(rows).astype(np.float32), truth


def test_agglomerative_recovers_speakers() -> None:
    emb, truth = _speakers([12, 7, 4], spread=0.5)
    labels = agglomerative(emb, threshold=0.45)
    assert _partition(labels) == _partition(np.array(truth))


def test_agglomerative_threshold_controls_granularity() -> None:
    emb, _ = _speakers([6, 6], spread=0.5)
    assert len(set(agglomerative(emb, threshold=0.999).tolist())) == len(emb)
    assert len(set(agglomerative(emb, threshold=-1.0).tolist())) == 1


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_agglomerative_matches_naive(seed: int) -> None:
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal((18, 8)).astype(np.float32)
    for threshold in (-0.2, 0.0, 0.2, 0.4):
        got = _partition(agglomerative(emb, threshold))
        want = {frozenset(c) for c in _naive_average_linkage(emb, threshold)}
        assert got == want


def test_agglomerative_edge_cases() -> None:
    assert agglomerative(np.zeros((0, 4), np.float32)).tolist() == []
    assert agglomerative(np.ones((1, 4), np.float32)).tolist() == [0]


def test_cluster_voices_attaches_short_lines() -> None:
    emb, truth = _speakers([5, 5], spread=0.4, seed=3)
    keys = [f"l{i}" for i in range(len(emb))]
    durations = {k: 4.0 for k in keys}
    durations["l0"] = 0.5  # too short to build clusters, still assigned by nearest centroid
    durations["l9"] = 0.6
    clusters = cluster_voices(dict(zip(keys, emb, strict=True)), durations, threshold=0.45)
    by_key = {k: n for n, cl in enumerate(clusters) for k in cl.members}
    assert len(clusters) == 2
    assert by_key["l0"] == by_key["l1"]
    assert by_key["l9"] == by_key["l8"]
    assert by_key["l0"] != by_key["l9"]
    for cl in clusters:
        assert cl.conf is not None and cl.conf > 0.8
        assert set(cl.member_cos) == set(cl.members)


def test_cluster_voices_short_line_of_new_voice_gets_own_group() -> None:
    emb, _ = _speakers([4, 1], spread=0.4, seed=5)
    keys = [f"l{i}" for i in range(len(emb))]
    durations = {k: 4.0 for k in keys}
    durations["l4"] = 0.8  # the only line of another actor, too short to build clusters
    clusters = cluster_voices(dict(zip(keys, emb, strict=True)), durations, threshold=0.35)
    assert sorted(len(cl.members) for cl in clusters) == [1, 4]
    assert any(cl.members == ["l4"] for cl in clusters)


def test_cluster_voices_all_short() -> None:
    emb, truth = _speakers([3, 3], spread=0.4, seed=6)
    keys = [f"l{i}" for i in range(len(emb))]
    clusters = cluster_voices(dict(zip(keys, emb, strict=True)), {k: 1.0 for k in keys}, threshold=0.35)
    assert sorted(sorted(cl.members) for cl in clusters) == [["l0", "l1", "l2"], ["l3", "l4", "l5"]]


def test_cluster_voices_singleton_has_no_conf() -> None:
    clusters = cluster_voices({"a": np.ones(8, np.float32)}, {"a": 3.0})
    assert len(clusters) == 1 and clusters[0].conf is None


def test_embedding_store_roundtrip_and_invalidation(tmp_path: Path) -> None:
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF0000")
    store = EmbeddingStore(tmp_path / "emb.npz")
    vec = np.arange(4, dtype=np.float32)
    store.put("line-a", wav_signature(wav), vec)
    store.save()
    again = EmbeddingStore(tmp_path / "emb.npz")
    assert len(again) == 1
    got = again.get("line-a", wav_signature(wav))
    assert got is not None and np.allclose(got, vec)
    wav.write_bytes(b"RIFF00000000")  # re-extracted file: new size -> cache miss
    assert again.get("line-a", wav_signature(wav)) is None
