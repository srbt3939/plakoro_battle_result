"""List builders: battles, battle_matches, battle_trend, players, recent_feed (手順6 §3・§4)."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..battle_matches import calculate_series_result
from .cells import is_battle_deleted
from .stats import parse_weaknesses

JST = timezone(timedelta(hours=9))


def today_jst() -> date:
    return datetime.now(JST).date()


def _ascii_lower(text: str) -> str:
    """Reproduce SQLite's built-in COLLATE NOCASE (ASCII A-Z folding only)."""
    return "".join(
        chr(ord(ch) + 32) if "A" <= ch <= "Z" else ch
        for ch in text
    )


def build_battles_json(
    battles: list[dict],
    matches_by_id: dict[int, dict],
    pokemon_by_id: dict[int, dict],
    players_by_id: dict[int, dict],
    allowed_pokemon_ids: set[int] | tuple[int, ...] | list[int],
) -> dict[str, list[dict[str, Any]]]:
    allowed = set(int(x) for x in allowed_pokemon_ids)
    filtered = [
        b
        for b in battles
        if not is_battle_deleted(b)
        and int(b["player1_pokemon_id"]) in allowed
        and int(b["player2_pokemon_id"]) in allowed
        and int(b["player1_pokemon_id"]) in pokemon_by_id
        and int(b["player2_pokemon_id"]) in pokemon_by_id
    ]
    filtered.sort(
        key=lambda b: (
            int(b["battle_message_id"]),
            int(b["battle_number"]),
            int(b["id"]),
        )
    )

    output: list[dict[str, Any]] = []
    for row in filtered:
        p1_id = row.get("player1_id")
        p2_id = row.get("player2_id")
        p1_pid = int(row["player1_pokemon_id"])
        p2_pid = int(row["player2_pokemon_id"])
        p1_poke = pokemon_by_id[p1_pid]
        p2_poke = pokemon_by_id[p2_pid]
        match_id = row.get("match_id")
        match_row = matches_by_id.get(int(match_id)) if match_id is not None else None

        p1_name = players_by_id[int(p1_id)]["name"] if p1_id is not None and int(p1_id) in players_by_id else None
        p2_name = (
            players_by_id[int(p2_id)]["name"]
            if p2_id is not None and int(p2_id) in players_by_id
            else (match_row.get("opponent") if match_row is not None else None)
        )

        entry: dict[str, Any] = {
            "id": int(row["id"]),
            "battle_message_id": int(row["battle_message_id"]),
            "battle_number": int(row["battle_number"]),
            "player1": {
                "id": int(p1_id) if p1_id is not None else None,
                "name": p1_name,
                "pokemon_id": p1_pid,
                "pokemon": p1_poke["name"],
                "type1": p1_poke.get("type1"),
                "type2": p1_poke.get("type2"),
                "weaknesses": parse_weaknesses(p1_poke.get("weaknesses")),
                "result": row["player1_result"],
            },
            "player2": {
                "id": int(p2_id) if p2_id is not None else None,
                "name": p2_name,
                "pokemon_id": p2_pid,
                "pokemon": p2_poke["name"],
                "type1": p2_poke.get("type1"),
                "type2": p2_poke.get("type2"),
                "weaknesses": parse_weaknesses(p2_poke.get("weaknesses")),
                "result": row["player2_result"],
            },
            "first_player": int(row["first_player"]) if row.get("first_player") is not None else None,
            "battle_type": row.get("battle_type") or "normal",
            "season_id": int(row["season_id"]),
        }
        if match_id is not None and match_row is not None:
            entry.update(
                {
                    "match_id": int(match_id),
                    "game_number": int(row["game_number"]) if row.get("game_number") is not None else None,
                    "tournament_name": match_row.get("tournament_name"),
                    "match_opponent": match_row.get("opponent"),
                    "match_result": match_row.get("match_result"),
                    "player_score": int(match_row["player_score"]),
                    "opponent_score": int(match_row["opponent_score"]),
                }
            )
        output.append(entry)

    return {"battles": output}


