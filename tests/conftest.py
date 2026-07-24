"""Fixture condivise: workspace isolato per i test.

``workspace_root()`` legge ``BOOKWRITER_HOME`` a ogni chiamata, quindi basta impostare l'env var:
niente reload di moduli.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def temp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "ws"
    monkeypatch.setenv("BOOKWRITER_HOME", str(ws))
    return ws
