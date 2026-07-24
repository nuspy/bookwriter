"""Clustering tematico dei nodi basato sugli embedding.

KMeans su vettori **L2-normalizzati** (così la distanza euclidea approssima quella coseno, coerente
con gli embedding nomic). Il numero di capitoli può essere imposto (``chapters``) oppure scelto
**automaticamente** massimizzando il silhouette score in un intervallo ragionevole.
"""

from __future__ import annotations

import numpy as np
from sklearn.cluster import KMeans, MiniBatchKMeans
from sklearn.metrics import silhouette_score

# Oltre questa soglia si usa MiniBatchKMeans e il silhouette su un campione (scala a migliaia).
_LARGE_N = 2000
_SILHOUETTE_SAMPLE = 1000


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _fit_predict(matrix: np.ndarray, k: int, random_state: int):
    if matrix.shape[0] > _LARGE_N:
        return MiniBatchKMeans(n_clusters=k, n_init=3, random_state=random_state, batch_size=1024).fit_predict(matrix)
    return KMeans(n_clusters=k, n_init=10, random_state=random_state).fit_predict(matrix)


def _silhouette(matrix: np.ndarray, labels: np.ndarray, random_state: int) -> float:
    n = matrix.shape[0]
    if n <= _SILHOUETTE_SAMPLE:
        return silhouette_score(matrix, labels, metric="euclidean")
    # Campione per non far esplodere il costo O(n^2) del silhouette su insiemi grandi.
    rng = np.random.default_rng(random_state)
    idx = rng.choice(n, size=_SILHOUETTE_SAMPLE, replace=False)
    return silhouette_score(matrix[idx], labels[idx], metric="euclidean")


def cluster_ids(
    ids: list[int],
    vectors: list[list[float]],
    *,
    chapters: int | None = None,
    max_k: int = 10,
    random_state: int = 42,
) -> list[list[int]]:
    """Raggruppa gli ``ids`` in cluster secondo i loro ``vectors``. Ritorna liste di id.

    - ``chapters`` fissato → esattamente k = min(chapters, n) cluster;
    - altrimenti k è scelto via silhouette in [2 .. min(max_k, n-1)].
    Deterministico (``random_state`` fisso).
    """
    n = len(ids)
    if n == 0:
        return []
    if n <= 2:
        return [list(ids)]

    matrix = _l2_normalize(np.asarray(vectors, dtype=float))

    if chapters is not None:
        k = max(1, min(chapters, n))
        labels = _fit_predict(matrix, k, random_state)
    else:
        best_k, best_score, best_labels = 1, -1.0, np.zeros(n, dtype=int)
        upper = min(max_k, n - 1)
        for k in range(2, upper + 1):
            labels = _fit_predict(matrix, k, random_state)
            if len(set(labels)) < 2:
                continue
            score = _silhouette(matrix, labels, random_state)
            if score > best_score:
                best_k, best_score, best_labels = k, score, labels
        labels = best_labels if best_k > 1 else np.zeros(n, dtype=int)

    groups: dict[int, list[int]] = {}
    for node_id, label in zip(ids, labels):
        groups.setdefault(int(label), []).append(node_id)
    return list(groups.values())
