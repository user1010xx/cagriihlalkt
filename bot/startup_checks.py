from __future__ import annotations

from pathlib import Path


def validate_runtime_setup(database_path: str) -> None:
    parent = Path(database_path).parent
    if str(parent) and str(parent) != ".":
        parent.mkdir(parents=True, exist_ok=True)
