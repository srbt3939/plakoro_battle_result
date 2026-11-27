"""SQLite -> DynamoDB migration script (手順4・追加レビューD1/D8/D9).

Modes:
  --dry-run (default): Read SQLite (ro), transform, run offline checks, no AWS access.
  --execute: Backup SQLite, verify 13 tables, idempotently PutItem all records, verify, write marker.
  --verify: Verify already-migrated DynamoDB contents against SQLite without writing.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.battle_matches import calculate_series_result, is_battle_challenge_message  # noqa: E402
from app.dynamodb_serde import deserialize_item, serialize_item  # noqa: E402
from scripts.output_result import SEASON_ALLOWED_POKEMON_IDS, parse_weaknesses  # noqa: E402
from scripts.verify_aws_setup import EXPECTED_TABLES  # noqa: E402

MAX_ITEM_BYTES = 400 * 1024
REGISTRATION_ORDER = (
    "Pokemon",
    "Seasons",
    "Players",
    "DiscordAccounts",
    "BattleMessages",
    "DiscordMessages",
    "BattleMatches",
    "Battles",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _open_sqlite_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def extract_and_transform(
    db_path: Path,
    *,
    migrated_at: str | None = None,
    sidecar: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    ts = migrated_at or _now_iso()
    sidecar = sidecar or {}
    player_overrides: dict[int, dict[str, Any]] = {
        int(k): v for k, v in (sidecar.get("players_meta") or {}).items()
    }
    extra_discord_accounts: list[dict[str, Any]] = list(
        sidecar.get("extra_discord_accounts") or []
    )

    with _open_sqlite_ro(db_path) as conn:
        raw_players = [dict(r) for r in conn.execute("SELECT * FROM players ORDER BY id").fetchall()]
        raw_pokemon = [dict(r) for r in conn.execute("SELECT * FROM pokemon ORDER BY id").fetchall()]
        raw_seasons = [dict(r) for r in conn.execute("SELECT * FROM seasons ORDER BY id").fetchall()]
        raw_messages = [dict(r) for r in conn.execute("SELECT * FROM battle_messages ORDER BY id").fetchall()]
        raw_matches = [dict(r) for r in conn.execute("SELECT * FROM battle_matches ORDER BY match_id").fetchall()]
        raw_battles = [dict(r) for r in conn.execute("SELECT * FROM battles ORDER BY id").fetchall()]

    messages_by_id = {int(m["id"]): m for m in raw_messages}
    matches_by_id = {int(m["match_id"]): m for m in raw_matches}
    match_by_message_id = {
        int(m["battle_message_id"]): int(m["match_id"])
        for m in raw_matches
        if m.get("battle_message_id") is not None
    }

    max_battle_num_by_msg: dict[int, int] = {}
    games_by_match_id: dict[int, list[dict[str, Any]]] = {}
    for b in raw_battles:
        mid = int(b["battle_message_id"])
        bnum = int(b["battle_number"])
        if bnum > max_battle_num_by_msg.get(mid, 0):
            max_battle_num_by_msg[mid] = bnum
        if b.get("match_id") is not None:
            games_by_match_id.setdefault(int(b["match_id"]), []).append(b)

    max_pokemon_id = max((int(p["id"]) for p in raw_pokemon), default=0)

    # 1. Players & DiscordAccounts
    players_items: list[dict[str, Any]] = []
    discord_accounts_items: list[dict[str, Any]] = []
    for p in raw_players:
        pid = int(p["id"])
        discord_uid = str(p["discord_user_id"]).strip()
        meta = player_overrides.get(pid, {})
        p_item: dict[str, Any] = {
            "playerId": pid,
            "name": p["name"],
            "discordUserId": discord_uid,
            "isPublic": bool(meta.get("isPublic", True)),
        }
        if p.get("created_at"):
            p_item["createdAt"] = str(p["created_at"])
        if meta.get("resetAt"):
            p_item["resetAt"] = str(meta["resetAt"])
        if meta.get("resetAfterBattleId") is not None:
            p_item["resetAfterBattleId"] = int(meta["resetAfterBattleId"])
        players_items.append(p_item)

        da_item: dict[str, Any] = {
            "discordUserId": discord_uid,
            "playerId": pid,
        }
        if p.get("created_at"):
            da_item["createdAt"] = str(p["created_at"])
        discord_accounts_items.append(da_item)

    seen_discord_ids = {d["discordUserId"] for d in discord_accounts_items}
    for extra in extra_discord_accounts:
        duid = str(extra["discordUserId"]).strip()
        if duid and duid not in seen_discord_ids:
            da_extra: dict[str, Any] = {
                "discordUserId": duid,
                "playerId": int(extra["playerId"]),
                "createdAt": str(extra.get("createdAt") or ts),
            }
            if extra.get("label"):
                da_extra["label"] = str(extra["label"])
            discord_accounts_items.append(da_extra)
            seen_discord_ids.add(duid)

    # 2. Pokemon
    pokemon_items: list[dict[str, Any]] = []
    for pk in raw_pokemon:
        pk_item: dict[str, Any] = {
            "pokemonId": int(pk["id"]),
            "name": pk["name"],
            "type1": pk["type1"],
            "weaknesses": parse_weaknesses(pk.get("weaknesses")),
        }
        if pk.get("emoji_id"):
            pk_item["emojiId"] = str(pk["emoji_id"])
        if pk.get("type2"):
            pk_item["type2"] = str(pk["type2"])
        pokemon_items.append(pk_item)

    # 3. BattleMessages & DiscordMessages
    battle_messages_items: list[dict[str, Any]] = []
    discord_messages_items: list[dict[str, Any]] = []
    for m in raw_messages:
        mid = int(m["id"])
        dmid = str(m["discord_message_id"])
        raw_text = m["raw_content"] or ""
        bm_item: dict[str, Any] = {
            "messageId": mid,
            "discordMessageId": dmid,
            "seasonId": int(m["season_id"]),
            "createdAt": str(m["created_at"]),
            "lastBattleNumber": max_battle_num_by_msg.get(mid, 0),
        }
        if raw_text:
            bm_item["rawContent"] = raw_text
        bdate = (m.get("battle_date") or "").strip()
        if bdate:
            bm_item["battleDate"] = bdate
        if mid in match_by_message_id:
            bm_item["matchId"] = match_by_message_id[mid]
        if is_battle_challenge_message(raw_text):
            bm_item["isBattleChallenge"] = True
        battle_messages_items.append(bm_item)
        discord_messages_items.append(
            {
                "discordMessageId": dmid,
                "messageId": mid,
            }
        )

    # 4. BattleMatches
    battle_matches_items: list[dict[str, Any]] = []
    for mt in raw_matches:
        match_id = int(mt["match_id"])
        raw_games = sorted(
            games_by_match_id.get(match_id, []),
            key=lambda g: int(g["game_number"]),
        )
        embedded_games = []
        for g in raw_games:
            emb: dict[str, Any] = {
                "battleId": int(g["id"]),
                "gameNumber": int(g["game_number"]),
                "player1PokemonId": int(g["player1_pokemon_id"]),
                "player2PokemonId": int(g["player2_pokemon_id"]),
                "player1Result": g["player1_result"],
            }
            if g.get("first_player") is not None:
                emb["firstPlayer"] = int(g["first_player"])
            embedded_games.append(emb)

        mt_item: dict[str, Any] = {
            "matchId": match_id,
            "matchType": mt["match_type"],
            "matchDate": mt["match_date"],
            "playerScore": int(mt["player_score"]),
            "opponentScore": int(mt["opponent_score"]),
            "seasonId": int(mt["season_id"]),
            "messageId": int(mt["battle_message_id"]),
            "createdAt": str(mt["created_at"]),
            "games": embedded_games,
        }
        if mt.get("tournament_name"):
            mt_item["tournamentName"] = mt["tournament_name"]
        if mt.get("opponent"):
            mt_item["opponent"] = mt["opponent"]
        if mt.get("match_result"):
            mt_item["matchResult"] = mt["match_result"]
        if mt.get("player_id") is not None:
            mt_item["playerId"] = int(mt["player_id"])
        battle_matches_items.append(mt_item)

    # 5. Battles
    battles_items: list[dict[str, Any]] = []
    season_battle_counts: dict[int, int] = {}
    season_match_counts: dict[int, int] = {}
    for mt_item in battle_matches_items:
        sid = int(mt_item["seasonId"])
        season_match_counts[sid] = season_match_counts.get(sid, 0) + 1

    for b in raw_battles:
        bid = int(b["id"])
        msg_id = int(b["battle_message_id"])
        msg = messages_by_id[msg_id]
        match_id_val = b.get("match_id")
        match_obj = matches_by_id.get(int(match_id_val)) if match_id_val is not None else None

        season_id = (
            int(match_obj["season_id"])
            if match_obj is not None and match_obj.get("season_id") is not None
            else int(msg["season_id"])
        )
        raw_bdate = (
            (match_obj.get("match_date") if match_obj is not None else None)
            or msg.get("battle_date")
            or ""
        ).strip()

        b_item: dict[str, Any] = {
            "battleId": bid,
            "messageId": msg_id,
            "battleNumber": int(b["battle_number"]),
            "player1PokemonId": int(b["player1_pokemon_id"]),
            "player2PokemonId": int(b["player2_pokemon_id"]),
            "player1Result": b["player1_result"],
            "player2Result": b["player2_result"],
            "battleType": b.get("battle_type") or "normal",
            "seasonId": season_id,
        }
        if b.get("player1_id") is not None:
            b_item["player1Id"] = int(b["player1_id"])
        if b.get("player2_id") is not None:
            b_item["player2Id"] = int(b["player2_id"])
        if b.get("first_player") is not None:
            b_item["firstPlayer"] = int(b["first_player"])
        if match_id_val is not None:
            b_item["matchId"] = int(match_id_val)
        if b.get("game_number") is not None:
            b_item["gameNumber"] = int(b["game_number"])
        if raw_bdate:
            b_item["battleDate"] = raw_bdate

        battles_items.append(b_item)
        season_battle_counts[season_id] = season_battle_counts.get(season_id, 0) + 1

    # 6. Seasons (includes battleCount, matchCount, dataChangedAt per D1/D9)
    seasons_items: list[dict[str, Any]] = []
    for s in raw_seasons:
        sid = int(s["id"])
        configured_allowed = list(
            SEASON_ALLOWED_POKEMON_IDS.get(sid) or range(1, max_pokemon_id + 1)
        )
        # Ensure latest season includes newly added pokemon IDs if DB grew
        if sid == max(int(x["id"]) for x in raw_seasons) and max_pokemon_id > max(configured_allowed, default=0):
            configured_allowed = list(range(1, max_pokemon_id + 1))
        s_item: dict[str, Any] = {
            "seasonId": sid,
            "name": s["name"],
            "startDate": s.get("start_date") or "1970-01-01",
            "allowedPokemonIds": [int(x) for x in configured_allowed],
            "dataVersion": 1,
            "battleCount": season_battle_counts.get(sid, 0),
            "matchCount": season_match_counts.get(sid, 0),
            "dataChangedAt": ts,
        }
        if s.get("end_date"):
            s_item["endDate"] = str(s["end_date"])
        if s.get("description"):
            s_item["description"] = str(s["description"])
        seasons_items.append(s_item)

    # 7. Counters (uses MAX(id), never COUNT!)
    reset_requests_sidecar = list(sidecar.get("reset_requests") or [])
    max_reset_req_id = max(
        (int(r["requestId"]) for r in reset_requests_sidecar),
        default=0,
    )
    counters_items: list[dict[str, Any]] = [
        {"counterName": "playerId", "value": max((int(p["id"]) for p in raw_players), default=0)},
        {"counterName": "messageId", "value": max((int(m["id"]) for m in raw_messages), default=0)},
        {"counterName": "battleId", "value": max((int(b["id"]) for b in raw_battles), default=0)},
        {"counterName": "matchId", "value": max((int(m["match_id"]) for m in raw_matches), default=0)},
        {"counterName": "resetRequestId", "value": max_reset_req_id},
        {"counterName": "masterVersion", "value": 1},
    ]

    return {
        "Pokemon": pokemon_items,
        "Seasons": seasons_items,
        "Players": players_items,
        "DiscordAccounts": discord_accounts_items,
        "BattleMessages": battle_messages_items,
        "DiscordMessages": discord_messages_items,
        "BattleMatches": battle_matches_items,
        "Battles": battles_items,
        "Counters": counters_items,
        "ResetRequests": reset_requests_sidecar,
        "ResetHistory": list(sidecar.get("reset_history") or []),
        "BattleAuditLogs": list(sidecar.get("battle_audit_logs") or []),
    }


def _check_no_empty_or_none(value: Any, path: str, errors: list[str]) -> None:
    if value is None or value == "":
        errors.append(f"Empty or None attribute at {path}")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _check_no_empty_or_none(v, f"{path}.{k}", errors)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _check_no_empty_or_none(item, f"{path}[{idx}]", errors)


def validate_offline(items_by_table: dict[str, list[dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    for table_name, items in items_by_table.items():
        spec = EXPECTED_TABLES[table_name]
        pk_name, _ = spec["pk"]
        sk_name = spec["sk"][0] if spec["sk"] else None
        for idx, item in enumerate(items):
            _check_no_empty_or_none(item, f"{table_name}[{idx}]", errors)
            if pk_name not in item:
                errors.append(f"{table_name}[{idx}] missing PK {pk_name}")
            if sk_name and sk_name not in item:
                errors.append(f"{table_name}[{idx}] missing SK {sk_name}")
            serialized = serialize_item(item)
            encoded_bytes = len(json.dumps(serialized, ensure_ascii=False).encode("utf-8"))
            if encoded_bytes >= MAX_ITEM_BYTES:
                errors.append(f"{table_name}[{idx}] exceeds 400KB ({encoded_bytes} bytes)")

    player_ids = {p["playerId"] for p in items_by_table["Players"]}
    pokemon_ids = {p["pokemonId"] for p in items_by_table["Pokemon"]}
    message_ids = {m["messageId"] for m in items_by_table["BattleMessages"]}
    match_ids = {m["matchId"] for m in items_by_table["BattleMatches"]}

    for b in items_by_table["Battles"]:
        bid = b["battleId"]
        if b["messageId"] not in message_ids:
            errors.append(f"Battle#{bid} references missing messageId {b['messageId']}")
        if b["player1PokemonId"] not in pokemon_ids or b["player2PokemonId"] not in pokemon_ids:
            errors.append(f"Battle#{bid} references missing pokemonId")
        if "player1Id" in b and b["player1Id"] not in player_ids:
            errors.append(f"Battle#{bid} references missing player1Id")
        if "player2Id" in b and b["player2Id"] not in player_ids:
            errors.append(f"Battle#{bid} references missing player2Id")
        if "matchId" in b and b["matchId"] not in match_ids:
            errors.append(f"Battle#{bid} references missing matchId")

    for m in items_by_table["BattleMatches"]:
        mid = m["matchId"]
        if m["messageId"] not in message_ids:
            errors.append(f"BattleMatch#{mid} references missing messageId")
        if "playerId" in m and m["playerId"] not in player_ids:
            errors.append(f"BattleMatch#{mid} references missing playerId")
        calc_res, p_score, o_score = calculate_series_result(
            [g["player1Result"] for g in m["games"]]
        )
        if m.get("matchResult") != calc_res or m["playerScore"] != p_score or m["opponentScore"] != o_score:
            errors.append(f"BattleMatch#{mid} score/result mismatch")

    return errors


def _put_item_idempotent(
    dynamodb_client: Any,
    full_table_name: str,
    pk_name: str,
    sk_name: str | None,
    item: dict[str, Any],
) -> str:
    cond = f"attribute_not_exists({pk_name})"
    if sk_name:
        cond += f" AND attribute_not_exists({sk_name})"

    serialized = serialize_item(item)
    for attempt in range(5):
        try:
            dynamodb_client.put_item(
                TableName=full_table_name,
                Item=serialized,
                ConditionExpression=cond,
            )
            return "inserted"
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException" or exc.__class__.__name__ == "ConditionalCheckFailedException":
                key = {pk_name: serialized[pk_name]}
                if sk_name:
                    key[sk_name] = serialized[sk_name]
                existing_raw = dynamodb_client.get_item(
                    TableName=full_table_name,
                    Key=key,
                    ConsistentRead=True,
                ).get("Item")
                existing = deserialize_item(existing_raw)
                # Volatile timestamps on Seasons/Counters are allowed to differ across reruns
                ignore_keys = {"dataChangedAt"} if full_table_name.endswith("Seasons") else set()
                left = {k: v for k, v in (existing or {}).items() if k not in ignore_keys}
                right = {k: v for k, v in item.items() if k not in ignore_keys}
                if left == right:
                    return "skipped_identical"
                raise RuntimeError(
                    f"Conflict in {full_table_name} for key {item.get(pk_name)}: existing item differs from SQLite."
                ) from exc
            if attempt < 4:
                time.sleep(0.2 * (2**attempt))
                continue
            raise
    return "inserted"


def verify_against_dynamodb(
    dynamodb_client: Any,
    table_prefix: str,
    expected_by_table: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    errors: list[str] = []
    counts_summary: dict[str, dict[str, int]] = {}

    for table_name in (*REGISTRATION_ORDER, "Counters"):
        full_name = f"{table_prefix}{table_name}"
        spec = EXPECTED_TABLES[table_name]
        pk_name, _ = spec["pk"]
        sk_name = spec["sk"][0] if spec["sk"] else None

        actual_items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"TableName": full_name}
        while True:
            resp = dynamodb_client.scan(**kwargs)
            for raw in resp.get("Items", []):
                parsed = deserialize_item(raw)
                if parsed is not None:
                    actual_items.append(parsed)
            if not resp.get("LastEvaluatedKey"):
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

        if table_name == "Counters":
            actual_filtered = [x for x in actual_items if x["counterName"] != "migration"]
        else:
            actual_filtered = actual_items

        expected_items = expected_by_table[table_name]
        counts_summary[table_name] = {
            "expected": len(expected_items),
            "actual": len(actual_filtered),
        }
        if len(actual_filtered) != len(expected_items):
            errors.append(
                f"{table_name}: count mismatch (expected {len(expected_items)}, actual {len(actual_filtered)})"
            )
            continue

        def _key_fn(it: dict[str, Any]) -> Any:
            return (it[pk_name], it[sk_name]) if sk_name else it[pk_name]

        actual_map = {_key_fn(it): it for it in actual_filtered}
        expected_map = {_key_fn(it): it for it in expected_items}
        if set(actual_map.keys()) != set(expected_map.keys()):
            errors.append(f"{table_name}: primary key set mismatch")
            continue

        for k, exp_item in expected_map.items():
            act_item = actual_map[k]
            if table_name == "Counters":
                if int(act_item["value"]) < int(exp_item["value"]):
                    errors.append(f"Counters[{k}]: actual {act_item['value']} < expected {exp_item['value']}")
            else:
                ignore = {"dataChangedAt"} if table_name == "Seasons" else set()
                e_clean = { field: val for field, val in exp_item.items() if field not in ignore }
                a_clean = { field: val for field, val in act_item.items() if field not in ignore }
                if e_clean != a_clean:
                    errors.append(f"{table_name}[{k}]: attribute mismatch")

    return {"ok": len(errors) == 0, "counts": counts_summary, "errors": errors}


def execute_migration(
    db_path: Path,
    dynamodb_client: Any,
    table_prefix: str,
    *,
    sidecar: dict[str, Any] | None = None,
    force: bool = False,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    for logical_name in EXPECTED_TABLES:
        dynamodb_client.describe_table(TableName=f"{table_prefix}{logical_name}")

    marker_resp = dynamodb_client.get_item(
        TableName=f"{table_prefix}Counters",
        Key={"counterName": {"S": "migration"}},
        ConsistentRead=True,
    )
    if marker_resp.get("Item") and not force:
        raise RuntimeError(
            "完了マーカー (Counters.migration) が既に存在するため再実行を拒否しました。強制実行する場合は --force を指定してください。"
        )

    sha_before = sha256_file(db_path)
    bdir = backup_dir or db_path.parent
    bdir.mkdir(parents=True, exist_ok=True)
    ts_compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = bdir / f"{db_path.name}.backup-{ts_compact}"
    shutil.copy2(db_path, backup_path)
    if sha256_file(backup_path) != sha_before:
        raise RuntimeError("バックアップファイルのSHA-256が元DBと一致しません。")

    migrated_at = _now_iso()
    items_by_table = extract_and_transform(db_path, migrated_at=migrated_at, sidecar=sidecar)
    offline_errors = validate_offline(items_by_table)
    if offline_errors:
        raise RuntimeError(f"Offline validation failed: {offline_errors}")

    stats: dict[str, dict[str, int]] = {}
    write_tables = (
        *REGISTRATION_ORDER,
        "ResetRequests",
        "ResetHistory",
        "BattleAuditLogs",
        "Counters",
    )
    for table_name in write_tables:
        items = items_by_table.get(table_name) or []
        if not items:
            continue
        spec = EXPECTED_TABLES[table_name]
        pk_name, _ = spec["pk"]
        sk_name = spec["sk"][0] if spec["sk"] else None
        inserted = 0
        skipped = 0
        for item in items:
            outcome = _put_item_idempotent(
                dynamodb_client,
                f"{table_prefix}{table_name}",
                pk_name,
                sk_name,
                item,
            )
            if outcome == "inserted":
                inserted += 1
            else:
                skipped += 1
        stats[table_name] = {"inserted": inserted, "skipped": skipped}

    sha_after = sha256_file(db_path)
    if sha_after != sha_before:
        raise RuntimeError("移行中に移行元SQLiteファイルが変更されたことを検知しました。")

    verification = verify_against_dynamodb(dynamodb_client, table_prefix, items_by_table)
    if not verification["ok"]:
        raise RuntimeError(f"Post-migration verification failed: {verification['errors']}")

    marker_item = {
        "counterName": "migration",
        "value": 1,
        "status": "completed",
        "completedAt": _now_iso(),
        "sourceSha256": sha_before,
        "counts": {t: len(items_by_table[t]) for t in REGISTRATION_ORDER},
    }
    dynamodb_client.put_item(
        TableName=f"{table_prefix}Counters",
        Item=serialize_item(marker_item),
    )

    return {
        "ok": True,
        "mode": "execute",
        "source_sha256": sha_before,
        "backup_path": str(backup_path),
        "write_stats": stats,
        "verification": verification,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate SQLite pokemon.db to DynamoDB.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Transform & validate offline only (default).")
    group.add_argument("--execute", action="store_true", help="Write to DynamoDB and verify.")
    group.add_argument("--verify", action="store_true", help="Verify DynamoDB against SQLite without writing.")
    parser.add_argument("--db", type=Path, default=BASE_DIR / "data" / "pokemon.db")
    parser.add_argument("--prefix", default=os.environ.get("PLAKORO_DYNAMODB_TABLE_PREFIX", "plakoro_dev_"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-northeast-1"))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--sidecar", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    sidecar_data = (
        json.loads(args.sidecar.read_text(encoding="utf-8"))
        if args.sidecar is not None
        else None
    )

    if not args.execute and not args.verify:
        items_by_table = extract_and_transform(args.db, sidecar=sidecar_data)
        errors = validate_offline(items_by_table)
        summary = {
            "ok": len(errors) == 0,
            "mode": "dry-run",
            "source_sha256": sha256_file(args.db),
            "counts": {t: len(items_by_table[t]) for t in (*REGISTRATION_ORDER, "Counters")},
            "errors": errors,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if not errors else 1

    import boto3

    session = (
        boto3.Session(profile_name=args.profile, region_name=args.region)
        if args.profile
        else boto3.Session(region_name=args.region)
    )
    client = session.client("dynamodb")

    if args.verify:
        items_by_table = extract_and_transform(args.db, sidecar=sidecar_data)
        result = verify_against_dynamodb(client, args.prefix, items_by_table)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    report = execute_migration(
        args.db,
        client,
        args.prefix,
        sidecar=sidecar_data,
        force=args.force,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
