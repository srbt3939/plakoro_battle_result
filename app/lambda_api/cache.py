"""Lambda in-memory season & master cache with GSI count verification (手順7 §6 / A-1〜A-3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Callable

from ..aggregation.cells import CellKey, build_season_cells
from ..dynamodb_serde import deserialize_item, from_attr
from .errors import ApiError

logger = logging.getLogger("plakoro.api")

PROVISIONAL_TTL_SECONDS = 60.0
RETRY_BACKOFFS = (0.3, 0.6, 1.2)


def log_event(event_name: str, **fields: Any) -> None:
    payload = {"event": event_name, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False))


@dataclass
class SeasonSnapshot:
    season_id: int
    data_version: int
    master_version: int
    allowed_pokemon_ids: list[int]
    cells: dict[CellKey, int]
    battles: list[dict[str, Any]]
    matches: list[dict[str, Any]]


@dataclass
class _ProvisionalEntry:
    snapshot: SeasonSnapshot
    expires_at_mono: float


class SeasonDataCache:
    """Per-Lambda-container in-memory cache (1 confirmed + 1 provisional per season)."""

    def __init__(
        self,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        now_utc_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._now_utc = now_utc_fn
        self._master_version: int | None = None
        self._pokemon_by_id: dict[int, dict[str, Any]] = {}
        self._players_by_id: dict[int, dict[str, Any]] = {}
        self._seasons_list: list[dict[str, Any]] = []
        self._confirmed: dict[int, SeasonSnapshot] = {}
        self._provisional: dict[int, _ProvisionalEntry] = {}

    def clear(self) -> None:
        self._master_version = None
        self._pokemon_by_id.clear()
        self._players_by_id.clear()
        self._seasons_list.clear()
        self._confirmed.clear()
        self._provisional.clear()

    def _scan_all(self, dynamodb_client: Any, table_name: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"TableName": table_name}
        while True:
            try:
                resp = dynamodb_client.scan(**kwargs)
            except Exception as exc:
                raise ApiError(
                    503,
                    "TEMPORARILY_UNAVAILABLE",
                    "データの読み込みに失敗しました。しばらくしてからやり直してください。",
                    retry_after=3,
                ) from exc
            for raw in resp.get("Items", []):
                parsed = deserialize_item(raw)
                if parsed is not None:
                    items.append(parsed)
            if not resp.get("LastEvaluatedKey"):
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items

    def get_masters(
        self,
        dynamodb_client: Any,
        table_prefix: str,
    ) -> tuple[int, dict[int, dict[str, Any]], dict[int, dict[str, Any]], list[dict[str, Any]]]:
        resp = dynamodb_client.get_item(
            TableName=f"{table_prefix}Counters",
            Key={"counterName": {"S": "masterVersion"}},
            ConsistentRead=True,
        )
        raw_item = resp.get("Item")
        current_mv = int(from_attr(raw_item["value"])) if raw_item and "value" in raw_item else 1

        if self._master_version != current_mv or not self._pokemon_by_id:
            raw_pokemon = self._scan_all(dynamodb_client, f"{table_prefix}Pokemon")
            raw_players = self._scan_all(dynamodb_client, f"{table_prefix}Players")
            raw_seasons = self._scan_all(dynamodb_client, f"{table_prefix}Seasons")

            self._pokemon_by_id = {
                int(p["pokemonId"]): {
                    "id": int(p["pokemonId"]),
                    "name": p["name"],
                    "type1": p.get("type1"),
                    "type2": p.get("type2"),
                    "weaknesses": p.get("weaknesses", []),
                    "emoji_id": p.get("emojiId"),
                }
                for p in raw_pokemon
            }
            self._players_by_id = {
                int(pl["playerId"]): {
                    "id": int(pl["playerId"]),
                    "name": pl["name"],
                    "is_public": bool(pl.get("isPublic", True)),
                    "reset_at": pl.get("resetAt"),
                    "reset_after_battle_id": pl.get("resetAfterBattleId"),
                }
                for pl in raw_players
            }
            self._seasons_list = sorted(
                (
                    {
                        "id": int(s["seasonId"]),
                        "name": s["name"],
                        "start_date": s.get("startDate"),
                        "end_date": s.get("endDate"),
                        "allowed_pokemon_ids": [int(x) for x in s.get("allowedPokemonIds", [])],
                    }
                    for s in raw_seasons
                ),
                key=lambda s: s["id"],
            )
            self._master_version = current_mv

        return current_mv, self._pokemon_by_id, self._players_by_id, self._seasons_list

    def _query_season_all_pages(
        self,
        dynamodb_client: Any,
        table_name: str,
        season_id: int,
    ) -> tuple[list[dict[str, Any]], int]:
        items: list[dict[str, Any]] = []
        pages = 0
        kwargs: dict[str, Any] = {
            "TableName": table_name,
            "IndexName": "season-index",
            "KeyConditionExpression": "seasonId = :sid",
            "ExpressionAttributeValues": {":sid": {"N": str(season_id)}},
            "ScanIndexForward": True,
        }
        while True:
            try:
                resp = dynamodb_client.query(**kwargs)
            except Exception as exc:
                raise ApiError(
                    503,
                    "TEMPORARILY_UNAVAILABLE",
                    "データの取得中に一時的な障害が発生しました。",
                    retry_after=3,
                ) from exc
            pages += 1
            for raw in resp.get("Items", []):
                parsed = deserialize_item(raw)
                if parsed is not None:
                    items.append(parsed)
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items, pages

    @staticmethod
    def _battle_item_to_snake(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(item["battleId"]),
            "battle_message_id": int(item["messageId"]),
            "battle_number": int(item["battleNumber"]),
            "player1_id": int(item["player1Id"]) if item.get("player1Id") is not None else None,
            "player2_id": int(item["player2Id"]) if item.get("player2Id") is not None else None,
            "player1_pokemon_id": int(item["player1PokemonId"]),
            "player2_pokemon_id": int(item["player2PokemonId"]),
            "first_player": int(item["firstPlayer"]) if item.get("firstPlayer") is not None else None,
            "player1_result": item["player1Result"],
            "player2_result": item["player2Result"],
            "battle_type": item.get("battleType") or "normal",
            "match_id": int(item["matchId"]) if item.get("matchId") is not None else None,
            "game_number": int(item["gameNumber"]) if item.get("gameNumber") is not None else None,
            "season_id": int(item["seasonId"]),
            "battle_date": item.get("battleDate", ""),
            "is_deleted": bool(item.get("isDeleted", False)),
        }

    @staticmethod
    def _match_item_to_snake(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "match_id": int(item["matchId"]),
            "match_type": item["matchType"],
            "tournament_name": item.get("tournamentName"),
            "opponent": item.get("opponent"),
            "match_date": item["matchDate"],
            "match_result": item.get("matchResult"),
            "player_score": int(item["playerScore"]),
            "opponent_score": int(item["opponentScore"]),
            "season_id": int(item["seasonId"]),
            "player_id": int(item["playerId"]) if item.get("playerId") is not None else None,
            "battle_message_id": int(item["messageId"]),
            "created_at": item.get("createdAt"),
            "is_deleted": bool(item.get("isDeleted", False)),
        }

    def get_season_snapshot(
        self,
        dynamodb_client: Any,
        table_prefix: str,
        season_id: int,
        master_version: int,
    ) -> tuple[SeasonSnapshot, bool]:
        """Return (snapshot, is_stale) for a single season, enforcing A-1 count check."""
        s_resp = dynamodb_client.get_item(
            TableName=f"{table_prefix}Seasons",
            Key={"seasonId": {"N": str(int(season_id))}},
            ConsistentRead=True,
        )
        s_item = deserialize_item(s_resp.get("Item"))
        if s_item is None:
            raise ApiError(404, "NOT_FOUND", "指定されたシーズンが見つかりません。")

        data_version = int(s_item.get("dataVersion", 1))
        expected_battles = s_item.get("battleCount")
        expected_matches = s_item.get("matchCount")
        data_changed_at_str = s_item.get("dataChangedAt")
        allowed_ids = [int(x) for x in s_item.get("allowedPokemonIds", [])]

        confirmed = self._confirmed.get(season_id)
        if (
            confirmed is not None
            and confirmed.data_version == data_version
            and confirmed.master_version == master_version
        ):
            return confirmed, False

        prov = self._provisional.get(season_id)
        now_mono = self._monotonic()
        if (
            prov is not None
            and prov.expires_at_mono > now_mono
            and prov.snapshot.data_version == data_version
            and prov.snapshot.master_version == master_version
        ):
            return prov.snapshot, False

        t0 = time.perf_counter()
        retried = 0
        matched = False
        raw_battles: list[dict[str, Any]] = []
        raw_matches: list[dict[str, Any]] = []
        total_pages = 0

        for attempt in range(len(RETRY_BACKOFFS) + 1):
            raw_battles, b_pages = self._query_season_all_pages(
                dynamodb_client, f"{table_prefix}Battles", season_id
            )
            raw_matches, m_pages = self._query_season_all_pages(
                dynamodb_client, f"{table_prefix}BattleMatches", season_id
            )
            total_pages = b_pages + m_pages

            battles_ok = expected_battles is None or len(raw_battles) == int(expected_battles)
            matches_ok = expected_matches is None or len(raw_matches) == int(expected_matches)
            if battles_ok and matches_ok:
                matched = True
                break
            if attempt < len(RETRY_BACKOFFS):
                retried += 1
                self._sleep(RETRY_BACKOFFS[attempt])

        load_ms = int((time.perf_counter() - t0) * 1000)
        if not matched:
            log_event(
                "gsi_count_mismatch",
                seasonId=season_id,
                expectedBattles=expected_battles,
                actualBattles=len(raw_battles),
                expectedMatches=expected_matches,
                actualMatches=len(raw_matches),
                retried=retried,
                load_ms=load_ms,
            )
            if confirmed is not None:
                return confirmed, True
            raise ApiError(
                503,
                "TEMPORARILY_UNAVAILABLE",
                "集計データを更新中です。数秒後に再度お試しください。",
                retry_after=2,
            )

        log_event(
            "season_loaded",
            seasonId=season_id,
            items_loaded=len(raw_battles) + len(raw_matches),
            pages=total_pages,
            load_ms=load_ms,
            retried=retried,
        )

        snake_battles = [self._battle_item_to_snake(b) for b in raw_battles]
        snake_matches = [self._match_item_to_snake(m) for m in raw_matches]
        active_battles = [b for b in snake_battles if not b["is_deleted"]]
        active_matches = [m for m in snake_matches if not m["is_deleted"]]

        snapshot = SeasonSnapshot(
            season_id=season_id,
            data_version=data_version,
            master_version=master_version,
            allowed_pokemon_ids=allowed_ids,
            cells=build_season_cells(active_battles),
            battles=active_battles,
            matches=active_matches,
        )

        is_recent_change = False
        if data_changed_at_str:
            try:
                changed_dt = datetime.fromisoformat(data_changed_at_str.replace("Z", "+00:00"))
                if changed_dt.tzinfo is None:
                    changed_dt = changed_dt.replace(tzinfo=timezone.utc)
                age_sec = (self._now_utc() - changed_dt).total_seconds()
                is_recent_change = age_sec < PROVISIONAL_TTL_SECONDS
            except ValueError:
                is_recent_change = False

        if is_recent_change:
            self._provisional[season_id] = _ProvisionalEntry(
                snapshot=snapshot,
                expires_at_mono=self._monotonic() + PROVISIONAL_TTL_SECONDS,
            )
        else:
            self._confirmed[season_id] = snapshot
            self._provisional.pop(season_id, None)

        return snapshot, False
