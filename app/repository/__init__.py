"""Repository factory controlled by PLAKORO_REPOSITORY ('sqlite' | 'dynamodb')."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import (
    BattleRepository,
    ConflictError,
    DuplicateError,
    NotFoundError,
    RepositoryError,
)
from .dynamodb_repo import DynamoDBBattleRepository
from .sqlite_repo import SQLiteBattleRepository

__all__ = [
    "BattleRepository",
    "ConflictError",
    "DuplicateError",
    "DynamoDBBattleRepository",
    "NotFoundError",
    "RepositoryError",
    "SQLiteBattleRepository",
    "get_repository",
]


def get_repository(
    *,
    db_path: Path | str | None = None,
    backend: str | None = None,
    dynamodb_client: Any = None,
    table_prefix: str | None = None,
) -> BattleRepository:
    selected = (backend or os.environ.get("PLAKORO_REPOSITORY", "sqlite")).strip().lower()
    if selected == "sqlite":
        resolved_db = (
            Path(db_path)
            if db_path is not None
            else Path(__file__).resolve().parent.parent.parent / "data" / "pokemon.db"
        )
        return SQLiteBattleRepository(resolved_db)
    if selected == "dynamodb":
        return DynamoDBBattleRepository(
            table_prefix=table_prefix,
            dynamodb_client=dynamodb_client,
        )
    raise ValueError(f"Unsupported PLAKORO_REPOSITORY value: {selected!r}")
