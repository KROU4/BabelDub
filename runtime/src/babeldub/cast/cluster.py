"""Speaker embeddings cache and voice clustering (average-linkage agglomerative, cosine).

Clustering uses the nearest-neighbour-chain algorithm (O(n^2) time and memory) with average
linkage on cosine similarity, then cuts the dendrogram at a similarity threshold. Average
linkage is reducible and monotone, so the cut is exactly "merge while similarity >= threshold".
Short lines are not used to build clusters; they join the nearest centroid afterwards when it
is similar enough, otherwise they are clustered among themselves.

Calibration on Low-Budget Repairs (ECAPA, studio game voices): different lines of one actor
score 0.44-0.65, different actors -0.16..0.28, hence the 0.35 default threshold.

A cluster whose centroid is close to the voice centroid of a character named in the metadata
is that character (his lines that lack a speaker hint, e.g. cutscenes): on Low-Budget Repairs
zbyszek~cluster_04 scored 0.88 and maciek~cluster_05 0.89, every other pair 0.06-0.38, hence
the 0.75 default of `cast.merge_threshold`.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from babeldub.qa.qc import QcItemError, QcSession

EMBEDDINGS = "cast/embeddings.npz"  # speaker embeddings of original lines, keyed by line id
DEFAULT_THRESHOLD = 0.35  # average cosine between two groups of lines to treat them as one voice
UNCERTAIN_CONF = 0.6  # clusters whose members sit further than this from the centroid need review
MIN_CLUSTER_S = 1.5  # shorter lines give unstable embeddings: attached to centroids afterwards
MAX_CLUSTER_POINTS = 3000  # memory bound of the similarity matrix (float32: ~36 MB)
MERGE_THRESHOLD = 0.75  # centroid cosine of a cluster to a named character that makes it that character


def l2_normalize(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat[None, :]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def wav_signature(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


class EmbeddingStore:
    """Persistent cache {key -> embedding} invalidated by the wav file signature (size, mtime)."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, tuple[str, np.ndarray]] = {}
        self.dirty = False
        if path.exists():
            with np.load(path, allow_pickle=False) as npz:
                keys = npz["keys"].tolist()
                sigs = npz["sigs"].tolist()
                emb = npz["emb"]
            for i, key in enumerate(keys):
                self._data[str(key)] = (str(sigs[i]), emb[i].astype(np.float32))

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, sig: str) -> np.ndarray | None:
        hit = self._data.get(key)
        if hit is None or hit[0] != sig:
            return None
        return hit[1]

    def put(self, key: str, sig: str, vec: np.ndarray) -> None:
        self._data[key] = (sig, np.asarray(vec, dtype=np.float32).reshape(-1))
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        keys = list(self._data)
        dims = {v[1].shape[0] for v in self._data.values()}
        if len(dims) > 1:  # model changed between runs: keep the newest dimension only
            newest = next(reversed(self._data.values()))[1].shape[0]
            keys = [k for k in keys if self._data[k][1].shape[0] == newest]
        emb = np.stack([self._data[k][1] for k in keys]) if keys else np.zeros((0, 0), np.float32)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".emb.", suffix=".npz", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                np.savez(fh, keys=np.array(keys, dtype=str), sigs=np.array([self._data[k][0] for k in keys], dtype=str), emb=emb)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        self.dirty = False


class Embedder:
    """Speaker embeddings of wav files with a persistent cache.

    The qc worker is started (through `opener`) only when something is not cached yet; without
    an opener, uncached items are simply missing from the result.
    """

    def __init__(self, store: EmbeddingStore, opener: Callable[[], QcSession] | None, owns_session: bool = True):
        self.store = store
        self.opener = opener
        self.owns_session = owns_session
        self.session: QcSession | None = None
        self.errors: list[str] = []  # per-file failures
        self.computed = 0

    @property
    def warnings(self) -> list[str]:
        return list(self.session.warnings) if self.session is not None else []

    def _ensure(self) -> bool:
        if self.session is None and self.opener is not None:
            self.session = self.opener()
        return self.session is not None

    def embed(self, items: Iterable[tuple[str, Path]]) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        uncached: list[tuple[str, Path, str]] = []
        for key, wav in items:
            if not wav.exists():
                continue
            sig = wav_signature(wav)
            vec = self.store.get(key, sig)
            if vec is None:
                uncached.append((key, wav, sig))
            else:
                out[key] = vec
        if uncached and self._ensure():
            assert self.session is not None
            for key, wav, sig in uncached:
                try:
                    vec = self.session.embed(key, wav)
                except QcItemError as exc:  # a broken file must not stop the rest
                    self.errors.append(f"{key}: {exc}")
                    continue
                self.store.put(key, sig, vec)
                self.computed += 1
                out[key] = vec
        return out

    def embed_one(self, key: str, wav: Path) -> np.ndarray | None:
        return self.embed([(key, wav)]).get(key)

    def close(self) -> None:
        self.store.save()
        if self.session is not None and self.owns_session:
            self.session.close()
        self.session = None


