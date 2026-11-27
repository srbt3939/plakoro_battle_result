"""SQLite repository implementation preserving full compatibility with pokemon.db."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from ..battle_matches import create_battle_match as sqlite_create_battle_match
from ..battle_matches import is_battle_challenge_message
from .base import BattleRepository, DuplicateError, NotFoundError

# In-process idempotency cache for SQLite mode so pokemon.db schema stays untouched.
_RECENT_REQUESTS: OrderedDict[tuple[str, int, str], list[int]] = OrderedDict()
_MAX_RECENT_REQUESTS = 256


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteBattleRepository(BattleRepository):
    backend_name = "sqlite"

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.backend_label = f"SQLite ({self.db_path.name})"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self._table_exists(conn, "battles"):
            columns = self._table_columns(conn, "battles")
            if "battle_type" not in columns:
                conn.execute(
                    "ALTER TABLE battles ADD COLUMN battle_type TEXT NOT NULL DEFAULT 'normal'"
                )
                conn.commit()
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        return {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }

    def list_seasons(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM seasons ORDER BY id").fetchall()
            return [dict(row) for row in rows]

    def list_pokemon(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM pokemon ORDER BY id").fetchall()
            return [dict(row) for row in rows]

    def list_players(self, order: str = "id", include_accounts: bool = False) -> list[dict]:
        order_clause = "name COLLATE NOCASE, id" if order == "name" else "id"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM players ORDER BY {order_clause}"
            ).fetchall()
            players = []
            for row in rows:
                item = dict(row)
                if include_accounts:
                    discord_id = item.get("discord_user_id")
                    item["discord_accounts"] = (
                        [{"discord_user_id": discord_id, "label": None}]
                        if discord_id
                        else []
                    )
                players.append(item)
            return players

    def list_messages(
        self,
        limit: int | None = None,
        challenge_only: bool = False,
    ) -> list[dict]:
        with self._connect() as conn:
            has_matches = self._table_exists(conn, "battle_matches")
            match_by_msg: dict[int, int] = {}
            if has_matches:
                match_cols = self._table_columns(conn, "battle_matches")
                where_not_deleted = (
                    "WHERE COALESCE(is_deleted, 0) = 0"
                    if "is_deleted" in match_cols
                    else ""
                )
                for mrow in conn.execute(
                    f"SELECT match_id, battle_message_id FROM battle_matches {where_not_deleted}"
                ).fetchall():
                    if mrow["battle_message_id"] is not None:
                        match_by_msg[mrow["battle_message_id"]] = mrow["match_id"]

            rows = conn.execute(
                "SELECT * FROM battle_messages ORDER BY id DESC"
            ).fetchall()
            result: list[dict] = []
            for row in rows:
                item = dict(row)
                is_challenge = is_battle_challenge_message(item.get("raw_content"))
                if challenge_only and not is_challenge:
                    continue
                item["is_battle_challenge"] = is_challenge
                item["match_id"] = match_by_msg.get(item["id"])
                result.append(item)
                if limit is not None and len(result) >= limit:
                    break
            return result

    def get_message(self, message_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM battle_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["is_battle_challenge"] = is_battle_challenge_message(
                item.get("raw_content")
            )
            item["match_id"] = None
            if self._table_exists(conn, "battle_matches"):
                match_cols = self._table_columns(conn, "battle_matches")
                extra = (
                    "AND COALESCE(is_deleted, 0) = 0"
                    if "is_deleted" in match_cols
                    else ""
                )
                match_row = conn.execute(
                    f"SELECT match_id FROM battle_matches WHERE battle_message_id = ? {extra}",
                    (message_id,),
                ).fetchone()
                if match_row is not None:
                    item["match_id"] = match_row["match_id"]
            return item

    def get_latest_message(self) -> dict | None:
        with self._connect() as conn:
            if not self._table_exists(conn, "battle_messages"):
                return None
            row = conn.execute(
                "SELECT * FROM battle_messages ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row is not None else None

    def get_battle(self, battle_id: int) -> dict | None:
        with self._connect() as conn:
            battle_cols = self._table_columns(conn, "battles")
            deleted_cond = (
                "AND COALESCE(b.is_deleted, 0) = 0"
                if "is_deleted" in battle_cols
                else ""
            )
            match_select = (
                "b.match_id, b.game_number"
                if {"match_id", "game_number"}.issubset(battle_cols)
                else "NULL AS match_id, NULL AS game_number"
            )
            row = conn.execute(
                f"""
                SELECT
                    b.id,
                    b.battle_message_id,
                    b.battle_number,
                    b.player1_id,
                    b.player2_id,
                    b.player1_pokemon_id,
                    b.player2_pokemon_id,
                    p1.name AS player1_pokemon,
                    p2.name AS player2_pokemon,
                    pl1.name AS player1_name,
                    pl2.name AS player2_name,
                    b.first_player,
                    b.player1_result,
                    b.player2_result,
                    b.battle_type,
                    {match_select}
                FROM battles AS b
                JOIN pokemon AS p1 ON b.player1_pokemon_id = p1.id
                JOIN pokemon AS p2 ON b.player2_pokemon_id = p2.id
                LEFT JOIN players AS pl1 ON b.player1_id = pl1.id
                LEFT JOIN players AS pl2 ON b.player2_id = pl2.id
                WHERE b.id = ? {deleted_cond}
                """,
                (battle_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def get_match(self, match_id: int) -> dict | None:
        with self._connect() as conn:
            if not self._table_exists(conn, "battle_matches"):
                return None
            match_cols = self._table_columns(conn, "battle_matches")
            deleted_cond = (
                "AND COALESCE(m.is_deleted, 0) = 0"
                if "is_deleted" in match_cols
                else ""
            )
            row = conn.execute(
                f"""
                SELECT m.*, pl.name AS player_name
                FROM battle_matches AS m
                LEFT JOIN players AS pl ON m.player_id = pl.id
                WHERE m.match_id = ? {deleted_cond}
                """,
                (match_id,),
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            battle_cols = self._table_columns(conn, "battles")
            b_deleted = (
                "AND COALESCE(b.is_deleted, 0) = 0"
                if "is_deleted" in battle_cols
                else ""
            )
            games = conn.execute(
                f"""
                SELECT b.id, b.game_number, b.player1_result, p1.name AS player1_pokemon, p2.name AS player2_pokemon
                FROM battles AS b
                JOIN pokemon AS p1 ON b.player1_pokemon_id = p1.id
                JOIN pokemon AS p2 ON b.player2_pokemon_id = p2.id
                WHERE b.match_id = ? {b_deleted}
                ORDER BY b.game_number
                """,
                (match_id,),
            ).fetchall()
            item["games"] = [dict(g) for g in games]
            return item

    def list_recent_battles(
        self,
        limit: int = 50,
        include_ids: list[int] | None = None,
    ) -> list[dict]:
        with self._connect() as conn:
            battle_cols = self._table_columns(conn, "battles")
            deleted_where = (
                "WHERE COALESCE(is_deleted, 0) = 0"
                if "is_deleted" in battle_cols
                else ""
            )
            match_select = (
                "battles.match_id"
                if "match_id" in battle_cols
                else "NULL AS match_id"
            )
            extra_ids = [int(x) for x in (include_ids or [])]
            if extra_ids:
                placeholders = ", ".join("?" for _ in extra_ids)
                not_del = (
                    "AND COALESCE(battles.is_deleted, 0) = 0"
                    if "is_deleted" in battle_cols
                    else ""
                )
                where_sql = f"""
                    WHERE (
                        battles.id IN (
                            SELECT id FROM battles {deleted_where} ORDER BY id DESC LIMIT ?
                        )
                        OR battles.id IN ({placeholders})
                    )
                    {not_del}
                """
                params: tuple = (limit, *extra_ids)
            else:
                where_sql = f"""
                    WHERE battles.id IN (
                        SELECT id FROM battles {deleted_where} ORDER BY id DESC LIMIT ?
                    )
                """
                params = (limit,)

            rows = conn.execute(
                f"""
                SELECT
                    battles.id,
                    battles.battle_message_id,
                    battles.battle_number,
                    p1.name AS player1_pokemon,
                    p2.name AS player2_pokemon,
                    player1.name AS player1_name,
                    player2.name AS player2_name,
                    battles.first_player,
                    battles.player1_result,
                    battles.player2_result,
                    battles.battle_type,
                    {match_select}
                FROM battles
                JOIN pokemon AS p1 ON battles.player1_pokemon_id = p1.id
                JOIN pokemon AS p2 ON battles.player2_pokemon_id = p2.id
                LEFT JOIN players AS player1 ON battles.player1_id = player1.id
                LEFT JOIN players AS player2 ON battles.player2_id = player2.id
                {where_sql}
                ORDER BY battles.id
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def create_message(
        self,
        discord_message_id: str,
        season_id: int,
        battle_date: str | None,
        raw_content: str,
        created_at: str | None = None,
    ) -> int:
        ts = created_at or _now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM battle_messages WHERE discord_message_id = ?",
                (discord_message_id,),
            ).fetchone()
            if existing is not None:
                raise DuplicateError("このDiscordメッセージIDはすでに登録されています。")
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO battle_messages (
                        discord_message_id, season_id, battle_date, raw_content, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (discord_message_id, int(season_id), battle_date or "", raw_content, ts),
                )
                conn.commit()
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError as exc:
                raise DuplicateError("このDiscordメッセージIDはすでに登録されています。") from exc

    def create_battles(
        self,
        message_id: int,
        player1_id: int | None,
        player2_id: int | None,
        rows: list[dict],
        request_id: str | None = None,
    ) -> list[int]:
        if len(rows) == 0:
            raise ValueError("少なくとも1試合を入力してください。")
        if len(rows) > 90:
            raise ValueError("1回に登録できる通常対戦は最大90件です。")

        cache_key = (
            (str(self.db_path.resolve()), int(message_id), request_id)
            if request_id
            else None
        )
        if cache_key is not None and cache_key in _RECENT_REQUESTS:
            return list(_RECENT_REQUESTS[cache_key])

        with self._connect() as conn:
            msg = conn.execute(
                "SELECT id FROM battle_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if msg is None:
                raise NotFoundError("指定されたメッセージが存在しません。")

            battle_ids: list[int] = []
            with conn:
                for row in rows:
                    p1_result = row["player1_result"]
                    p2_result = {"win": "lose", "lose": "win"}[p1_result]
                    first_player = row.get("first_player")
                    if first_player == "":
                        first_player = None
                    elif first_player is not None:
                        first_player = int(first_player)
                    cursor = conn.execute(
                        """
                        INSERT INTO battles (
                            battle_message_id,
                            battle_number,
                            player1_pokemon_id,
                            player2_pokemon_id,
                            player1_id,
                            player2_id,
                            first_player,
                            player1_result,
                            player2_result,
                            battle_type
                        )
                        SELECT
                            ?,
                            COALESCE(MAX(battle_number), 0) + 1,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?,
                            ?
                        FROM battles
                        WHERE battle_message_id = ?
                        """,
                        (
                            message_id,
                            int(row["player1_pokemon_id"]),
                            int(row["player2_pokemon_id"]),
                            int(player1_id) if player1_id is not None else None,
                            int(player2_id) if player2_id is not None else None,
                            first_player,
                            p1_result,
                            p2_result,
                            row.get("battle_type") or "normal",
                            message_id,
                        ),
                    )
                    battle_ids.append(int(cursor.lastrowid))

        if cache_key is not None:
            _RECENT_REQUESTS[cache_key] = list(battle_ids)
            while len(_RECENT_REQUESTS) > _MAX_RECENT_REQUESTS:
                _RECENT_REQUESTS.popitem(last=False)

        return battle_ids

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
        with self._connect() as conn:
            required_columns = self._table_columns(conn, "battles")
            if not {"match_id", "game_number"}.issubset(required_columns):
                raise RuntimeError(
                    "DB移行が必要です。scripts/migrate_battle_challenges.py を実行してください。"
                )
            msg = conn.execute(
                "SELECT id FROM battle_messages WHERE id = ?",
                (battle_message_id,),
            ).fetchone()
            if msg is None:
                raise NotFoundError("指定されたメッセージが存在しません。")

            match_cols = self._table_columns(conn, "battle_matches")
            extra = (
                "AND COALESCE(is_deleted, 0) = 0"
                if "is_deleted" in match_cols
                else ""
            )
            existing = conn.execute(
                f"SELECT match_id FROM battle_matches WHERE battle_message_id = ? {extra}",
                (battle_message_id,),
            ).fetchone()
            if existing is not None:
                raise DuplicateError("このメッセージの大会対戦は登録済みです。")

            try:
                with conn:
                    return int(
                        sqlite_create_battle_match(
                            conn,
                            battle_message_id=battle_message_id,
                            season_id=season_id,
                            tournament_name=tournament_name,
                            opponent=opponent,
                            match_date=match_date,
                            player_id=player_id,
                            games=games,
                        )
                    )
            except sqlite3.IntegrityError as exc:
                raise DuplicateError("このメッセージの大会対戦は登録済みです。") from exc

    def create_player(
        self,
        discord_user_id: str,
        name: str,
        created_at: str | None = None,
    ) -> int:
        ts = created_at or _now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM players WHERE discord_user_id = ?",
                (discord_user_id,),
            ).fetchone()
            if existing is not None:
                raise DuplicateError("このDiscord User IDはすでに登録されています。")
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO players (discord_user_id, name, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (discord_user_id, name, ts),
                )
                conn.commit()
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError as exc:
                raise DuplicateError("このDiscord User IDはすでに登録されています。") from exc

    def link_discord_account(
        self,
        player_id: int,
        discord_user_id: str,
        label: str | None = None,
    ) -> None:
        raise NotImplementedError(
            "既存プレイヤーへの複数Discordアカウント追加紐づけはDynamoDB接続時のみ利用できます。"
        )

    def _ensure_soft_delete_schema(self, conn: sqlite3.Connection) -> None:
        battle_cols = self._table_columns(conn, "battles")
        if "is_deleted" not in battle_cols:
            conn.execute(
                "ALTER TABLE battles ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0"
            )
        if self._table_exists(conn, "battle_matches"):
            match_cols = self._table_columns(conn, "battle_matches")
            if "is_deleted" not in match_cols:
                conn.execute(
                    "ALTER TABLE battle_matches ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0"
                )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS battle_audit_logs (
                target_key TEXT NOT NULL,
                logged_at TEXT NOT NULL,
                audit_id TEXT NOT NULL,
                action TEXT NOT NULL,
                performed_by TEXT,
                reason TEXT NOT NULL,
                before_json TEXT NOT NULL,
                PRIMARY KEY (target_key, logged_at)
            )
            """
        )

    def delete_battle(self, battle_id: int, reason: str) -> None:
        clean_reason = (reason or "").strip()
        if not clean_reason:
            raise ValueError("削除理由を入力してください。")
        with self._connect() as conn:
            self._ensure_soft_delete_schema(conn)
            row = conn.execute(
                "SELECT * FROM battles WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
                (battle_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("指定された対戦が見つからないか、すでに削除されています。")
            before = dict(row)
            if before.get("match_id") is not None:
                raise ValueError(
                    "大会対戦（BO3）の個別ゲームは単独で削除できません。シリーズ単位で削除してください。"
                )
            now = _now_iso()
            audit_id = f"{battle_id:04x}"[-6:]
            with conn:
                conn.execute(
                    "UPDATE battles SET is_deleted = 1 WHERE id = ?",
                    (battle_id,),
                )
                conn.execute(
                    """
                    INSERT INTO battle_audit_logs (
                        target_key, logged_at, audit_id, action, performed_by, reason, before_json
                    ) VALUES (?, ?, ?, 'delete', 'local-sqlite', ?, ?)
                    """,
                    (
                        f"battle#{battle_id}",
                        f"{now}#{audit_id}",
                        audit_id,
                        clean_reason,
                        json.dumps(before, ensure_ascii=False),
                    ),
                )

    def delete_match(self, match_id: int, reason: str) -> None:
        clean_reason = (reason or "").strip()
        if not clean_reason:
            raise ValueError("削除理由を入力してください。")
        with self._connect() as conn:
            self._ensure_soft_delete_schema(conn)
            match_row = conn.execute(
                "SELECT * FROM battle_matches WHERE match_id = ? AND COALESCE(is_deleted, 0) = 0",
                (match_id,),
            ).fetchone()
            if match_row is None:
                raise NotFoundError("指定された大会対戦が見つからないか、すでに削除されています。")
            before_match = dict(match_row)
            game_rows = conn.execute(
                "SELECT * FROM battles WHERE match_id = ? AND COALESCE(is_deleted, 0) = 0 ORDER BY game_number",
                (match_id,),
            ).fetchall()
            now = _now_iso()
            audit_id = f"{match_id:04x}"[-6:]
            with conn:
                conn.execute(
                    "UPDATE battle_matches SET is_deleted = 1, battle_message_id = NULL WHERE match_id = ?",
                    (match_id,),
                )
                conn.execute(
                    "UPDATE battles SET is_deleted = 1 WHERE match_id = ?",
                    (match_id,),
                )
                conn.execute(
                    """
                    INSERT INTO battle_audit_logs (
                        target_key, logged_at, audit_id, action, performed_by, reason, before_json
                    ) VALUES (?, ?, ?, 'delete', 'local-sqlite', ?, ?)
                    """,
                    (
                        f"match#{match_id}",
                        f"{now}#{audit_id}",
                        audit_id,
                        clean_reason,
                        json.dumps(before_match, ensure_ascii=False),
                    ),
                )
                for game_row in game_rows:
                    gdict = dict(game_row)
                    conn.execute(
                        """
                        INSERT INTO battle_audit_logs (
                            target_key, logged_at, audit_id, action, performed_by, reason, before_json
                        ) VALUES (?, ?, ?, 'delete', 'local-sqlite', ?, ?)
                        """,
                        (
                            f"battle#{gdict['id']}",
                            f"{now}#{audit_id}",
                            audit_id,
                            clean_reason,
                            json.dumps(gdict, ensure_ascii=False),
                        ),
                    )
