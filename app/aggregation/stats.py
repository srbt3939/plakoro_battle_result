"""Aggregate usage, pokemon_stats, matchups, and battle_challenge_stats (手順6 §3・§4)."""

from __future__ import annotations

from collections import Counter
import json
from typing import Any

from .cells import CellKey, is_battle_deleted


def parse_weaknesses(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [str(value)]
    return parsed if isinstance(parsed, list) else [str(parsed)]


def pokemon_details(pokemon: dict) -> dict[str, Any]:
    return {
        "type1": pokemon.get("type1"),
        "type2": pokemon.get("type2"),
        "weaknesses": parse_weaknesses(pokemon.get("weaknesses")),
    }


def _win_rate(wins: int, battles: int) -> float | int:
    if battles == 0:
        return 0
    return round(wins / battles * 100, 1)


def compute_usage(
    cells: dict[CellKey, int],
    pokemon_by_id: dict[int, dict],
    allowed_pokemon_ids: set[int] | tuple[int, ...] | list[int],
) -> dict[str, Any]:
    allowed = set(int(x) for x in allowed_pokemon_ids)
    usage_counts: Counter[int] = Counter()
    for (p1_pid, p2_pid, _fp, _r1, _r2, _is_ch), count in cells.items():
        if p1_pid in allowed and p2_pid in allowed:
            if p1_pid in pokemon_by_id:
                usage_counts[p1_pid] += count
            if p2_pid in pokemon_by_id:
                usage_counts[p2_pid] += count

    sorted_items = sorted(usage_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    total_usage = sum(count for _, count in sorted_items)

    pokemon_list = []
    for rank, (pid, usage_count) in enumerate(sorted_items, start=1):
        pinfo = pokemon_by_id[pid]
        rate = round(usage_count / total_usage * 100, 1) if total_usage > 0 else 0
        pokemon_list.append(
            {
                "rank": rank,
                "pokemon_id": pid,
                "name": pinfo["name"],
                **pokemon_details(pinfo),
                "usage_count": usage_count,
                "usage_rate": rate,
            }
        )

    return {
        "total_usage": total_usage,
        "pokemon": pokemon_list,
    }


def compute_first_player_summary(cells: dict[CellKey, int]) -> dict[str, dict[str, int]]:
    """Separate first/second win counts across all, mirror-only, and non-mirror games (手順6 §3-4)."""
    buckets = {
        "all": {"games": 0, "first_wins": 0, "second_wins": 0, "unknown": 0},
        "mirror": {"games": 0, "first_wins": 0, "second_wins": 0, "unknown": 0},
        "non_mirror": {"games": 0, "first_wins": 0, "second_wins": 0, "unknown": 0},
    }
    for (p1_pid, p2_pid, fp, r1, r2, _is_ch), count in cells.items():
        target_keys = ["all", "mirror" if p1_pid == p2_pid else "non_mirror"]
        for bkey in target_keys:
            b = buckets[bkey]
            b["games"] += count
            if fp == 1:
                if r1 == "win":
                    b["first_wins"] += count
                elif r2 == "win":
                    b["second_wins"] += count
            elif fp == 2:
                if r2 == "win":
                    b["first_wins"] += count
                elif r1 == "win":
                    b["second_wins"] += count
            else:
                b["unknown"] += count
    return buckets


def compute_pokemon_stats(
    cells: dict[CellKey, int],
    pokemon_by_id: dict[int, dict],
    allowed_pokemon_ids: set[int] | tuple[int, ...] | list[int],
    *,
    exclude_mirror: bool = False,
    include_mirror_field: bool = False,
) -> dict[str, Any]:
    allowed = set(int(x) for x in allowed_pokemon_ids)
    appearing_ids: set[int] = set()
    for (p1_pid, p2_pid, _fp, _r1, _r2, _is_ch), count in cells.items():
        if count > 0 and p1_pid in allowed and p2_pid in allowed:
            appearing_ids.add(p1_pid)
            appearing_ids.add(p2_pid)

    stats_list = []
    for pid in sorted(pid for pid in appearing_ids if pid in pokemon_by_id):
        total_battles = 0
        total_wins = 0
        first_battles = 0
        first_wins = 0
        second_battles = 0
        second_wins = 0
        unknown_battles = 0
        unknown_wins = 0

        mirror_battles = 0
        mirror_first_wins = 0
        mirror_second_wins = 0
        mirror_unknown = 0

        for (p1_pid, p2_pid, fp, r1, r2, _is_ch), count in cells.items():
            if p1_pid not in allowed or p2_pid not in allowed:
                continue
            if p1_pid != pid and p2_pid != pid:
                continue

            is_mirror = p1_pid == p2_pid == pid
            if is_mirror:
                mirror_battles += count
                if fp == 1:
                    if r1 == "win":
                        mirror_first_wins += count
                    else:
                        mirror_second_wins += count
                elif fp == 2:
                    if r2 == "win":
                        mirror_first_wins += count
                    else:
                        mirror_second_wins += count
                else:
                    mirror_unknown += count

                if exclude_mirror:
                    continue

            # Reproduces exact SQL semantics of scripts/output_result.py (Q1)
            total_battles += count
            if (p1_pid == pid and r1 == "win") or (p2_pid == pid and r2 == "win"):
                total_wins += count

            if (fp == 1 and p1_pid == pid) or (fp == 2 and p2_pid == pid):
                first_battles += count
                if (fp == 1 and p1_pid == pid and r1 == "win") or (
                    fp == 2 and p2_pid == pid and r2 == "win"
                ):
                    first_wins += count

            if (fp == 1 and p2_pid == pid) or (fp == 2 and p1_pid == pid):
                second_battles += count
                if (fp == 1 and p2_pid == pid and r2 == "win") or (
                    fp == 2 and p1_pid == pid and r1 == "win"
                ):
                    second_wins += count

            if fp is None:
                unknown_battles += count
                if (p1_pid == pid and r1 == "win") or (p2_pid == pid and r2 == "win"):
                    unknown_wins += count

        pinfo = pokemon_by_id[pid]
        entry: dict[str, Any] = {
            "pokemon_id": pid,
            "name": pinfo["name"],
            **pokemon_details(pinfo),
            "total": {
                "battles": total_battles,
                "wins": total_wins,
                "losses": total_battles - total_wins,
                "win_rate": _win_rate(total_wins, total_battles),
            },
            "first_player": {
                "battles": first_battles,
                "wins": first_wins,
                "losses": first_battles - first_wins,
                "win_rate": _win_rate(first_wins, first_battles),
            },
            "second_player": {
                "battles": second_battles,
                "wins": second_wins,
                "losses": second_battles - second_wins,
                "win_rate": _win_rate(second_wins, second_battles),
            },
            "unknown": {
                "battles": unknown_battles,
                "wins": unknown_wins,
                "losses": unknown_battles - unknown_wins,
                "win_rate": _win_rate(unknown_wins, unknown_battles),
            },
        }
        if include_mirror_field:
            entry["mirror"] = {
                "battles": mirror_battles,
                "first_wins": mirror_first_wins,
                "second_wins": mirror_second_wins,
                "unknown": mirror_unknown,
            }
        stats_list.append(entry)

    result: dict[str, Any] = {"pokemon": stats_list}
    if include_mirror_field:
        result["first_player_summary"] = compute_first_player_summary(cells)
    return result


def compute_matchups(
    battles: list[dict],
    pokemon_by_id: dict[int, dict],
    allowed_pokemon_ids: set[int] | tuple[int, ...] | list[int],
    *,
    exclude_mirror: bool = False,
) -> dict[str, Any]:
    """Compute matchups with deterministic tie-breaking by ascending battle id (Q3)."""
    allowed = set(int(x) for x in allowed_pokemon_ids)
    ordered_battles = sorted(
        (b for b in battles if not is_battle_deleted(b)),
        key=lambda b: int(b["id"]),
    )
    matchups: dict[tuple[int, int], dict[str, Any]] = {}

    for row in ordered_battles:
        p1_id = int(row["player1_pokemon_id"])
        p2_id = int(row["player2_pokemon_id"])
        if p1_id not in allowed or p2_id not in allowed:
            continue
        if p1_id not in pokemon_by_id or p2_id not in pokemon_by_id:
            continue
        if exclude_mirror and p1_id == p2_id:
            continue

        p1_info = pokemon_by_id[p1_id]
        p2_info = pokemon_by_id[p2_id]

        # Matches exact `<` (not `<=`) branch in scripts/output_result.py line 1064
        if p1_id < p2_id:
            pokemon1_id = p1_id
            pokemon1_info = p1_info
            pokemon2_id = p2_id
            pokemon2_info = p2_info
            pokemon1_result = row["player1_result"]
            pokemon2_result = row["player2_result"]
        else:
            pokemon1_id = p2_id
            pokemon1_info = p2_info
            pokemon2_id = p1_id
            pokemon2_info = p1_info
            pokemon1_result = row["player2_result"]
            pokemon2_result = row["player1_result"]

        key = (pokemon1_id, pokemon2_id)
        if key not in matchups:
            matchups[key] = {
                "pokemon1": {
                    "id": pokemon1_id,
                    "name": pokemon1_info["name"],
                    **pokemon_details(pokemon1_info),
                },
                "pokemon2": {
                    "id": pokemon2_id,
                    "name": pokemon2_info["name"],
                    **pokemon_details(pokemon2_info),
                },
                "battles": 0,
                "pokemon1_wins": 0,
                "pokemon2_wins": 0,
            }

        matchups[key]["battles"] += 1
        if pokemon1_result == "win":
            matchups[key]["pokemon1_wins"] += 1
        elif pokemon2_result == "win":
            matchups[key]["pokemon2_wins"] += 1

    result = []
    for matchup in matchups.values():
        b_count = matchup["battles"]
        p1_wins = matchup["pokemon1_wins"]
        p2_wins = matchup["pokemon2_wins"]
        result.append(
            {
                "pokemon1": matchup["pokemon1"],
                "pokemon2": matchup["pokemon2"],
                "battles": b_count,
                "pokemon1_wins": p1_wins,
                "pokemon2_wins": p2_wins,
                "pokemon1_win_rate": round(p1_wins / b_count * 100, 1),
                "pokemon2_win_rate": round(p2_wins / b_count * 100, 1),
            }
        )

    result.sort(key=lambda x: x["battles"], reverse=True)
    return {"matchups": result}


def compute_battle_challenge_stats(
    cells: dict[CellKey, int],
    formatted_matches: list[dict],
    pokemon_by_id: dict[int, dict],
    allowed_pokemon_ids: set[int] | tuple[int, ...] | list[int],
) -> dict[str, Any]:
    allowed = set(int(x) for x in allowed_pokemon_ids)
    scope_cells: dict[str, list[tuple[CellKey, int]]] = {
        "normal": [],
        "battle_challenge": [],
        "all": [],
    }
    for key, count in cells.items():
        p1_pid, p2_pid, _fp, _r1, _r2, is_ch = key
        if p1_pid not in allowed or p2_pid not in allowed:
            continue
        scope_cells["all"].append((key, count))
        if is_ch:
            scope_cells["battle_challenge"].append((key, count))
        else:
            scope_cells["normal"].append((key, count))

    def game_totals(entries: list[tuple[CellKey, int]]) -> dict[str, int]:
        games = sum(c for _, c in entries)
        wins = sum(c for ((_, _, _, r1, _, _), c) in entries if r1 == "win")
        losses = sum(c for ((_, _, _, r1, _, _), c) in entries if r1 == "lose")
        return {"games": games, "wins": wins, "losses": losses}

    pokemon_scopes: dict[str, list[dict[str, Any]]] = {}
    for scope_name, entries in scope_cells.items():
        by_pokemon: dict[int, dict[str, int]] = {}
        for (p1_pid, p2_pid, _fp, r1, r2, _is_ch), count in entries:
            for pid, res in ((p1_pid, r1), (p2_pid, r2)):
                st = by_pokemon.setdefault(
                    pid, {"games": 0, "wins": 0, "losses": 0, "usage_count": 0}
                )
                st["games"] += count
                st["usage_count"] += count
                if res == "win":
                    st["wins"] += count
                elif res == "lose":
                    st["losses"] += count
        total_usage = sum(st["usage_count"] for st in by_pokemon.values())
        pokemon_scopes[scope_name] = [
            {
                "pokemon_id": pid,
                "name": pokemon_by_id[pid]["name"],
                **st,
                "usage_rate": round(st["usage_count"] / total_usage * 100, 1)
                if total_usage
                else 0,
                "win_rate": round(st["wins"] / st["games"] * 100, 1)
                if st["games"]
                else 0,
            }
            for pid, st in sorted(by_pokemon.items())
            if pid in pokemon_by_id
        ]

    series_wins = sum(1 for m in formatted_matches if m.get("match_result") == "win")
    series_losses = sum(1 for m in formatted_matches if m.get("match_result") == "lose")
    decided_series = series_wins + series_losses
    all_game_stats = game_totals(scope_cells["battle_challenge"])
    game_count = all_game_stats["games"]

    return {
        "series": {
            "series_count": len(formatted_matches),
            "wins": series_wins,
            "losses": series_losses,
            "win_rate": round(series_wins / decided_series * 100, 1) if decided_series else 0,
            "undecided": len(formatted_matches) - decided_series,
        },
        "games": {
            **all_game_stats,
            "win_rate": round(all_game_stats["wins"] / game_count * 100, 1) if game_count else 0,
        },
        "game_scopes": {
            scope_name: game_totals(entries)
            for scope_name, entries in scope_cells.items()
        },
        "pokemon": pokemon_scopes,
    }
