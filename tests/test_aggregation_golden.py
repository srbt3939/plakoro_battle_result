"""Golden comparison tests & edge-case tests for app.aggregation (手順6 §6)."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3
import unittest

from app.aggregation import (
    build_battle_matches_json,
    build_battle_trend_json,
    build_battles_json,
    build_players_json,
    build_season_cells,
    compute_battle_challenge_stats,
    compute_character_detail,
    compute_first_player_summary,
    compute_matchups,
    compute_player_detail,
    compute_player_list,
    compute_pokemon_stats,
    compute_usage,
    filter_and_merge_cells,
)
from app.lambda_api.cache import SeasonDataCache
from scripts.build_test_fixtures import ANONYMIZED_DB_PATH, FIXED_TODAY, GOLDEN_DIR
from scripts.migrate_to_dynamodb import execute_migration
from tests.fake_dynamodb import InMemoryDynamoDBClient

BASE_DIR = Path(__file__).resolve().parent.parent


class AggregationGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ddb = InMemoryDynamoDBClient("golden_", page_size=200)
        execute_migration(ANONYMIZED_DB_PATH, cls.ddb, "golden_")
        cls.cache = SeasonDataCache()
        cls.mv, cls.pokemon_by_id, cls.players_by_id, cls.seasons_list = (
            cls.cache.get_masters(cls.ddb, "golden_")
        )
        cls.js_golden = json.loads((GOLDEN_DIR / "js_golden.json").read_text(encoding="utf-8"))

    def test_anonymized_fixtures_contain_zero_real_pii(self) -> None:
        real_db = BASE_DIR / "data" / "pokemon.db"
        if not real_db.exists():
            self.skipTest("Real DB not present")
        with sqlite3.connect(real_db) as conn:
            real_discord_ids = {
                str(r[0]) for r in conn.execute("SELECT discord_user_id FROM players").fetchall() if r[0]
            }
            real_msg_ids = {
                str(r[0]) for r in conn.execute("SELECT discord_message_id FROM battle_messages").fetchall() if r[0]
            }
            real_names = {
                str(r[0]) for r in conn.execute("SELECT name FROM players").fetchall() if r[0]
            }

        with sqlite3.connect(ANONYMIZED_DB_PATH) as aconn:
            anon_discord_ids = {
                str(r[0]) for r in aconn.execute("SELECT discord_user_id FROM players").fetchall()
            }
            anon_msg_ids = {
                str(r[0]) for r in aconn.execute("SELECT discord_message_id FROM battle_messages").fetchall()
            }
            anon_names = {
                str(r[0]) for r in aconn.execute("SELECT name FROM players").fetchall()
            }

        self.assertTrue(real_discord_ids.isdisjoint(anon_discord_ids))
        self.assertTrue(real_msg_ids.isdisjoint(anon_msg_ids))
        self.assertTrue(real_names.isdisjoint(anon_names))

        for json_file in GOLDEN_DIR.rglob("*.json"):
            text = json_file.read_text(encoding="utf-8")
            for did in real_discord_ids:
                self.assertNotIn(did, text)

    def _get_scope_inputs(self, scope: str):
        if scope == "all":
            snaps = [
                self.cache.get_season_snapshot(self.ddb, "golden_", s["id"], self.mv)[0]
                for s in self.seasons_list
            ]
            allowed = set()
            battles = []
            matches = []
            for sn in snaps:
                allowed.update(sn.allowed_pokemon_ids)
                battles.extend(sn.battles)
                matches.extend(sn.matches)
            cells = filter_and_merge_cells([sn.cells for sn in snaps], allowed)
            return cells, battles, matches, allowed

        sid = int(scope.split("_")[1])
        sn, _ = self.cache.get_season_snapshot(self.ddb, "golden_", sid, self.mv)
        allowed = set(sn.allowed_pokemon_ids)
        cells = filter_and_merge_cells([sn.cells], allowed)
        return cells, sn.battles, sn.matches, allowed

    def test_all_29_golden_json_files_match_exactly(self) -> None:
        # 1. players.json
        expected_players = json.loads((GOLDEN_DIR / "players.json").read_text(encoding="utf-8"))
        actual_players = build_players_json(list(self.players_by_id.values()))
        self.assertEqual(actual_players, expected_players)

        # 2. 4 scopes x 7 files = 28 files
        for scope in ("season_1", "season_2", "season_3", "all"):
            with self.subTest(scope=scope):
                cells, battles, matches, allowed = self._get_scope_inputs(scope)
                matches_by_id = {int(m["match_id"]): m for m in matches}

                actual_battles = build_battles_json(
                    battles, matches_by_id, self.pokemon_by_id, self.players_by_id, allowed
                )
                actual_matches = build_battle_matches_json(
                    matches, battles, self.pokemon_by_id, self.players_by_id
                )
                actual_ch_stats = compute_battle_challenge_stats(
                    cells, actual_matches["matches"], self.pokemon_by_id, allowed
                )
                actual_trend = build_battle_trend_json(battles, allowed, today=FIXED_TODAY)
                actual_usage = compute_usage(cells, self.pokemon_by_id, allowed)
                actual_poke_stats = compute_pokemon_stats(cells, self.pokemon_by_id, allowed)
                actual_matchups = compute_matchups(battles, self.pokemon_by_id, allowed)

                scope_dir = GOLDEN_DIR / scope
                self.assertEqual(
                    actual_battles,
                    json.loads((scope_dir / "battles.json").read_text(encoding="utf-8")),
                )
                self.assertEqual(
                    actual_matches,
                    json.loads((scope_dir / "battle_matches.json").read_text(encoding="utf-8")),
                )
                self.assertEqual(
                    actual_ch_stats,
                    json.loads((scope_dir / "battle_challenge_stats.json").read_text(encoding="utf-8")),
                )
                self.assertEqual(
                    actual_trend,
                    json.loads((scope_dir / "battle_trend.json").read_text(encoding="utf-8")),
                )
                self.assertEqual(
                    actual_usage,
                    json.loads((scope_dir / "usage.json").read_text(encoding="utf-8")),
                )
                self.assertEqual(
                    actual_poke_stats,
                    json.loads((scope_dir / "pokemon_stats.json").read_text(encoding="utf-8")),
                )
                self.assertEqual(
                    actual_matchups,
                    json.loads((scope_dir / "matchups.json").read_text(encoding="utf-8")),
                )

    def test_player_list_player_detail_and_character_detail_match_node_js_golden(self) -> None:
        players_ordered = build_players_json(list(self.players_by_id.values()))["players"]

        for scope in ("season_1", "season_2", "season_3", "all"):
            cells, battles, matches, allowed = self._get_scope_inputs(scope)
            matches_by_id = {int(m["match_id"]): m for m in matches}
            formatted_battles = build_battles_json(
                battles, matches_by_id, self.pokemon_by_id, self.players_by_id, allowed
            )["battles"]
            usage_pokemon = compute_usage(cells, self.pokemon_by_id, allowed)["pokemon"]
            matchups = compute_matchups(battles, self.pokemon_by_id, allowed)["matchups"]

            # P1: Player list
            p1 = compute_player_list(players_ordered, formatted_battles)["players"]
            self.assertEqual(p1, self.js_golden["player_list"][scope])

            # P2: All players x 3 battle_types (300 cases)
            for p in players_ordered:
                pid_str = str(p["id"])
                for btype in ("all", "normal", "challenge"):
                    actual_p2 = compute_player_detail(p, formatted_battles, battle_type=btype)
                    exp_p2 = self.js_golden["player_detail"][scope][pid_str][btype]
                    self.assertEqual(actual_p2["summary"], exp_p2["summary"])
                    self.assertEqual(actual_p2["recent"], exp_p2["recent"])
                    self.assertEqual(actual_p2["pokemon_usage"], exp_p2["pokemon_usage"])
                    self.assertEqual(
                        [b["id"] for b in actual_p2["recent_battles"]],
                        exp_p2["recent_battle_ids"],
                    )
                    # Q2 verification: new.first == old.first, new.second + new.unknown == old.second
                    self.assertEqual(actual_p2["order"]["first"], exp_p2["legacy_order"]["first"])
                    self.assertEqual(
                        actual_p2["order"]["second"]["battles"] + actual_p2["order"]["unknown"]["battles"],
                        exp_p2["legacy_order"]["second_plus_unknown"]["battles"],
                    )
                    self.assertEqual(
                        actual_p2["order"]["second"]["wins"] + actual_p2["order"]["unknown"]["wins"],
                        exp_p2["legacy_order"]["second_plus_unknown"]["wins"],
                    )
                    self.assertEqual(
                        actual_p2["order"]["unknown"]["battles"],
                        exp_p2["legacy_order"]["null_first_player_count"],
                    )

            # G5: All 23 characters x 4 scopes
            for cid, poke in self.pokemon_by_id.items():
                actual_g5 = compute_character_detail(
                    poke, formatted_battles, matchups, usage_pokemon, self.players_by_id
                )
                exp_g5 = self.js_golden["character_detail"][scope][str(cid)]
                self.assertEqual(actual_g5["usage_count"], exp_g5["usage_count"])
                self.assertEqual(actual_g5["summary"]["battles"], exp_g5["summary"]["battles"])
                self.assertEqual(actual_g5["summary"]["wins"], exp_g5["summary"]["wins"])
                top_opp_id = actual_g5["summary"]["top_opponent"]["id"] if actual_g5["summary"]["top_opponent"] else None
                self.assertEqual(top_opp_id, exp_g5["summary"]["top_opponent_id"])
                self.assertEqual(actual_g5["order"]["first"], exp_g5["legacy_order"]["first"])
                self.assertEqual(
                    actual_g5["order"]["second"]["battles"] + actual_g5["order"]["unknown"]["battles"],
                    exp_g5["legacy_order"]["second_plus_unknown"]["battles"],
                )
                self.assertEqual(
                    actual_g5["order"]["unknown"]["battles"],
                    exp_g5["legacy_order"]["null_first_player_count"],
                )
                actual_pcounts = {str(u["id"]): u["count"] for u in actual_g5["player_usage"]}
                self.assertEqual(actual_pcounts, exp_g5["player_counts"])
                self.assertEqual(
                    [b["id"] for b in actual_g5["recent_battles"]],
                    exp_g5["recent_battle_ids"],
                )

    def test_edge_cases_mirror_separation_union_scope_and_reset_boundary(self) -> None:
        # 1. Union of allowedPokemonIds in 'all' includes battle excluded in season 1
        s1_battles = [
            {
                "id": 1,
                "battle_message_id": 1,
                "battle_number": 1,
                "player1_pokemon_id": 1,
                "player2_pokemon_id": 20,  # Not in season 1 allowed {1..12}, but in season 3 {1..23}
                "first_player": 1,
                "player1_result": "win",
                "player2_result": "lose",
                "match_id": None,
            }
        ]
        s1_cells = build_season_cells(s1_battles)
        self.assertEqual(filter_and_merge_cells([s1_cells], set(range(1, 13))), {})
        merged_all = filter_and_merge_cells([s1_cells], set(range(1, 24)))
        self.assertEqual(sum(merged_all.values()), 1)

        # 2. Mirror separation & C1 exclude_mirror flag
        synth_battles = [
            {
                "id": 10,
                "battle_message_id": 2,
                "battle_number": 1,
                "player1_pokemon_id": 1,
                "player2_pokemon_id": 1,  # Mirror!
                "first_player": 1,
                "player1_result": "win",
                "player2_result": "lose",
                "match_id": None,
            },
            {
                "id": 11,
                "battle_message_id": 2,
                "battle_number": 2,
                "player1_pokemon_id": 1,
                "player2_pokemon_id": 2,
                "first_player": None,
                "player1_result": "lose",
                "player2_result": "win",
                "match_id": None,
                "battle_date": "",
            },
            {
                "id": 12,
                "battle_message_id": 2,
                "battle_number": 3,
                "player1_pokemon_id": 1,
                "player2_pokemon_id": 2,
                "first_player": 1,
                "player1_result": "win",
                "player2_result": "lose",
                "match_id": None,
                "is_deleted": True,  # Soft-deleted: must be ignored
            },
        ]
        cells = build_season_cells(synth_battles)
        fp_summary = compute_first_player_summary(cells)
        self.assertEqual(fp_summary["all"], {"games": 2, "first_wins": 1, "second_wins": 0, "unknown": 1})
        self.assertEqual(fp_summary["mirror"], {"games": 1, "first_wins": 1, "second_wins": 0, "unknown": 0})
        self.assertEqual(fp_summary["non_mirror"], {"games": 1, "first_wins": 0, "second_wins": 0, "unknown": 1})

        p_map = {
            1: {"id": 1, "name": "A", "type1": "くさ", "type2": None, "weaknesses": []},
            2: {"id": 2, "name": "B", "type1": "ほのお", "type2": None, "weaknesses": []},
        }
        c1_stats = compute_pokemon_stats(
            cells, p_map, {1, 2}, exclude_mirror=True, include_mirror_field=True
        )["pokemon"]
        p1_stat = next(p for p in c1_stats if p["pokemon_id"] == 1)
        self.assertEqual(p1_stat["total"]["battles"], 1)  # Mirror excluded from win_rate
        self.assertEqual(p1_stat["total"]["wins"], 0)
        self.assertEqual(p1_stat["mirror"], {"battles": 1, "first_wins": 1, "second_wins": 0, "unknown": 0})

        # Empty battle_date excluded from trend
        trend = build_battle_trend_json(synth_battles, {1, 2}, today=date(2026, 11, 27))
        self.assertEqual(sum(d["battle_count"] for d in trend["days"]), 0)

        # Reset boundary: reset_after_battle_id excludes <= boundary
        formatted = build_battles_json(
            [
                {**synth_battles[0], "season_id": 1, "player1_id": 5, "player2_id": 6},
                {**synth_battles[1], "season_id": 1, "player1_id": 5, "player2_id": 6},
            ],
            {},
            p_map,
            {5: {"id": 5, "name": "P5"}, 6: {"id": 6, "name": "P6"}},
            {1, 2},
        )["battles"]
        detail_after_10 = compute_player_detail(
            {"id": 5, "name": "P5"},
            formatted,
            reset_after_battle_id=10,
        )
        self.assertEqual(detail_after_10["summary"]["battles"], 1)
        self.assertEqual(detail_after_10["recent_battles"][0]["id"], 11)


if __name__ == "__main__":
    unittest.main()
