"""DynamoDB repository implementation for insert_app.py (手順3・手順5・追加レビューB-1/A-1/B-4/B-5)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import secrets
from typing import Any

from ..battle_matches import is_battle_challenge_message, prepare_battle_match
from ..dynamodb_serde import deserialize_item, from_attr, serialize_item, to_attr
from .base import BattleRepository, ConflictError, DuplicateError, NotFoundError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_token(request_id: str, attempt: int) -> str:
    digest = hashlib.sha256(f"{request_id}:{attempt}".encode("utf-8")).hexdigest()
    return digest[:32]


def _extract_cancel_reasons(exc: Exception) -> list[str]:
    response = getattr(exc, "response", None) or {}
    reasons = response.get("CancellationReasons") or []
    return [r.get("Code", "None") for r in reasons]


def _is_transaction_canceled(exc: Exception) -> bool:
    response = getattr(exc, "response", None) or {}
    code = response.get("Error", {}).get("Code", "")
    return code == "TransactionCanceledException" or exc.__class__.__name__ == "TransactionCanceledException"


def _is_conditional_failed(exc: Exception) -> bool:
    response = getattr(exc, "response", None) or {}
    code = response.get("Error", {}).get("Code", "")
    return code == "ConditionalCheckFailedException" or exc.__class__.__name__ == "ConditionalCheckFailedException"


class DynamoDBBattleRepository(BattleRepository):
    backend_name = "dynamodb"

    def __init__(
        self,
        *,
        table_prefix: str | None = None,
        dynamodb_client: Any = None,
        sts_client: Any = None,
        region_name: str | None = None,
        profile_name: str | None = None,
    ):
        self.table_prefix = (
            table_prefix
            if table_prefix is not None
            else os.environ.get("PLAKORO_DYNAMODB_TABLE_PREFIX", "plakoro_dev_")
        )
        self.backend_label = f"DynamoDB ({self.table_prefix})"
        self._dynamodb = dynamodb_client
        self._sts = sts_client
        self._region_name = region_name or os.environ.get("AWS_REGION", "ap-northeast-1")
        self._profile_name = profile_name or os.environ.get("AWS_PROFILE")
        self._caller_arn: str | None = None

    def _client(self) -> Any:
        if self._dynamodb is None:
            import boto3

            session = (
                boto3.Session(profile_name=self._profile_name, region_name=self._region_name)
                if self._profile_name
                else boto3.Session(region_name=self._region_name)
            )
            self._dynamodb = session.client("dynamodb")
        return self._dynamodb

    def _get_caller_arn(self) -> str:
        if self._caller_arn is not None:
            return self._caller_arn
        if self._sts is not None:
            self._caller_arn = self._sts.get_caller_identity().get("Arn", "unknown")
            return self._caller_arn
        try:
            import boto3

            session = (
                boto3.Session(profile_name=self._profile_name, region_name=self._region_name)
                if self._profile_name
                else boto3.Session(region_name=self._region_name)
            )
            self._caller_arn = session.client("sts").get_caller_identity().get("Arn", "unknown")
        except Exception:
            self._caller_arn = "local-dynamodb"
        return self._caller_arn

    def table_name(self, logical_name: str) -> str:
        return f"{self.table_prefix}{logical_name}"

    def _allocate_ids(self, counter_name: str, count: int = 1) -> list[int]:
        resp = self._client().update_item(
            TableName=self.table_name("Counters"),
            Key={"counterName": {"S": counter_name}},
            UpdateExpression="ADD #v :inc",
            ExpressionAttributeNames={"#v": "value"},
            ExpressionAttributeValues={":inc": {"N": str(count)}},
            ReturnValues="UPDATED_NEW",
        )
        end_value = int(from_attr(resp["Attributes"]["value"]))
        start_value = end_value - count + 1
        return list(range(start_value, end_value + 1))

    def _scan_all(self, logical_name: str) -> list[dict]:
        client = self._client()
        table = self.table_name(logical_name)
        items: list[dict] = []
        kwargs: dict[str, Any] = {"TableName": table}
        while True:
            resp = client.scan(**kwargs)
            for raw in resp.get("Items", []):
                parsed = deserialize_item(raw)
                if parsed is not None:
                    items.append(parsed)
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    def _query_season_index(
        self,
        logical_name: str,
        season_id: int,
        *,
        descending: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        client = self._client()
        table = self.table_name(logical_name)
        items: list[dict] = []
        kwargs: dict[str, Any] = {
            "TableName": table,
            "IndexName": "season-index",
            "KeyConditionExpression": "seasonId = :sid",
            "ExpressionAttributeValues": {":sid": {"N": str(season_id)}},
            "ScanIndexForward": not descending,
        }
        while True:
            resp = client.query(**kwargs)
            for raw in resp.get("Items", []):
                parsed = deserialize_item(raw)
                if parsed is not None:
                    items.append(parsed)
                    if limit is not None and len(items) >= limit:
                        return items
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    @staticmethod
    def _season_to_snake(item: dict) -> dict:
        return {
            "id": item["seasonId"],
            "name": item["name"],
            "start_date": item.get("startDate"),
            "end_date": item.get("endDate"),
            "description": item.get("description"),
            "allowed_pokemon_ids": item.get("allowedPokemonIds", []),
            "data_version": item.get("dataVersion", 1),
        }

    @staticmethod
    def _pokemon_to_snake(item: dict) -> dict:
        weaknesses = item.get("weaknesses", [])
        return {
            "id": item["pokemonId"],
            "name": item["name"],
            "emoji_id": item.get("emojiId"),
            "type1": item.get("type1"),
            "type2": item.get("type2"),
            "weaknesses": json.dumps(weaknesses, ensure_ascii=False),
        }

    @staticmethod
    def _player_to_snake(item: dict, accounts: list[dict] | None = None) -> dict:
        result = {
            "id": item["playerId"],
            "name": item["name"],
            "discord_user_id": item.get("discordUserId"),
            "created_at": item.get("createdAt"),
            "is_public": item.get("isPublic", True),
            "reset_at": item.get("resetAt"),
            "reset_after_battle_id": item.get("resetAfterBattleId"),
        }
        if accounts is not None:
            result["discord_accounts"] = accounts
        return result

    @staticmethod
    def _message_to_snake(item: dict) -> dict:
        return {
            "id": item["messageId"],
            "discord_message_id": item["discordMessageId"],
            "season_id": item["seasonId"],
            "battle_date": item.get("battleDate", ""),
            "raw_content": item.get("rawContent", ""),
            "created_at": item.get("createdAt", ""),
            "last_battle_number": item.get("lastBattleNumber", 0),
            "match_id": item.get("matchId"),
            "is_battle_challenge": bool(item.get("isBattleChallenge", False)),
        }

    def list_seasons(self) -> list[dict]:
        items = self._scan_all("Seasons")
        items.sort(key=lambda x: x["seasonId"])
        return [self._season_to_snake(item) for item in items]

    def list_pokemon(self) -> list[dict]:
        items = self._scan_all("Pokemon")
        items.sort(key=lambda x: x["pokemonId"])
        return [self._pokemon_to_snake(item) for item in items]

    def list_players(self, order: str = "id", include_accounts: bool = False) -> list[dict]:
        items = self._scan_all("Players")
        accounts_by_player: dict[int, list[dict]] = {}
        if include_accounts:
            for acc in self._scan_all("DiscordAccounts"):
                pid = acc["playerId"]
                accounts_by_player.setdefault(pid, []).append(
                    {
                        "discord_user_id": acc["discordUserId"],
                        "label": acc.get("label"),
                        "created_at": acc.get("createdAt"),
                    }
                )
            for acc_list in accounts_by_player.values():
                acc_list.sort(key=lambda a: (a.get("created_at") or "", a["discord_user_id"]))

        if order == "name":
            items.sort(key=lambda x: (x["name"].casefold(), x["playerId"]))
        else:
            items.sort(key=lambda x: x["playerId"])

        return [
            self._player_to_snake(
                item,
                accounts_by_player.get(item["playerId"], []) if include_accounts else None,
            )
            for item in items
        ]

    def list_messages(
        self,
        limit: int | None = None,
        challenge_only: bool = False,
    ) -> list[dict]:
        seasons = self._scan_all("Seasons")
        season_ids = sorted((s["seasonId"] for s in seasons), reverse=True)
        merged: list[dict] = []
        for sid in season_ids:
            season_items = self._query_season_index("BattleMessages", sid, descending=True)
            for item in season_items:
                if challenge_only and not item.get("isBattleChallenge"):
                    continue
                merged.append(item)
        merged.sort(key=lambda x: x["messageId"], reverse=True)
        if limit is not None:
            merged = merged[:limit]
        return [self._message_to_snake(item) for item in merged]

    def get_message(self, message_id: int) -> dict | None:
        resp = self._client().get_item(
            TableName=self.table_name("BattleMessages"),
            Key={"messageId": {"N": str(int(message_id))}},
            ConsistentRead=True,
        )
        item = deserialize_item(resp.get("Item"))
        return self._message_to_snake(item) if item is not None else None

    def get_latest_message(self) -> dict | None:
        messages = self.list_messages(limit=1)
        return messages[0] if messages else None

    def get_battle(self, battle_id: int) -> dict | None:
        resp = self._client().get_item(
            TableName=self.table_name("Battles"),
            Key={"battleId": {"N": str(int(battle_id))}},
            ConsistentRead=True,
        )
        item = deserialize_item(resp.get("Item"))
        if item is None or item.get("isDeleted"):
            return None
        pokemon_map = {p["id"]: p["name"] for p in self.list_pokemon()}
        player_map = {p["id"]: p["name"] for p in self.list_players()}
        return {
            "id": item["battleId"],
            "battle_message_id": item["messageId"],
            "battle_number": item["battleNumber"],
            "player1_id": item.get("player1Id"),
            "player2_id": item.get("player2Id"),
            "player1_pokemon_id": item["player1PokemonId"],
            "player2_pokemon_id": item["player2PokemonId"],
            "player1_pokemon": pokemon_map.get(item["player1PokemonId"], ""),
            "player2_pokemon": pokemon_map.get(item["player2PokemonId"], ""),
            "player1_name": player_map.get(item.get("player1Id")),
            "player2_name": player_map.get(item.get("player2Id")),
            "first_player": item.get("firstPlayer"),
            "player1_result": item["player1Result"],
            "player2_result": item["player2Result"],
            "battle_type": item.get("battleType", "normal"),
            "match_id": item.get("matchId"),
            "game_number": item.get("gameNumber"),
        }

    def get_match(self, match_id: int) -> dict | None:
        resp = self._client().get_item(
            TableName=self.table_name("BattleMatches"),
            Key={"matchId": {"N": str(int(match_id))}},
            ConsistentRead=True,
        )
        item = deserialize_item(resp.get("Item"))
        if item is None or item.get("isDeleted"):
            return None
        pokemon_map = {p["id"]: p["name"] for p in self.list_pokemon()}
        player_map = {p["id"]: p["name"] for p in self.list_players()}
        games = [
            {
                "id": g["battleId"],
                "game_number": g["gameNumber"],
                "player1_result": g["player1Result"],
                "player1_pokemon": pokemon_map.get(g["player1PokemonId"], ""),
                "player2_pokemon": pokemon_map.get(g["player2PokemonId"], ""),
            }
            for g in sorted(item.get("games", []), key=lambda g: g["gameNumber"])
        ]
        return {
            "match_id": item["matchId"],
            "match_type": item["matchType"],
            "tournament_name": item.get("tournamentName"),
            "opponent": item.get("opponent"),
            "match_date": item["matchDate"],
            "match_result": item.get("matchResult"),
            "player_score": item["playerScore"],
            "opponent_score": item["opponentScore"],
            "season_id": item["seasonId"],
            "player_id": item.get("playerId"),
            "player_name": player_map.get(item.get("playerId")),
            "battle_message_id": item["messageId"],
            "games": games,
        }

    def list_recent_battles(
        self,
        limit: int = 50,
        include_ids: list[int] | None = None,
    ) -> list[dict]:
        seasons = self._scan_all("Seasons")
        season_ids = sorted((s["seasonId"] for s in seasons), reverse=True)
        by_id: dict[int, dict] = {}
        for sid in season_ids:
            items = self._query_season_index("Battles", sid, descending=True)
            for item in items:
                if not item.get("isDeleted"):
                    by_id[item["battleId"]] = item

        extra_ids = [int(x) for x in (include_ids or []) if int(x) not in by_id]
        if extra_ids:
            table = self.table_name("Battles")
            for offset in range(0, len(extra_ids), 100):
                chunk = extra_ids[offset : offset + 100]
                resp = self._client().batch_get_item(
                    RequestItems={
                        table: {
                            "Keys": [{"battleId": {"N": str(bid)}} for bid in chunk],
                            "ConsistentRead": True,
                        }
                    }
                )
                for raw in resp.get("Responses", {}).get(table, []):
                    parsed = deserialize_item(raw)
                    if parsed is not None and not parsed.get("isDeleted"):
                        by_id[parsed["battleId"]] = parsed

        pokemon_map = {p["id"]: p["name"] for p in self.list_pokemon()}
        player_map = {p["id"]: p["name"] for p in self.list_players()}
        latest_desc = sorted(by_id.values(), key=lambda x: x["battleId"], reverse=True)[:limit]
        latest_asc = sorted(latest_desc, key=lambda x: x["battleId"])
        return [
            {
                "id": item["battleId"],
                "battle_message_id": item["messageId"],
                "battle_number": item["battleNumber"],
                "player1_pokemon": pokemon_map.get(item["player1PokemonId"], ""),
                "player2_pokemon": pokemon_map.get(item["player2PokemonId"], ""),
                "player1_name": player_map.get(item.get("player1Id")),
                "player2_name": player_map.get(item.get("player2Id")),
                "first_player": item.get("firstPlayer"),
                "player1_result": item["player1Result"],
                "player2_result": item["player2Result"],
                "battle_type": item.get("battleType", "normal"),
                "match_id": item.get("matchId"),
            }
            for item in latest_asc
        ]

    def create_message(
        self,
        discord_message_id: str,
        season_id: int,
        battle_date: str | None,
        raw_content: str,
        created_at: str | None = None,
    ) -> int:
        clean_discord_id = (discord_message_id or "").strip()
        if not clean_discord_id:
            raise ValueError("Discord Message IDを入力してください。")
        ts = created_at or _now_iso()
        message_id = self._allocate_ids("messageId", 1)[0]
        msg_item: dict[str, Any] = {
            "messageId": message_id,
            "discordMessageId": clean_discord_id,
            "seasonId": int(season_id),
            "battleDate": (battle_date or "").strip() or None,
            "rawContent": raw_content if raw_content else None,
            "createdAt": ts,
            "lastBattleNumber": 0,
        }
        if is_battle_challenge_message(raw_content):
            msg_item["isBattleChallenge"] = True

        try:
            self._client().transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name("DiscordMessages"),
                            "Item": serialize_item(
                                {
                                    "discordMessageId": clean_discord_id,
                                    "messageId": message_id,
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(discordMessageId)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name("BattleMessages"),
                            "Item": serialize_item(msg_item),
                            "ConditionExpression": "attribute_not_exists(messageId)",
                        }
                    },
                ]
            )
            return message_id
        except Exception as exc:
            if _is_transaction_canceled(exc):
                reasons = _extract_cancel_reasons(exc)
                if reasons and reasons[0] == "ConditionalCheckFailed":
                    raise DuplicateError("このDiscordメッセージIDはすでに登録されています。") from exc
            raise

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

        req_id = (request_id or "").strip() or secrets.token_hex(16)
        n_rows = len(rows)

        for attempt in range(1, 4):
            resp = self._client().get_item(
                TableName=self.table_name("BattleMessages"),
                Key={"messageId": {"N": str(int(message_id))}},
                ConsistentRead=True,
            )
            msg = deserialize_item(resp.get("Item"))
            if msg is None:
                raise NotFoundError("指定されたメッセージが存在しません。")

            if msg.get("lastRequestId") == req_id and msg.get("lastRequestBattleIds"):
                return [int(x) for x in msg["lastRequestBattleIds"]]

            k = int(msg.get("lastBattleNumber", 0))
            season_id = int(msg["seasonId"])
            battle_date = msg.get("battleDate")
            battle_ids = self._allocate_ids("battleId", n_rows)
            now = _now_iso()

            transact_items: list[dict[str, Any]] = [
                {
                    "Update": {
                        "TableName": self.table_name("BattleMessages"),
                        "Key": {"messageId": {"N": str(int(message_id))}},
                        "UpdateExpression": (
                            "SET lastBattleNumber = :new_k, "
                            "lastRequestId = :req_id, "
                            "lastRequestBattleIds = :battle_ids"
                        ),
                        "ConditionExpression": (
                            "lastBattleNumber = :expected_k AND "
                            "(attribute_not_exists(lastRequestId) OR lastRequestId <> :req_id)"
                        ),
                        "ExpressionAttributeValues": {
                            ":expected_k": {"N": str(k)},
                            ":new_k": {"N": str(k + n_rows)},
                            ":req_id": {"S": req_id},
                            ":battle_ids": to_attr(battle_ids),
                        },
                    }
                },
                {
                    "Update": {
                        "TableName": self.table_name("Seasons"),
                        "Key": {"seasonId": {"N": str(season_id)}},
                        "UpdateExpression": (
                            "ADD dataVersion :one, battleCount :n "
                            "SET dataChangedAt = :now"
                        ),
                        "ExpressionAttributeValues": {
                            ":one": {"N": "1"},
                            ":n": {"N": str(n_rows)},
                            ":now": {"S": now},
                        },
                    }
                },
            ]

            for offset, (bid, row) in enumerate(zip(battle_ids, rows), start=1):
                p1_result = row["player1_result"]
                p2_result = {"win": "lose", "lose": "win"}[p1_result]
                first_player = row.get("first_player")
                if first_player == "":
                    first_player = None
                elif first_player is not None:
                    first_player = int(first_player)
                battle_item: dict[str, Any] = {
                    "battleId": bid,
                    "messageId": int(message_id),
                    "battleNumber": k + offset,
                    "player1Id": int(player1_id) if player1_id is not None else None,
                    "player2Id": int(player2_id) if player2_id is not None else None,
                    "player1PokemonId": int(row["player1_pokemon_id"]),
                    "player2PokemonId": int(row["player2_pokemon_id"]),
                    "firstPlayer": first_player,
                    "player1Result": p1_result,
                    "player2Result": p2_result,
                    "battleType": row.get("battle_type") or "normal",
                    "seasonId": season_id,
                    "battleDate": battle_date,
                }
                transact_items.append(
                    {
                        "Put": {
                            "TableName": self.table_name("Battles"),
                            "Item": serialize_item(battle_item),
                            "ConditionExpression": "attribute_not_exists(battleId)",
                        }
                    }
                )

            token = _client_token(req_id, attempt)
            try:
                self._client().transact_write_items(
                    TransactItems=transact_items,
                    ClientRequestToken=token,
                )
                return battle_ids
            except Exception as exc:
                if _is_transaction_canceled(exc):
                    check_resp = self._client().get_item(
                        TableName=self.table_name("BattleMessages"),
                        Key={"messageId": {"N": str(int(message_id))}},
                        ConsistentRead=True,
                    )
                    refreshed = deserialize_item(check_resp.get("Item"))
                    if (
                        refreshed is not None
                        and refreshed.get("lastRequestId") == req_id
                        and refreshed.get("lastRequestBattleIds")
                    ):
                        return [int(x) for x in refreshed["lastRequestBattleIds"]]
                    continue
                raise

        raise ConflictError("他の操作と競合しました。再読み込みしてください。")

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
        resp = self._client().get_item(
            TableName=self.table_name("BattleMessages"),
            Key={"messageId": {"N": str(int(battle_message_id))}},
            ConsistentRead=True,
        )
        msg = deserialize_item(resp.get("Item"))
        if msg is None:
            raise NotFoundError("指定されたメッセージが存在しません。")
        if msg.get("matchId") is not None:
            raise DuplicateError("このメッセージの大会対戦は登録済みです。")

        prepared = prepare_battle_match(games)
        normalized_games = prepared["games"]
        k = int(msg.get("lastBattleNumber", 0))
        match_id = self._allocate_ids("matchId", 1)[0]
        battle_ids = self._allocate_ids("battleId", len(normalized_games))
        now = _now_iso()

        embedded_games: list[dict[str, Any]] = []
        battle_puts: list[dict[str, Any]] = []
        for game_number, (bid, game) in enumerate(zip(battle_ids, normalized_games), start=1):
            embedded = {
                "battleId": bid,
                "gameNumber": game_number,
                "player1PokemonId": game["player1_pokemon_id"],
                "player2PokemonId": game["player2_pokemon_id"],
                "player1Result": game["player1_result"],
            }
            if game["first_player"] is not None:
                embedded["firstPlayer"] = game["first_player"]
            embedded_games.append(embedded)

            battle_item: dict[str, Any] = {
                "battleId": bid,
                "messageId": int(battle_message_id),
                "battleNumber": k + game_number,
                "player1Id": int(player_id) if player_id is not None else None,
                "player1PokemonId": game["player1_pokemon_id"],
                "player2PokemonId": game["player2_pokemon_id"],
                "firstPlayer": game["first_player"],
                "player1Result": game["player1_result"],
                "player2Result": game["player2_result"],
                "battleType": "challenge",
                "matchId": match_id,
                "gameNumber": game_number,
                "seasonId": int(season_id),
                "battleDate": match_date,
            }
            battle_puts.append(
                {
                    "Put": {
                        "TableName": self.table_name("Battles"),
                        "Item": serialize_item(battle_item),
                        "ConditionExpression": "attribute_not_exists(battleId)",
                    }
                }
            )

        match_item: dict[str, Any] = {
            "matchId": match_id,
            "matchType": "battle_challenge",
            "tournamentName": (tournament_name or "").strip() or None,
            "opponent": (opponent or "").strip() or None,
            "matchDate": match_date,
            "matchResult": prepared["match_result"],
            "playerScore": prepared["player_score"],
            "opponentScore": prepared["opponent_score"],
            "seasonId": int(season_id),
            "playerId": int(player_id) if player_id is not None else None,
            "messageId": int(battle_message_id),
            "createdAt": now,
            "games": embedded_games,
        }

        transact_items = [
            {
                "Update": {
                    "TableName": self.table_name("BattleMessages"),
                    "Key": {"messageId": {"N": str(int(battle_message_id))}},
                    "UpdateExpression": "SET matchId = :mid, lastBattleNumber = :new_k",
                    "ConditionExpression": "attribute_not_exists(matchId) AND lastBattleNumber = :expected_k",
                    "ExpressionAttributeValues": {
                        ":mid": {"N": str(match_id)},
                        ":expected_k": {"N": str(k)},
                        ":new_k": {"N": str(k + len(normalized_games))},
                    },
                }
            },
            {
                "Put": {
                    "TableName": self.table_name("BattleMatches"),
                    "Item": serialize_item(match_item),
                    "ConditionExpression": "attribute_not_exists(matchId)",
                }
            },
            *battle_puts,
            {
                "Update": {
                    "TableName": self.table_name("Seasons"),
                    "Key": {"seasonId": {"N": str(int(season_id))}},
                    "UpdateExpression": (
                        "ADD dataVersion :one, battleCount :g, matchCount :one "
                        "SET dataChangedAt = :now"
                    ),
                    "ExpressionAttributeValues": {
                        ":one": {"N": "1"},
                        ":g": {"N": str(len(normalized_games))},
                        ":now": {"S": now},
                    },
                }
            },
        ]

        try:
            self._client().transact_write_items(TransactItems=transact_items)
            return match_id
        except Exception as exc:
            if _is_transaction_canceled(exc):
                check_resp = self._client().get_item(
                    TableName=self.table_name("BattleMessages"),
                    Key={"messageId": {"N": str(int(battle_message_id))}},
                    ConsistentRead=True,
                )
                refreshed = deserialize_item(check_resp.get("Item"))
                if refreshed is not None and refreshed.get("matchId") is not None:
                    raise DuplicateError("このメッセージの大会対戦は登録済みです。") from exc
                raise ConflictError("他の操作と競合しました。再読み込みしてください。") from exc
            raise

    def create_player(
        self,
        discord_user_id: str,
        name: str,
        created_at: str | None = None,
    ) -> int:
        clean_discord_id = (discord_user_id or "").strip()
        clean_name = (name or "").strip()
        if not clean_discord_id or not clean_name:
            raise ValueError("Discord User IDとプレイヤー名を入力してください。")
        ts = created_at or _now_iso()
        player_id = self._allocate_ids("playerId", 1)[0]

        try:
            self._client().transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.table_name("DiscordAccounts"),
                            "Item": serialize_item(
                                {
                                    "discordUserId": clean_discord_id,
                                    "playerId": player_id,
                                    "createdAt": ts,
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(discordUserId)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name("Players"),
                            "Item": serialize_item(
                                {
                                    "playerId": player_id,
                                    "name": clean_name,
                                    "discordUserId": clean_discord_id,
                                    "createdAt": ts,
                                    "isPublic": True,
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(playerId)",
                        }
                    },
                    {
                        "Update": {
                            "TableName": self.table_name("Counters"),
                            "Key": {"counterName": {"S": "masterVersion"}},
                            "UpdateExpression": "ADD #v :one",
                            "ExpressionAttributeNames": {"#v": "value"},
                            "ExpressionAttributeValues": {":one": {"N": "1"}},
                        }
                    },
                ]
            )
            return player_id
        except Exception as exc:
            if _is_transaction_canceled(exc):
                reasons = _extract_cancel_reasons(exc)
                if reasons and reasons[0] == "ConditionalCheckFailed":
                    raise DuplicateError("このDiscord User IDはすでに登録されています。") from exc
            raise

    def link_discord_account(
        self,
        player_id: int,
        discord_user_id: str,
        label: str | None = None,
    ) -> None:
        clean_discord_id = (discord_user_id or "").strip()
        if not clean_discord_id:
            raise ValueError("Discord User IDを入力してください。")

        resp = self._client().get_item(
            TableName=self.table_name("Players"),
            Key={"playerId": {"N": str(int(player_id))}},
            ConsistentRead=True,
        )
        player = deserialize_item(resp.get("Item"))
        if player is None:
            raise NotFoundError("指定されたプレイヤーが存在しません。")

        item: dict[str, Any] = {
            "discordUserId": clean_discord_id,
            "playerId": int(player_id),
            "createdAt": _now_iso(),
            "label": (label or "").strip() or None,
        }
        try:
            self._client().put_item(
                TableName=self.table_name("DiscordAccounts"),
                Item=serialize_item(item),
                ConditionExpression="attribute_not_exists(discordUserId)",
            )
        except Exception as exc:
            if _is_conditional_failed(exc):
                raise DuplicateError("このDiscord User IDはすでに他のプレイヤーに紐づいています。") from exc
            raise

    def delete_battle(self, battle_id: int, reason: str) -> None:
        clean_reason = (reason or "").strip()
        if not clean_reason:
            raise ValueError("削除理由を入力してください。")

        resp = self._client().get_item(
            TableName=self.table_name("Battles"),
            Key={"battleId": {"N": str(int(battle_id))}},
            ConsistentRead=True,
        )
        battle = deserialize_item(resp.get("Item"))
        if battle is None or battle.get("isDeleted"):
            raise NotFoundError("指定された対戦が見つからないか、すでに削除されています。")
        if battle.get("matchId") is not None:
            raise ValueError(
                "大会対戦（BO3）の個別ゲームは単独で削除できません。シリーズ単位で削除してください。"
            )

        now = _now_iso()
        audit_id = secrets.token_hex(4)
        after_snapshot = {**battle, "isDeleted": True}
        audit_item = {
            "targetKey": f"battle#{int(battle_id)}",
            "loggedAt": f"{now}#{audit_id}",
            "auditId": audit_id,
            "action": "delete",
            "performedBy": self._get_caller_arn(),
            "reason": clean_reason,
            "before": battle,
            "after": after_snapshot,
        }

        try:
            self._client().transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.table_name("Battles"),
                            "Key": {"battleId": {"N": str(int(battle_id))}},
                            "UpdateExpression": "SET isDeleted = :t",
                            "ConditionExpression": "attribute_not_exists(isDeleted) OR isDeleted = :f",
                            "ExpressionAttributeValues": {
                                ":t": {"BOOL": True},
                                ":f": {"BOOL": False},
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table_name("BattleAuditLogs"),
                            "Item": serialize_item(audit_item),
                            "ConditionExpression": "attribute_not_exists(targetKey) AND attribute_not_exists(loggedAt)",
                        }
                    },
                    {
                        "Update": {
                            "TableName": self.table_name("Seasons"),
                            "Key": {"seasonId": {"N": str(int(battle["seasonId"]))}},
                            "UpdateExpression": "ADD dataVersion :one SET dataChangedAt = :now",
                            "ExpressionAttributeValues": {
                                ":one": {"N": "1"},
                                ":now": {"S": now},
                            },
                        }
                    },
                ]
            )
        except Exception as exc:
            if _is_transaction_canceled(exc):
                raise NotFoundError("指定された対戦はすでに削除されています。") from exc
            raise

    def delete_match(self, match_id: int, reason: str) -> None:
        clean_reason = (reason or "").strip()
        if not clean_reason:
            raise ValueError("削除理由を入力してください。")

        resp = self._client().get_item(
            TableName=self.table_name("BattleMatches"),
            Key={"matchId": {"N": str(int(match_id))}},
            ConsistentRead=True,
        )
        match_item = deserialize_item(resp.get("Item"))
        if match_item is None or match_item.get("isDeleted"):
            raise NotFoundError("指定された大会対戦が見つからないか、すでに削除されています。")

        game_ids = [int(g["battleId"]) for g in match_item.get("games", [])]
        game_items: list[dict[str, Any]] = []
        for gid in game_ids:
            gresp = self._client().get_item(
                TableName=self.table_name("Battles"),
                Key={"battleId": {"N": str(gid)}},
                ConsistentRead=True,
            )
            gparsed = deserialize_item(gresp.get("Item"))
            if gparsed is not None:
                game_items.append(gparsed)

        now = _now_iso()
        audit_id = secrets.token_hex(4)
        performer = self._get_caller_arn()

        transact_items: list[dict[str, Any]] = [
            {
                "Update": {
                    "TableName": self.table_name("BattleMatches"),
                    "Key": {"matchId": {"N": str(int(match_id))}},
                    "UpdateExpression": "SET isDeleted = :t",
                    "ConditionExpression": "attribute_not_exists(isDeleted) OR isDeleted = :f",
                    "ExpressionAttributeValues": {
                        ":t": {"BOOL": True},
                        ":f": {"BOOL": False},
                    },
                }
            },
            {
                "Update": {
                    "TableName": self.table_name("BattleMessages"),
                    "Key": {"messageId": {"N": str(int(match_item["messageId"]))}},
                    "UpdateExpression": "REMOVE matchId",
                    "ConditionExpression": "matchId = :mid",
                    "ExpressionAttributeValues": {":mid": {"N": str(int(match_id))}},
                }
            },
            {
                "Put": {
                    "TableName": self.table_name("BattleAuditLogs"),
                    "Item": serialize_item(
                        {
                            "targetKey": f"match#{int(match_id)}",
                            "loggedAt": f"{now}#{audit_id}",
                            "auditId": audit_id,
                            "action": "delete",
                            "performedBy": performer,
                            "reason": clean_reason,
                            "before": match_item,
                            "after": {**match_item, "isDeleted": True},
                        }
                    ),
                    "ConditionExpression": "attribute_not_exists(targetKey) AND attribute_not_exists(loggedAt)",
                }
            },
            {
                "Update": {
                    "TableName": self.table_name("Seasons"),
                    "Key": {"seasonId": {"N": str(int(match_item["seasonId"]))}},
                    "UpdateExpression": "ADD dataVersion :one SET dataChangedAt = :now",
                    "ExpressionAttributeValues": {
                        ":one": {"N": "1"},
                        ":now": {"S": now},
                    },
                }
            },
        ]

        for gitem in game_items:
            gid = int(gitem["battleId"])
            transact_items.append(
                {
                    "Update": {
                        "TableName": self.table_name("Battles"),
                        "Key": {"battleId": {"N": str(gid)}},
                        "UpdateExpression": "SET isDeleted = :t",
                        "ExpressionAttributeValues": {":t": {"BOOL": True}},
                    }
                }
            )
            transact_items.append(
                {
                    "Put": {
                        "TableName": self.table_name("BattleAuditLogs"),
                        "Item": serialize_item(
                            {
                                "targetKey": f"battle#{gid}",
                                "loggedAt": f"{now}#{audit_id}",
                                "auditId": audit_id,
                                "action": "delete",
                                "performedBy": performer,
                                "reason": clean_reason,
                                "before": gitem,
                                "after": {**gitem, "isDeleted": True},
                            }
                        ),
                        "ConditionExpression": "attribute_not_exists(targetKey) AND attribute_not_exists(loggedAt)",
                    }
                }
            )

        try:
            self._client().transact_write_items(TransactItems=transact_items)
        except Exception as exc:
            if _is_transaction_canceled(exc):
                raise ConflictError("大会対戦の削除で競合が発生しました。再読み込みしてください。") from exc
            raise