def agglomerative(emb: np.ndarray, threshold: float = DEFAULT_THRESHOLD) -> np.ndarray:
    """Flat cluster labels (0..k-1, ordered by first appearance) for average-linkage cosine."""
    n = int(emb.shape[0]) if emb.ndim == 2 else 0
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    if n == 1:
        return np.zeros(1, dtype=np.int64)
    x = l2_normalize(emb)
    sim = (x @ x.T).astype(np.float32)
    neg = np.float32(-np.inf)
    np.fill_diagonal(sim, neg)
    size = np.ones(n, dtype=np.float32)
    active = np.ones(n, dtype=bool)
    merges: list[tuple[int, int, float]] = []
    chain: list[int] = []
    remaining = n
    while remaining > 1:
        if not chain:
            chain.append(int(np.flatnonzero(active)[0]))
        a = chain[-1]
        row = sim[a]
        b = int(np.argmax(row))
        if len(chain) > 1 and row[chain[-2]] >= row[b]:
            b = chain[-2]
        if len(chain) > 1 and b == chain[-2]:
            chain.pop()
            chain.pop()
            merges.append((a, b, float(row[b])))
            merged = (size[a] * sim[a] + size[b] * sim[b]) / (size[a] + size[b])
            size[a] += size[b]
            sim[a, :] = merged
            sim[:, a] = merged
            sim[a, a] = neg
            sim[b, :] = neg
            sim[:, b] = neg
            active[b] = False
            remaining -= 1
        else:
            chain.append(b)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, s in merges:
        if s >= threshold:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
    roots: dict[int, int] = {}
    labels = np.empty(n, dtype=np.int64)
    for i in range(n):
        labels[i] = roots.setdefault(find(i), len(roots))
    return labels


@dataclass
class Cluster:
    members: list[str]  # keys in input order
    centroid: np.ndarray
    member_cos: dict[str, float] = field(default_factory=dict)

    @property
    def conf(self) -> float | None:
        """Median cosine of members to the centroid (None for a single line)."""
        if len(self.members) < 2:
            return None
        return float(np.median(list(self.member_cos.values())))


def cluster_voices(
    embeddings: dict[str, np.ndarray],
    durations: dict[str, float],
    threshold: float = DEFAULT_THRESHOLD,
    min_cluster_s: float = MIN_CLUSTER_S,
    max_points: int = MAX_CLUSTER_POINTS,
) -> list[Cluster]:
    """Cluster lines by voice.

    Long lines build the clusters. Every other line joins the nearest centroid if its cosine
    to it reaches the threshold; the rest are clustered among themselves (a short line of a
    voice that has no long lines still gets its own group instead of a wrong one).
    """
    keys = list(embeddings)
    if not keys:
        return []
    core = [k for k in keys if durations.get(k, 0.0) >= min_cluster_s]
    if len(core) > max_points:  # keep the longest lines: their embeddings are the most reliable
        core = sorted(core, key=lambda k: (-durations.get(k, 0.0), k))[:max_points]
    core_set = set(core)
    groups: list[list[str]] = []

    def add_groups(members: list[str]) -> None:
        labels = agglomerative(np.stack([embeddings[k] for k in members]), threshold)
        found: dict[int, list[str]] = {}
        for key, label in zip(members, labels.tolist(), strict=True):
            found.setdefault(label, []).append(key)
        groups.extend(found[lab] for lab in sorted(found))

    if core:
        add_groups([k for k in keys if k in core_set])
    rest = [k for k in keys if k not in core_set]
    leftovers: list[str] = []
    if groups and rest:
        cmat = np.stack([l2_normalize(np.stack([embeddings[k] for k in g]).mean(axis=0))[0] for g in groups])
        for key in rest:
            sims = cmat @ l2_normalize(embeddings[key])[0]
            best = int(np.argmax(sims))
            if sims[best] >= threshold or len(leftovers) >= max_points:
                groups[best].append(key)
            else:
                leftovers.append(key)
    else:
        leftovers = rest
    if leftovers:
        add_groups(leftovers)
    clusters: list[Cluster] = []
    for group in groups:
        member_set = set(group)
        members = [k for k in keys if k in member_set]
        centroid = l2_normalize(np.stack([embeddings[k] for k in members]).mean(axis=0))[0]
        member_cos = {k: float(np.dot(l2_normalize(embeddings[k])[0], centroid)) for k in members}
        clusters.append(Cluster(members=members, centroid=centroid, member_cos=member_cos))
    return clusters


def centroid_of(vectors: list[np.ndarray]) -> np.ndarray | None:
    if not vectors:
        return None
    return l2_normalize(np.stack(vectors).mean(axis=0))[0]


def merge_targets(
    clusters: dict[str, np.ndarray],
    chars: dict[str, np.ndarray],
    threshold: float = MERGE_THRESHOLD,
) -> dict[str, tuple[str, float]]:
    """{cluster_id: (char_id, cosine)} for clusters whose centroid reaches `threshold` with the
    closest character centroid. Clusters are never merged with each other here: the clustering
    threshold already decided that."""
    if not clusters or not chars:
        return {}
    ids = list(chars)
    mat = l2_normalize(np.stack([chars[c] for c in ids]))
    out: dict[str, tuple[str, float]] = {}
    for cluster_id, centroid in clusters.items():
        sims = mat @ l2_normalize(centroid)[0]
        best = int(np.argmax(sims))
        if float(sims[best]) >= threshold:
            out[cluster_id] = (ids[best], float(sims[best]))
    return out
