"""Read-only DynamoDB data integrity checker (手順8 §6-2 / 追加レビューB-2).

Checks all 9 invariants across the 13 DynamoDB tables without outputting PII.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.battle_matches import calculate_series_result  # noqa: E402
from app.dynamodb_serde import deserialize_item  # noqa: E402


def _scan_table(dynamodb_client: Any, table_name: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"TableName": table_name}
    while True:
        resp = dynamodb_client.scan(**kwargs)
        for raw in resp.get("Items", []):
            parsed = deserialize_item(raw)
            if parsed is not None:
                items.append(parsed)
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def check_dynamodb_integrity(dynamodb_client: Any, table_prefix: str) -> dict[str, Any]:
    players = _scan_table(dynamodb_client, f"{table_prefix}Players")
    discord_accounts = _scan_table(dynamodb_client, f"{table_prefix}DiscordAccounts")
    seasons = _scan_table(dynamodb_client, f"{table_prefix}Seasons")
    messages = _scan_table(dynamodb_client, f"{table_prefix}BattleMessages")
    discord_messages = _scan_table(dynamodb_client, f"{table_prefix}DiscordMessages")
    matches = _scan_table(dynamodb_client, f"{table_prefix}BattleMatches")
    battles = _scan_table(dynamodb_client, f"{table_prefix}Battles")
    counters = _scan_table(dynamodb_client, f"{table_prefix}Counters")
    reset_requests = _scan_table(dynamodb_client, f"{table_prefix}ResetRequests")

    errors: list[str] = []

    battles_by_id = {int(b["battleId"]): b for b in battles}
    matches_by_id = {int(m["matchId"]): m for m in matches}
    messages_by_id = {int(m["messageId"]): m for m in messages}
    players_by_id = {int(p["playerId"]): p for p in players}

    # 1 & 2 & 9: BattleMatches <-> Battles consistency, scores, and soft-delete propagation
    for m in matches:
        mid = int(m["matchId"])
        match_deleted = bool(m.get("isDeleted"))
        emb_games = sorted(m.get("games", []), key=lambda g: int(g["gameNumber"]))
        actual_games = sorted(
            (b for b in battles if b.get("matchId") is not None and int(b["matchId"]) == mid),
            key=lambda b: int(b.get("gameNumber") or 0),
        )

        if len(emb_games) != len(actual_games):
            errors.append(
                f"Match#{mid}: embedded games count ({len(emb_games)}) != Battles count ({len(actual_games)})"
            )

        for emb in emb_games:
            bid = int(emb["battleId"])
            b = battles_by_id.get(bid)
            if b is None:
                errors.append(f"Match#{mid}: embedded battleId {bid} missing in Battles")
                continue
            if b.get("matchId") != mid or b.get("gameNumber") != emb["gameNumber"]:
                errors.append(f"Match#{mid}: Battle#{bid} matchId/gameNumber mismatch")
            if match_deleted and not b.get("isDeleted"):
                errors.append(f"Match#{mid}: match is deleted but Battle#{bid} is not deleted")

        if not match_deleted:
            active_results = [
                b["player1Result"] for b in actual_games if not b.get("isDeleted")
            ]
            calc_res, p_score, o_score = calculate_series_result(active_results)
            if (
                m.get("matchResult") != calc_res
                or int(m["playerScore"]) != p_score
                or int(m["opponentScore"]) != o_score
            ):
                errors.append(f"Match#{mid}: stored result/score does not match recalculated Battles")

    # 3: BattleMessages.matchId <-> BattleMatches.messageId
    for m in matches:
        mid = int(m["matchId"])
        msg_id = int(m["messageId"])
        msg = messages_by_id.get(msg_id)
        if msg is None:
            errors.append(f"Match#{mid}: messageId {msg_id} missing in BattleMessages")
            continue
        if m.get("isDeleted"):
            if msg.get("matchId") == mid:
                errors.append(f"Match#{mid}: deleted match still linked on BattleMessages#{msg_id}")
        else:
            if msg.get("matchId") != mid:
                errors.append(f"Match#{mid}: BattleMessages#{msg_id}.matchId != {mid}")

    for msg in messages:
        msg_id = int(msg["messageId"])
        if msg.get("matchId") is not None:
            mid = int(msg["matchId"])
            m = matches_by_id.get(mid)
            if m is None or m.get("isDeleted") or int(m["messageId"]) != msg_id:
                errors.append(f"BattleMessages#{msg_id}: points to invalid/deleted Match#{mid}")

    # 4: lastBattleNumber >= max(battleNumber)
    max_bnum_by_msg: dict[int, int] = {}
    for b in battles:
        msg_id = int(b["messageId"])
        bnum = int(b["battleNumber"])
        if bnum > max_bnum_by_msg.get(msg_id, 0):
            max_bnum_by_msg[msg_id] = bnum
    for msg in messages:
        msg_id = int(msg["messageId"])
        last_num = int(msg.get("lastBattleNumber", 0))
        max_num = max_bnum_by_msg.get(msg_id, 0)
        if last_num < max_num:
            errors.append(
                f"BattleMessages#{msg_id}: lastBattleNumber ({last_num}) < max battleNumber ({max_num})"
            )

    # 5: Counters >= max IDs
    counter_map = {c["counterName"]: int(c["value"]) for c in counters if "value" in c}
    expected_maxes = {
        "playerId": max(players_by_id.keys(), default=0),
        "messageId": max(messages_by_id.keys(), default=0),
        "battleId": max(battles_by_id.keys(), default=0),
        "matchId": max(matches_by_id.keys(), default=0),
        "resetRequestId": max((int(r["requestId"]) for r in reset_requests), default=0),
    }
    for cname, min_expected in expected_maxes.items():
        actual_val = counter_map.get(cname, -1)
        if actual_val < min_expected:
            errors.append(f"Counters[{cname}] ({actual_val}) < max ID ({min_expected})")

    # 6: Seasons.battleCount & matchCount match total items (including deleted)
    total_battles_by_season: dict[int, int] = {}
    for b in battles:
        sid = int(b["seasonId"])
        total_battles_by_season[sid] = total_battles_by_season.get(sid, 0) + 1
    total_matches_by_season: dict[int, int] = {}
    for m in matches:
        sid = int(m["seasonId"])
        total_matches_by_season[sid] = total_matches_by_season.get(sid, 0) + 1
    for s in seasons:
        sid = int(s["seasonId"])
        if "battleCount" in s and int(s["battleCount"]) != total_battles_by_season.get(sid, 0):
            errors.append(
                f"Season#{sid}: battleCount ({s['battleCount']}) != actual ({total_battles_by_season.get(sid, 0)})"
            )
        if "matchCount" in s and int(s["matchCount"]) != total_matches_by_season.get(sid, 0):
            errors.append(
                f"Season#{sid}: matchCount ({s['matchCount']}) != actual ({total_matches_by_season.get(sid, 0)})"
            )

    # 7: DiscordMessages <-> BattleMessages 1:1
    dm_pairs = {(d["discordMessageId"], int(d["messageId"])) for d in discord_messages}
    bm_pairs = {(m["discordMessageId"], int(m["messageId"])) for m in messages}
    if dm_pairs != bm_pairs or len(discord_messages) != len(messages):
        errors.append("DiscordMessages and BattleMessages are not 1-to-1")

    # 8: DiscordAccounts <-> Players (N:1 allowed; every player has >= 1 account + primary ID)
    accounts_by_player: dict[int, set[str]] = {}
    for acc in discord_accounts:
        pid = int(acc["playerId"])
        if pid not in players_by_id:
            errors.append(f"DiscordAccounts references missing playerId {pid}")
            continue
        accounts_by_player.setdefault(pid, set()).add(acc["discordUserId"])

    for pid, p in players_by_id.items():
        linked = accounts_by_player.get(pid, set())
        if not linked:
            errors.append(f"Player#{pid} has 0 linked DiscordAccounts")
        elif p.get("discordUserId") and p["discordUserId"] not in linked:
            errors.append(f"Player#{pid} primary discordUserId is missing from DiscordAccounts")

    return {
        "ok": len(errors) == 0,
        "counts": {
            "Players": len(players),
            "DiscordAccounts": len(discord_accounts),
            "Seasons": len(seasons),
            "BattleMessages": len(messages),
            "Battles": len(battles),
            "BattleMatches": len(matches),
        },
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check DynamoDB data integrity.")
    parser.add_argument("--prefix", default=os.environ.get("PLAKORO_DYNAMODB_TABLE_PREFIX", "plakoro_dev_"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-northeast-1"))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    args = parser.parse_args(argv)

    import boto3

    session = (
        boto3.Session(profile_name=args.profile, region_name=args.region)
        if args.profile
        else boto3.Session(region_name=args.region)
    )
    client = session.client("dynamodb")
    result = check_dynamodb_integrity(client, args.prefix)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
