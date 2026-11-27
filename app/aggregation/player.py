"""Player list (P1) and player detail (P2) aggregations (手順6 §5)."""

from __future__ import annotations

from typing import Any


def _extract_player_side(battle: dict[str, Any], player_id: int) -> dict[str, Any]:
    if battle["player1"]["id"] == player_id:
        self_side = battle["player1"]
        opp_side = battle["player2"]
        fp = battle.get("first_player")
        order_bucket = "first" if fp == 1 else ("second" if fp == 2 else "unknown")
    else:
        self_side = battle["player2"]
        opp_side = battle["player1"]
        fp = battle.get("first_player")
        order_bucket = "first" if fp == 2 else ("second" if fp == 1 else "unknown")
    return {
        "battle": battle,
        "self": self_side,
        "opponent": opp_side,
        "order_bucket": order_bucket,
    }


def compute_player_list(
    players: list[dict[str, Any]],
    formatted_battles: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Compute P1 player list with each player's top-used pokemon (counts only; JS sorts names)."""
    usage_by_player: dict[int, dict[int, dict[str, Any]]] = {}
    for battle in formatted_battles:
        for side_key in ("player1", "player2"):
            side = battle[side_key]
            pid = side.get("id")
            if pid is None:
                continue
            pid_int = int(pid)
            pmap = usage_by_player.setdefault(pid_int, {})
            poke_id = int(side["pokemon_id"])
            if poke_id not in pmap:
                pmap[poke_id] = {
                    "pokemon_id": poke_id,
                    "name": side["pokemon"],
                    "count": 0,
                }
            pmap[poke_id]["count"] += 1

    output = []
    for player in players:
        pid = int(player["id"])
        pmap = usage_by_player.get(pid, {})
        top_pokemon = (
            sorted(pmap.values(), key=lambda x: x["count"], reverse=True)[0]
            if pmap
            else None
        )
        output.append(
            {
                "id": pid,
                "name": player["name"],
                "top_pokemon": top_pokemon,
            }
        )
    return {"players": output}


def compute_player_detail(
    player: dict[str, Any],
    formatted_battles: list[dict[str, Any]],
    *,
    battle_type: str = "all",
    reset_after_battle_id: int | None = None,
) -> dict[str, Any]:
    """Compute P2 player detail (手順6 §5).

    Applies reset_after_battle_id (> boundary), battle_type filter, and separates
    unknown turn order into ``order.unknown`` (Q2).
    """
    pid = int(player["id"])
    filtered: list[dict[str, Any]] = []
    for battle in formatted_battles:
        if reset_after_battle_id is not None and int(battle["id"]) <= int(reset_after_battle_id):
            continue
        if battle["player1"]["id"] != pid and battle["player2"]["id"] != pid:
            continue
        btype = battle.get("battle_type") or "normal"
        if battle_type != "all" and btype != battle_type:
            continue
        filtered.append(battle)

    records = [_extract_player_side(b, pid) for b in filtered]
    total_battles = len(records)
    total_wins = sum(1 for r in records if r["self"]["result"] == "win")

    # Preserve insertion order for tie-breaking (matches JS Map + Array.prototype.sort)
    usage_map: dict[int, dict[str, Any]] = {}
    top_map: dict[int, dict[str, Any]] = {}
    for rec in records:
        s = rec["self"]
        poke_id = int(s["pokemon_id"])
        if poke_id not in top_map:
            top_map[poke_id] = {
                "pokemon_id": poke_id,
                "name": s["pokemon"],
                "count": 0,
            }
        top_map[poke_id]["count"] += 1

        if poke_id not in usage_map:
            usage_map[poke_id] = {
                "pokemon_id": poke_id,
                "name": s["pokemon"],
                "type1": s.get("type1"),
                "type2": s.get("type2"),
                "battles": 0,
                "wins": 0,
            }
        usage_map[poke_id]["battles"] += 1
        if s["result"] == "win":
            usage_map[poke_id]["wins"] += 1

    top_pokemon = (
        sorted(top_map.values(), key=lambda x: x["count"], reverse=True)[0]
        if top_map
        else None
    )
    pokemon_usage = sorted(usage_map.values(), key=lambda x: x["battles"], reverse=True)

    by_id_desc = sorted(records, key=lambda r: int(r["battle"]["id"]), reverse=True)
    recent_10 = by_id_desc[:10]
    recent_10_wins = sum(1 for r in recent_10 if r["self"]["result"] == "win")

    order_stats = {
        "first": {"battles": 0, "wins": 0},
        "second": {"battles": 0, "wins": 0},
        "unknown": {"battles": 0, "wins": 0},
    }
    for rec in records:
        bucket = order_stats[rec["order_bucket"]]
        bucket["battles"] += 1
        if rec["self"]["result"] == "win":
            bucket["wins"] += 1

    recent_battles = [r["battle"] for r in by_id_desc[:8]]

    return {
        "player": {"id": pid, "name": player["name"]},
        "summary": {
            "battles": total_battles,
            "wins": total_wins,
            "top_pokemon": top_pokemon,
        },
        "recent": {
            "battles": len(recent_10),
            "wins": recent_10_wins,
        },
        "order": order_stats,
        "pokemon_usage": pokemon_usage,
        "recent_battles": recent_battles,
    }
