"""Character detail (G5) aggregation (手順6 §5)."""

from __future__ import annotations

from typing import Any


def _extract_character_side(battle: dict[str, Any], character_id: int) -> dict[str, Any]:
    """Match character.js getCharacterSide() (checks player1 first even in mirrors)."""
    fp = battle.get("first_player")
    if battle["player1"]["pokemon_id"] == character_id:
        order_bucket = "first" if fp == 1 else ("second" if fp == 2 else "unknown")
        return {
            "battle": battle,
            "player": battle["player1"],
            "opponent": battle["player2"],
            "order_bucket": order_bucket,
        }
    order_bucket = "first" if fp == 2 else ("second" if fp == 1 else "unknown")
    return {
        "battle": battle,
        "player": battle["player2"],
        "opponent": battle["player1"],
        "order_bucket": order_bucket,
    }


def compute_character_detail(
    pokemon: dict[str, Any],
    formatted_battles: list[dict[str, Any]],
    matchups: list[dict[str, Any]],
    usage_pokemon: list[dict[str, Any]],
    players_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    cid = int(pokemon["id"])
    char_battles = [
        b
        for b in formatted_battles
        if b["player1"]["pokemon_id"] == cid or b["player2"]["pokemon_id"] == cid
    ]
    records = [_extract_character_side(b, cid) for b in char_battles]

    wins = sum(1 for r in records if r["player"]["result"] == "win")
    usage_entry = next((u for u in usage_pokemon if int(u["pokemon_id"]) == cid), None)
    usage_count = int(usage_entry["usage_count"]) if usage_entry else 0

    char_matchups = [
        m
        for m in matchups
        if int(m["pokemon1"]["id"]) == cid or int(m["pokemon2"]["id"]) == cid
    ]
    char_matchups_sorted = sorted(char_matchups, key=lambda m: m["battles"], reverse=True)
    top_matchup = char_matchups_sorted[0] if char_matchups_sorted else None
    top_opponent = None
    if top_matchup is not None:
        opp = (
            top_matchup["pokemon2"]
            if int(top_matchup["pokemon1"]["id"]) == cid
            else top_matchup["pokemon1"]
        )
        top_opponent = {
            "id": opp["id"],
            "name": opp["name"],
            "battles": top_matchup["battles"],
        }

    order_stats = {
        "first": {"battles": 0, "wins": 0},
        "second": {"battles": 0, "wins": 0},
        "unknown": {"battles": 0, "wins": 0},
    }
    player_counts: dict[int, int] = {}
    for rec in records:
        bucket = order_stats[rec["order_bucket"]]
        bucket["battles"] += 1
        if rec["player"]["result"] == "win":
            bucket["wins"] += 1

        pid = rec["player"].get("id")
        if pid is not None and int(pid) in players_by_id:
            pid_int = int(pid)
            player_counts[pid_int] = player_counts.get(pid_int, 0) + 1

    player_usage = [
        {
            "id": pid,
            "name": players_by_id[pid]["name"],
            "count": count,
        }
        for pid, count in player_counts.items()
    ]

    recent_battles = sorted(char_battles, key=lambda b: int(b["id"]), reverse=True)[:8]

    return {
        "pokemon": {
            "id": cid,
            "name": pokemon["name"],
            "type1": pokemon.get("type1"),
            "type2": pokemon.get("type2"),
        },
        "usage_count": usage_count,
        "summary": {
            "battles": len(records),
            "wins": wins,
            "top_opponent": top_opponent,
        },
        "order": order_stats,
        "player_usage": player_usage,
        "matchups": char_matchups_sorted,
        "recent_battles": recent_battles,
    }
