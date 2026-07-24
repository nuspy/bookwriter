"""Test dei checkpoint Job (progresso persistito)."""

from __future__ import annotations

from pathlib import Path

from bookwriter.db.project_schema import (
    finish_job,
    init_project_db,
    list_jobs,
    start_job,
    update_job,
)


def test_job_lifecycle(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    init_project_db(db)
    jid = start_job(db, "atomize")
    assert jid >= 1
    update_job(db, jid, 0.5)
    row = list_jobs(db)[0]
    assert row["kind"] == "atomize" and row["state"] == "running" and row["progress"] == 0.5
    finish_job(db, jid)
    row = list_jobs(db)[0]
    assert row["state"] == "done" and row["progress"] == 1.0
