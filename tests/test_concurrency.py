"""Test dell'esecuzione concorrente."""

from __future__ import annotations

from bookwriter.concurrency import imap_unordered


def test_imap_all_items_parallel() -> None:
    items = [1, 2, 3, 4, 5]
    result = dict(imap_unordered(lambda x: x * x, items, 3))
    assert result == {1: 1, 2: 4, 3: 9, 4: 16, 5: 25}


def test_imap_serial_path() -> None:
    assert dict(imap_unordered(lambda x: x + 1, [10], 4)) == {10: 11}
    assert list(imap_unordered(lambda x: x, [], 4)) == []
