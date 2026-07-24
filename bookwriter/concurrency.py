"""Esecuzione concorrente di chiamate I/O-bound (LLM) con grado di parallelismo limitato."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def imap_unordered(fn: Callable[[T], R], items: list[T], concurrency: int) -> Iterator[tuple[T, R]]:
    """Applica ``fn`` agli ``items`` in parallelo; produce ``(item, risultato)`` appena pronti.

    Con ``concurrency <= 1`` (o pochi item) esegue in sequenza. Le scritture su DB restano al
    chiamante (nel thread principale), evitando contese: qui gira solo la parte I/O-bound.
    """
    if concurrency <= 1 or len(items) <= 1:
        for item in items:
            yield item, fn(item)
        return
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(fn, item): item for item in items}
        for future in as_completed(futures):
            yield futures[future], future.result()
