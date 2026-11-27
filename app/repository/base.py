"""Repository interface and domain exceptions for insert_app.py (手順5)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class RepositoryError(Exception):
    """Base error for repository operations."""


class DuplicateError(RepositoryError):
    """Raised when a unique constraint (message, BO3 match, Discord ID) is violated."""


class NotFoundError(RepositoryError):
    """Raised when a referenced entity does not exist."""


class ConflictError(RepositoryError):
    """Raised when concurrent updates conflict after retries."""


class BattleRepository(ABC):
    """Use-case oriented repository shared by SQLite and DynamoDB backends."""

    backend_name: str
    backend_label: str

    @abstractmethod
    def list_seasons(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_pokemon(self) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_players(self, order: str = "id", include_accounts: bool = False) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def list_messages(
        self,
        limit: int | None = None,
        challenge_only: bool = False,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_message(self, message_id: int) -> dict | None:
        raise NotImplementedError

    @abstractmethod
    def get_latest_message(self) -> dict | None:
        raise NotImplementedError

    @abstractmethod
    def get_battle(self, battle_id: int) -> dict | None:
        raise NotImplementedError

    @abstractmethod
    def get_match(self, match_id: int) -> dict | None:
        raise NotImplementedError

    @abstractmethod
    def list_recent_battles(
        self,
        limit: int = 50,
        include_ids: list[int] | None = None,
    ) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def create_message(
        self,
        discord_message_id: str,
        season_id: int,
        battle_date: str | None,
        raw_content: str,
        created_at: str | None = None,
    ) -> int:
        raise NotImplementedError

    @abstractmethod
    def create_battles(
        self,
        message_id: int,
        player1_id: int | None,
        player2_id: int | None,
        rows: list[dict],
        request_id: str | None = None,
    ) -> list[int]:
        raise NotImplementedError

    @abstractmethod
    def create_battle_match(
        self,
        *,
        battle_message_id: int,
        season_id: int,
        tournament_name: str | None,
        opponent: str | None,
        match_date: str,
        player_id: int | None,
        games: list[dict],
    ) -> int:
        raise NotImplementedError

    @abstractmethod
    def create_player(
        self,
        discord_user_id: str,
        name: str,
        created_at: str | None = None,
    ) -> int:
        raise NotImplementedError

    @abstractmethod
    def link_discord_account(
        self,
        player_id: int,
        discord_user_id: str,
        label: str | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_battle(self, battle_id: int, reason: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_match(self, match_id: int, reason: str) -> None:
        raise NotImplementedError
