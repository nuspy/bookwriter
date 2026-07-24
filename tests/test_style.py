"""Test del motore di stile (client LLM fittizio)."""

from __future__ import annotations

from pathlib import Path

from bookwriter.db.project_schema import get_style_row, init_project_db, save_style
from bookwriter.style.engine import (
    abstract_style,
    apply_style,
    authors_style,
    build_style_contract,
    extract_style,
)
from bookwriter.style.models import STYLE_DIMENSIONS, AuthorWeight, StyleProfile


class FakeStyleClient:
    def chat_json(self, messages, *, schema, model=None):
        props = schema.get("properties", {})
        if "dimensions" in props:
            return {
                "dimensions": {d: f"desc-{d}" for d in STYLE_DIMENSIONS},
                "exemplars": ["Un esempio."],
                "constraints": ["niente gergo tecnico"],
            }
        if "conforms" in props:
            return {"conforms": True, "issues": []}
        return {}

    def chat(self, messages, *, model=None, max_tokens=None):
        return "TESTO RISCRITTO"


def test_extract_and_abstract() -> None:
    client = FakeStyleClient()
    ex = extract_style("un testo campione", client, name="Mio stile")
    assert ex.origin == "extracted"
    assert set(ex.dimensions) == set(STYLE_DIMENSIONS)
    assert ex.constraints == ["niente gergo tecnico"]

    ab = abstract_style("saggistico ma caldo, frasi brevi", client, name="Caldo")
    assert ab.origin == "abstract"
    assert ab.dimensions["sintassi"] == "desc-sintassi"


def test_authors_blend() -> None:
    client = FakeStyleClient()
    specs = [
        AuthorWeight(author="Calvino", weight=0.7, dims=["sintassi", "ritmo"]),
        AuthorWeight(author="Hemingway", weight=0.3, dims=["lessico"]),
    ]
    prof = authors_style(specs, client, name="Blend")
    assert prof.origin == "authors"
    # La dimensione 'sintassi' proviene solo da Calvino (ambito assegnato).
    assert "Calvino" in prof.dimensions["sintassi"] and "%" in prof.dimensions["sintassi"]
    assert "Hemingway" in prof.dimensions["lessico"]


def test_contract_and_apply() -> None:
    client = FakeStyleClient()
    prof = extract_style("x", client, name="S")
    contract = build_style_contract(prof)
    assert "sintassi" in contract and "profilo: S" in contract
    out = apply_style("testo originale", prof, client)
    assert out == "TESTO RISCRITTO"


def test_persistence_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    init_project_db(db)
    prof = extract_style("x", FakeStyleClient(), name="Persistito")
    sid = save_style(db, prof.name, prof.origin, prof.model_dump())
    assert sid >= 1
    row = get_style_row(db, "Persistito")
    assert row is not None
    restored = StyleProfile.model_validate(row["data"])
    assert restored.name == "Persistito"
    assert set(restored.dimensions) == set(STYLE_DIMENSIONS)
