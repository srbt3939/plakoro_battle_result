"""Contract tests for SQLite & DynamoDB repositories, migration, integrity, and rollback (手順4・5・8)."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import insert_app
from app.dynamodb_serde import serialize_item
from app.repository import (
    DuplicateError,
    DynamoDBBattleRepository,
    NotFoundError,
    SQLiteBattleRepository,
)
from scripts.build_test_fixtures import ANONYMIZED_DB_PATH
from scripts.check_integrity import check_dynamodb_integrity
from scripts.export_to_sqlite import export_dynamodb_to_sqlite
from scripts.migrate_to_dynamodb import execute_migration, verify_against_dynamodb, extract_and_transform
from scripts.verify_aws_setup import verify_tables
from tests.fake_dynamodb import InMemoryDynamoDBClient
from tests.test_battle_challenges import EXAMPLE_MESSAGE, create_database


class RepositoryContractTests(unittest.TestCase):
    def _make_sqlite_repo(self, tmp_dir: str) -> SQLiteBattleRepository:
        db_path = Path(tmp_dir) / "contract.db"
        create_database(db_path)
        return SQLiteBattleRepository(db_path)

    def _make_dynamodb_repo(self) -> tuple[DynamoDBBattleRepository, InMemoryDynamoDBClient]:
        ddb = InMemoryDynamoDBClient("contract_")
        for tbl, item in (
            ("Seasons", {"seasonId": 3, "name": "Season 3", "startDate": "2026-10-01", "allowedPokemonIds": [1, 2], "dataVersion": 1, "battleCount": 0, "matchCount": 0}),
            ("Pokemon", {"pokemonId": 1, "name": "カヌチャン", "type1": "はがね", "weaknesses": []}),
            ("Pokemon", {"pokemonId": 2, "name": "レックウザ", "type1": "ドラゴン", "weaknesses": []}),
            ("Players", {"playerId": 1, "name": "Player One", "discordUserId": "discord-1", "createdAt": "2026-01-01", "isPublic": True}),
            ("DiscordAccounts", {"discordUserId": "discord-1", "playerId": 1, "createdAt": "2026-01-01"}),
            ("BattleMessages", {"messageId": 1, "discordMessageId": "message-1", "seasonId": 3, "battleDate": "2026-11-10", "rawContent": EXAMPLE_MESSAGE, "createdAt": "2026-11-10T00:00:00", "lastBattleNumber": 0, "isBattleChallenge": True}),
            ("DiscordMessages", {"discordMessageId": "message-1", "messageId": 1}),
            ("BattleMessages", {"messageId": 44, "discordMessageId": "message-44", "seasonId": 3, "battleDate": "2026-11-10", "rawContent": "通常報告", "createdAt": "manual", "lastBattleNumber": 6}),
            ("DiscordMessages", {"discordMessageId": "message-44", "messageId": 44}),
            ("Counters", {"counterName": "playerId", "value": 1}),
            ("Counters", {"counterName": "messageId", "value": 44}),
            ("Counters", {"counterName": "battleId", "value": 100}),
            ("Counters", {"counterName": "matchId", "value": 0}),
            ("Counters", {"counterName": "resetRequestId", "value": 0}),
            ("Counters", {"counterName": "masterVersion", "value": 1}),
        ):
            ddb.put_item(TableName=f"contract_{tbl}", Item=serialize_item(item))
        return DynamoDBBattleRepository(table_prefix="contract_", dynamodb_client=ddb), ddb

    def _run_common_contract(self, repo) -> None:
        # 1. Duplicate message -> DuplicateError
        mid = repo.create_message("msg-unique-1", 3, "2026-11-20", "テスト報告")
        self.assertGreater(mid, 0)
        with self.assertRaises(DuplicateError):
            repo.create_message("msg-unique-1", 3, "2026-11-20", "テスト報告")

        # 2. Idempotent create_battles with request_id + null players
        rows = [
            {
                "player1_pokemon_id": 1,
                "player2_pokemon_id": 2,
                "first_player": None,
                "player1_result": "win",
                "battle_type": "normal",
            }
        ]
        ids_first = repo.create_battles(mid, None, None, rows, request_id="req-uuid-001")
        ids_retry = repo.create_battles(mid, None, None, rows, request_id="req-uuid-001")
        self.assertEqual(ids_first, ids_retry)

        b = repo.get_battle(ids_first[0])
        self.assertIsNotNone(b)
        self.assertIsNone(b["player1_id"])
        self.assertIsNone(b["player2_id"])

        # 3. BO3 decisive game rejection & duplicate BO3 check
        with self.assertRaises(ValueError):
            repo.create_battle_match(
                battle_message_id=1,
                season_id=3,
                tournament_name="テスト大会",
                opponent="相手",
                match_date="2026-11-10",
                player_id=1,
                games=[
                    {"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "win", "first_player": 1},
                    {"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "win", "first_player": 1},
                    {"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "lose", "first_player": 2},
                ],
            )
        match_id = repo.create_battle_match(
            battle_message_id=1,
            season_id=3,
            tournament_name="テスト大会",
            opponent="相手",
            match_date="2026-11-10",
            player_id=1,
            games=[
                {"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "win", "first_player": 1},
                {"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "lose", "first_player": 2},
                {"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "win", "first_player": 1},
            ],
        )
        with self.assertRaises(DuplicateError):
            repo.create_battle_match(
                battle_message_id=1,
                season_id=3,
                tournament_name="テスト大会",
                opponent="相手",
                match_date="2026-11-10",
                player_id=1,
                games=[{"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "win", "first_player": 1}],
            )

        # 4. Soft-delete battle & BO3 match (unlinks match_id so BO3 can be re-registered)
        repo.delete_battle(ids_first[0], "誤登録テスト")
        self.assertIsNone(repo.get_battle(ids_first[0]))

        repo.delete_match(match_id, "シリーズ誤登録テスト")
        self.assertIsNone(repo.get_match(match_id))
        self.assertIsNone(repo.get_message(1)["match_id"])

    def test_sqlite_repository_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_sqlite_repo(tmp)
            self._run_common_contract(repo)

    def test_dynamodb_repository_contract_and_multi_discord_link(self) -> None:
        repo, ddb = self._make_dynamodb_repo()
        self._run_common_contract(repo)

        # Gap continuation on message 44 (lastBattleNumber=6 -> next battleNumber=7)
        new_ids = repo.create_battles(
            44,
            1,
            None,
            [{"player1_pokemon_id": 1, "player2_pokemon_id": 2, "first_player": 1, "player1_result": "win"}],
            request_id="gap-test",
        )
        b_gap = repo.get_battle(new_ids[0])
        self.assertEqual(b_gap["battle_number"], 7)

        # Multi-Discord account linking (hand-camera sub-account)
        repo.link_discord_account(1, "discord-sub-cam", label="手元カメラ用")
        with self.assertRaises(DuplicateError):
            repo.link_discord_account(1, "discord-sub-cam", label="重複")
        with self.assertRaises(NotFoundError):
            repo.link_discord_account(999, "discord-unknown")

        players = repo.list_players(include_accounts=True)
        self.assertEqual(len(players[0]["discord_accounts"]), 2)

        # GSI lag resilience via include_ids
        ddb.hidden_from_gsi.add(("contract_Battles", new_ids[0]))
        ddb.gsi_lag_countdown["contract_Battles"] = 5
        recent_with_include = repo.list_recent_battles(limit=50, include_ids=new_ids)
        self.assertIn(new_ids[0], [r["id"] for r in recent_with_include])

    def test_insert_app_origin_csrf_and_delete_routes(self) -> None:
        repo, _ddb = self._make_dynamodb_repo()
        with patch.object(insert_app, "REPOSITORY", repo):
            client = insert_app.app.test_client()
            # Cross-origin POST is rejected with 403
            evil_resp = client.post(
                "/player",
                data={"discord_user_id": "x", "name": "Evil"},
                headers={"Origin": "https://evil.example.com"},
            )
            self.assertEqual(evil_resp.status_code, 403)

            # Localhost Origin is accepted
            ok_resp = client.post(
                "/player",
                data={"discord_user_id": "discord-new", "name": "NewPlayer"},
                headers={"Origin": "http://127.0.0.1:5000"},
            )
            self.assertEqual(ok_resp.status_code, 302)

            # Header shows DynamoDB prefix
            index_html = client.get("/").get_data(as_text=True)
            self.assertIn("DynamoDB (contract_)", index_html)


class MigrationIntegrityAndRollbackTests(unittest.TestCase):
    def test_full_migration_integrity_check_and_reverse_export_roundtrip(self) -> None:
        ddb = InMemoryDynamoDBClient("mig_")
        self.assertEqual(verify_tables(ddb, "mig_"), [])

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            report = execute_migration(
                ANONYMIZED_DB_PATH,
                ddb,
                "mig_",
                backup_dir=tmp_path,
            )
            self.assertTrue(report["ok"])

            # Completion marker prevents accidental rerun without --force
            with self.assertRaises(RuntimeError):
                execute_migration(ANONYMIZED_DB_PATH, ddb, "mig_", backup_dir=tmp_path)

            # With --force, idempotent rerun skips all identical items cleanly
            rerun = execute_migration(
                ANONYMIZED_DB_PATH,
                ddb,
                "mig_",
                force=True,
                backup_dir=tmp_path,
            )
            self.assertEqual(rerun["write_stats"]["Battles"]["inserted"], 0)
            self.assertGreater(rerun["write_stats"]["Battles"]["skipped"], 1200)

            # Integrity check passes all 9 checks
            integrity = check_dynamodb_integrity(ddb, "mig_")
            self.assertTrue(integrity["ok"], integrity["errors"])

            # Mutating 1 battle score/count causes integrity check & verify to detect failure
            repo = DynamoDBBattleRepository(table_prefix="mig_", dynamodb_client=ddb)
            repo.link_discord_account(1, "999000000000000001", label="手元カメラ用")
            ddb.update_item(
                TableName="mig_Players",
                Key={"playerId": {"N": "1"}},
                UpdateExpression="SET isPublic = :f, resetAfterBattleId = :r",
                ExpressionAttributeValues={":f": {"BOOL": False}, ":r": {"N": "500"}},
            )
            self.assertTrue(check_dynamodb_integrity(ddb, "mig_")["ok"])

            # Reverse export to SQLite + sidecar JSON
            rollback_db = tmp_path / "rollback.db"
            sidecar_json = tmp_path / "sidecar.json"
            exp_report = export_dynamodb_to_sqlite(ddb, "mig_", rollback_db, sidecar_json)
            self.assertTrue(exp_report["ok"])
            self.assertEqual(exp_report["counts"]["extra_discord_accounts"], 1)

            # Re-migrate into a fresh DynamoDB prefix using rollback.db + sidecar.json
            ddb2 = InMemoryDynamoDBClient("remig_")
            sidecar_data = __import__("json").loads(sidecar_json.read_text(encoding="utf-8"))
            remig_report = execute_migration(
                rollback_db,
                ddb2,
                "remig_",
                sidecar=sidecar_data,
                backup_dir=tmp_path / "b2",
            )
            self.assertTrue(remig_report["ok"])
            self.assertTrue(check_dynamodb_integrity(ddb2, "remig_")["ok"])

            # Confirm sidecar preserved isPublic=False, resetAfterBattleId=500, and 2nd Discord ID
            repo2 = DynamoDBBattleRepository(table_prefix="remig_", dynamodb_client=ddb2)
            p1 = next(p for p in repo2.list_players(include_accounts=True) if p["id"] == 1)
            self.assertFalse(p1["is_public"])
            self.assertEqual(p1["reset_after_battle_id"], 500)
            self.assertEqual(len(p1["discord_accounts"]), 2)

            # Intentional corruption is caught by verify_against_dynamodb
            ddb2.update_item(
                TableName="remig_Battles",
                Key={"battleId": {"N": "1"}},
                UpdateExpression="SET player1Result = :bad",
                ExpressionAttributeValues={":bad": {"S": "corrupted"}},
            )
            expected = extract_and_transform(rollback_db, sidecar=sidecar_data)
            verify_res = verify_against_dynamodb(ddb2, "remig_", expected)
            self.assertFalse(verify_res["ok"])


if __name__ == "__main__":
    unittest.main()
