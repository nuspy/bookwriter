"""Store vettoriale su LanceDB (``<progetto>/vectors.lance``).

Una tabella per tipo di elemento: ``chunks`` (già disponibili) e in futuro ``nodes``. Ogni riga:
``id`` (chiave, corrisponde alla riga in project.db), ``text`` (per anteprima), ``vector`` (embedding).
Ricerca per similarità coseno (adatta agli embedding nomic normalizzati).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb


class LanceStore:
    def __init__(self, path: str | Path) -> None:
        self._db = lancedb.connect(str(path))

    @staticmethod
    def _table_name(kind: str) -> str:
        return f"{kind}s"  # chunk -> chunks, node -> nodes

    def _has(self, kind: str) -> bool:
        # list_tables() (>=0.34) ritorna un ListTablesResponse con .tables; versioni
        # più vecchie ritornano già una lista di nomi. Gestiamo entrambi.
        tables = self._db.list_tables()
        names = getattr(tables, "tables", tables)
        return self._table_name(kind) in names

    def count(self, kind: str) -> int:
        if not self._has(kind):
            return 0
        return self._db.open_table(self._table_name(kind)).count_rows()

    def existing_ids(self, kind: str) -> set[int]:
        """Id già presenti nello store (per embedding idempotente/incrementale)."""
        if not self._has(kind):
            return set()
        table = self._db.open_table(self._table_name(kind))
        return set(table.to_arrow().column("id").to_pylist())

    def add(self, kind: str, records: list[dict[str, Any]]) -> None:
        """Aggiunge righe ``{id, text, vector}``; crea la tabella se non esiste."""
        if not records:
            return
        name = self._table_name(kind)
        if self._has(kind):
            self._db.open_table(name).add(records)
        else:
            self._db.create_table(name, data=records)

    def all_vectors(self, kind: str) -> list[tuple[int, list[float]]]:
        """Tutte le coppie ``(id, vector)`` della tabella (per costruire il grafo top-k)."""
        if not self._has(kind):
            return []
        table = self._db.open_table(self._table_name(kind))
        arrow = table.to_arrow()
        ids = arrow.column("id").to_pylist()
        vectors = arrow.column("vector").to_pylist()
        return list(zip(ids, vectors))

    def search(self, kind: str, vector: list[float], k: int = 8) -> list[dict[str, Any]]:
        """Top-k per similarità coseno. Ritorna ``{id, text, distance}`` ordinati per vicinanza."""
        if not self._has(kind):
            return []
        table = self._db.open_table(self._table_name(kind))
        results = table.search(vector).metric("cosine").limit(k).to_list()
        return [
            {"id": r["id"], "text": r.get("text", ""), "distance": r.get("_distance")}
            for r in results
        ]
