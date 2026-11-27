"""Cell-based season battle aggregation (手順6 §4-2).

Pure functions with zero AWS/boto3 dependencies.
"""

from __future__ import annotations

from collections import Counter

# (p1_pokemon_id, p2_pokemon_id, first_player, p1_result, p2_result, is_challenge)
CellKey = tuple[int, int, int | None, str, str, bool]


def is_battle_deleted(battle: dict) -> bool:
    return bool(battle.get("is_deleted") or battle.get("isDeleted"))


def build_season_cells(battles: list[dict]) -> dict[CellKey, int]:
    """Build unfiltered count cells for a single season's battles."""
    counts: Counter[CellKey] = Counter()
    for battle in battles:
        if is_battle_deleted(battle):
            continue
        p1_pid = int(battle["player1_pokemon_id"])
        p2_pid = int(battle["player2_pokemon_id"])
        fp_raw = battle.get("first_player")
        fp = int(fp_raw) if fp_raw in (1, 2, "1", "2") else None
        r1 = str(battle["player1_result"])
        r2 = str(battle["player2_result"])
        is_challenge = battle.get("match_id") is not None
        counts[(p1_pid, p2_pid, fp, r1, r2, is_challenge)] += 1
    return dict(counts)


def filter_and_merge_cells(
    cells_list: list[dict[CellKey, int]],
    allowed_pokemon_ids: set[int] | tuple[int, ...] | list[int],
) -> dict[CellKey, int]:
    """Filter cells by allowed_pokemon_ids on both sides and sum across seasons."""
    allowed = set(int(x) for x in allowed_pokemon_ids)
    merged: Counter[CellKey] = Counter()
    for cells in cells_list:
        for key, count in cells.items():
            if key[0] in allowed and key[1] in allowed:
                merged[key] += count
    return dict(merged)
