"""Test del clustering per embedding."""

from __future__ import annotations

from bookwriter.organize.clustering import cluster_ids

# Due gruppi netti: A vicino a [1,0], B vicino a [0,1].
IDS = [1, 2, 3, 4]
VECS = [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0], [0.1, 0.9]]


def _as_sets(groups):
    return {frozenset(g) for g in groups}


def test_fixed_chapters() -> None:
    groups = cluster_ids(IDS, VECS, chapters=2)
    assert _as_sets(groups) == {frozenset({1, 2}), frozenset({3, 4})}


def test_auto_k_finds_two() -> None:
    groups = cluster_ids(IDS, VECS)  # k automatico via silhouette
    assert _as_sets(groups) == {frozenset({1, 2}), frozenset({3, 4})}


def test_small_input_single_group() -> None:
    assert cluster_ids([1, 2], [[1.0, 0.0], [0.0, 1.0]]) == [[1, 2]]
    assert cluster_ids([], []) == []


def test_all_ids_preserved() -> None:
    groups = cluster_ids(IDS, VECS, chapters=3)
    flat = sorted(i for g in groups for i in g)
    assert flat == IDS


def test_large_scalable_path() -> None:
    import numpy as np

    rng = np.random.default_rng(0)
    centers = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]])
    pts = np.vstack([c + 0.01 * rng.standard_normal((700, 3)) for c in centers])  # 2100 > soglia grande
    ids = list(range(len(pts)))
    groups = cluster_ids(ids, pts.tolist())
    assert sorted(i for g in groups for i in g) == ids
    assert len(groups) >= 2