def build_battle_matches_json(
    matches: list[dict],
    battles: list[dict],
    pokemon_by_id: dict[int, dict],
    players_by_id: dict[int, dict],
) -> dict[str, list[dict[str, Any]]]:
    active_matches = [m for m in matches if not is_battle_deleted(m)]
    active_matches.sort(
        key=lambda m: (m.get("match_date") or "", int(m["match_id"])),
        reverse=True,
    )
    match_map = {int(m["match_id"]): m for m in active_matches}

    games_by_match: dict[int, list[dict[str, Any]]] = {}
    game_rows = [
        b
        for b in battles
        if not is_battle_deleted(b)
        and b.get("match_id") is not None
        and int(b["match_id"]) in match_map
        and int(b["player1_pokemon_id"]) in pokemon_by_id
        and int(b["player2_pokemon_id"]) in pokemon_by_id
    ]
    game_rows.sort(key=lambda b: (int(b["match_id"]), int(b.get("game_number") or 0), int(b["id"])))

    for row in game_rows:
        mid = int(row["match_id"])
        mrow = match_map[mid]
        p1_id = row.get("player1_id")
        p2_id = row.get("player2_id")
        p1_pid = int(row["player1_pokemon_id"])
        p2_pid = int(row["player2_pokemon_id"])
        p1_poke = pokemon_by_id[p1_pid]
        p2_poke = pokemon_by_id[p2_pid]
        p1_name = players_by_id[int(p1_id)]["name"] if p1_id is not None and int(p1_id) in players_by_id else None
        p2_name = (
            players_by_id[int(p2_id)]["name"]
            if p2_id is not None and int(p2_id) in players_by_id
            else mrow.get("opponent")
        )
        games_by_match.setdefault(mid, []).append(
            {
                "id": int(row["id"]),
                "battle_message_id": int(row["battle_message_id"]),
                "battle_number": int(row["battle_number"]),
                "game_number": int(row["game_number"]),
                "battle_type": "challenge",
                "match_id": mid,
                "season_id": int(mrow["season_id"]),
                "player1": {
                    "id": int(p1_id) if p1_id is not None else None,
                    "name": p1_name,
                    "pokemon_id": p1_pid,
                    "pokemon": p1_poke["name"],
                    "type1": p1_poke.get("type1"),
                    "type2": p1_poke.get("type2"),
                    "weaknesses": parse_weaknesses(p1_poke.get("weaknesses")),
                    "result": row["player1_result"],
                },
                "player2": {
                    "id": int(p2_id) if p2_id is not None else None,
                    "name": p2_name,
                    "pokemon_id": p2_pid,
                    "pokemon": p2_poke["name"],
                    "type1": p2_poke.get("type1"),
                    "type2": p2_poke.get("type2"),
                    "weaknesses": parse_weaknesses(p2_poke.get("weaknesses")),
                    "result": row["player2_result"],
                },
                "first_player": int(row["first_player"]) if row.get("first_player") is not None else None,
            }
        )

    output_matches: list[dict[str, Any]] = []
    for mrow in active_matches:
        mid = int(mrow["match_id"])
        games = games_by_match.get(mid, [])
        result, player_score, opponent_score = calculate_series_result(
            [g["player1"]["result"] for g in games]
        )
        pid = mrow.get("player_id")
        pname = players_by_id[int(pid)]["name"] if pid is not None and int(pid) in players_by_id else None
        output_matches.append(
            {
                "match_id": mid,
                "match_type": mrow["match_type"],
                "tournament_name": mrow.get("tournament_name"),
                "opponent": mrow.get("opponent"),
                "match_date": mrow.get("match_date"),
                "match_result": result,
                "player_score": player_score,
                "opponent_score": opponent_score,
                "season_id": int(mrow["season_id"]),
                "player": {
                    "id": int(pid) if pid is not None else None,
                    "name": pname,
                },
                "battle_message_id": int(mrow["battle_message_id"]),
                "games": games,
            }
        )

    return {"matches": output_matches}


def build_battle_trend_json(
    battles: list[dict],
    allowed_pokemon_ids: set[int] | tuple[int, ...] | list[int],
    today: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    allowed = set(int(x) for x in allowed_pokemon_ids)
    ref_today = today or today_jst()
    start_date = ref_today - timedelta(days=13)
    start_iso = start_date.isoformat()
    end_iso = ref_today.isoformat()

    counts: Counter[str] = Counter()
    for row in battles:
        if is_battle_deleted(row):
            continue
        if int(row["player1_pokemon_id"]) not in allowed or int(row["player2_pokemon_id"]) not in allowed:
            continue
        bdate = (row.get("battle_date") or "").strip()
        if not bdate:
            continue
        if start_iso <= bdate <= end_iso:
            counts[bdate] += 1

    days = [
        {
            "date": (start_date + timedelta(days=offset)).isoformat(),
            "battle_count": counts.get((start_date + timedelta(days=offset)).isoformat(), 0),
        }
        for offset in range(14)
    ]
    return {"days": days}


def build_players_json(players: list[dict]) -> dict[str, list[dict[str, Any]]]:
    sorted_players = sorted(players, key=lambda p: (_ascii_lower(p["name"]), int(p["id"])))
    return {
        "players": [
            {"id": int(p["id"]), "name": p["name"]}
            for p in sorted_players
        ]
    }


def build_recent_feed(
    formatted_battles: list[dict[str, Any]],
    formatted_matches: list[dict[str, Any]],
    *,
    battle_filter: str = "all",
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Merge recent battles and BO3 matches in recent_battles.js order (G4)."""
    normal_entries = [
        {
            "kind": "normal",
            "sort_id": int(battle["battle_message_id"]),
            "item_id": int(battle["battle_number"]),
            "battle": battle,
        }
        for battle in formatted_battles
        if battle.get("match_id") is None
    ]
    challenge_entries = [
        {
            "kind": "challenge",
            "sort_id": int(match["battle_message_id"]),
            "item_id": int(match["match_id"]),
            "match": match,
        }
        for match in formatted_matches
    ]
    if battle_filter == "normal":
        entries = normal_entries
    elif battle_filter == "challenge":
        entries = challenge_entries
    else:
        entries = [*normal_entries, *challenge_entries]

    entries.sort(key=lambda e: (e["sort_id"], e["item_id"]), reverse=True)
    return entries[:limit]
