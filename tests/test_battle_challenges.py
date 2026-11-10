import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import insert_app
from app.battle_matches import (
    calculate_series_result,
    create_battle_match,
    is_battle_challenge_message,
    parse_tournament_games,
    save_battle_challenge_message,
)
from app.battle_parser import parse_discord_message
from scripts import output_result
from scripts.migrate_battle_challenges import migrate_database


EXAMPLE_MESSAGE = """大阪　バトルチャレンジ
<:02emoji_30:1555182396113031228> <:25kanutyann:1566689717053952030> <:03maruVS:1555182421840891967> <:20rekkuuza:1566689094556328016>
<:02emoji_30:1555182396113031228> <:25kanutyann:1566689717053952030> <:04batsuVS:1555182440593621082> <:20rekkuuza:1566689094556328016>
<:01moji_31:1555182348209754244> <:25kanutyann:1566689717053952030> <:03maruVS:1555182421840891967> <:20rekkuuza:1566689094556328016>
参加者8人中、5人がレックウザでした笑
無事イーブイゲット！！"""


def create_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE seasons (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE players (
                id INTEGER PRIMARY KEY,
                discord_user_id TEXT UNIQUE,
                name TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE pokemon (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                type1 TEXT,
                type2 TEXT,
                weaknesses TEXT
            );
            CREATE TABLE battle_messages (
                id INTEGER PRIMARY KEY,
                discord_message_id TEXT UNIQUE,
                season_id INTEGER,
                battle_date TEXT,
                raw_content TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE battle_matches (
                match_id INTEGER PRIMARY KEY,
                match_type TEXT NOT NULL,
                tournament_name TEXT,
                opponent TEXT,
                match_date TEXT NOT NULL,
                match_result TEXT,
                player_score INTEGER NOT NULL,
                opponent_score INTEGER NOT NULL,
                season_id INTEGER NOT NULL,
                player_id INTEGER,
                battle_message_id INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE battles (
                id INTEGER PRIMARY KEY,
                battle_message_id INTEGER,
                battle_number INTEGER NOT NULL,
                player1_id INTEGER,
                player2_id INTEGER,
                player1_pokemon_id INTEGER NOT NULL,
                player2_pokemon_id INTEGER NOT NULL,
                first_player INTEGER,
                player1_result TEXT NOT NULL,
                player2_result TEXT NOT NULL,
                battle_type TEXT NOT NULL DEFAULT 'normal',
                match_id INTEGER,
                game_number INTEGER,
                UNIQUE (battle_message_id, battle_number),
                UNIQUE (match_id, game_number)
            );
            INSERT INTO seasons VALUES (3, 'Season 3');
            INSERT INTO players VALUES (1, 'discord-1', 'Player One', '2026-01-01');
            INSERT INTO pokemon VALUES (1, 'カヌチャン', 'はがね', 'フェアリー', '[]');
            INSERT INTO pokemon VALUES (2, 'レックウザ', 'ドラゴン', 'そら', '[]');
            INSERT INTO battle_messages
                VALUES (1, 'message-1', 3, '2026-11-10', '', '2026-11-10T00:00:00');
            INSERT INTO battle_messages
                VALUES (2, 'message-2', 3, '2026-11-10', '', '2026-11-10T00:00:00');
        """)
        conn.execute(
            "UPDATE battle_messages SET raw_content = ? WHERE id IN (1, 2)",
            (EXAMPLE_MESSAGE,),
        )


def game_form(index: int, result: str) -> dict:
    return {
        f"player1_pokemon_id_{index}": "1",
        f"player2_pokemon_id_{index}": "2",
        f"player1_result_{index}": result,
        f"first_player_{index}": "1",
    }


class BattleChallengeTests(unittest.TestCase):
    def test_keywords_identify_battle_challenge_messages(self):
        for keyword in ("バトルチャレンジ", "バトチャレ", "バトチャレンジ", "バトルチャレ"):
            content = f"大阪 {keyword}\n{EXAMPLE_MESSAGE.splitlines()[1]}"
            self.assertTrue(is_battle_challenge_message(content))
            self.assertEqual(parse_discord_message(content)[0]["battle_type"], "challenge")

        self.assertEqual(len(parse_tournament_games(EXAMPLE_MESSAGE)), 3)
        self.assertEqual(
            [game["player1_result"] for game in parse_tournament_games(EXAMPLE_MESSAGE)],
            ["win", "lose", "win"],
        )

    def test_series_results_and_undecided_series(self):
        self.assertEqual(calculate_series_result(["win", "win"]), ("win", 2, 0))
        self.assertEqual(calculate_series_result(["win", "lose", "win"]), ("win", 2, 1))
        self.assertEqual(calculate_series_result(["lose", "lose"]), ("lose", 0, 2))
        self.assertEqual(calculate_series_result(["lose", "win", "lose"]), ("lose", 1, 2))
        self.assertEqual(calculate_series_result(["win"]), (None, 1, 0))
        self.assertEqual(calculate_series_result(["win", "lose"]), (None, 1, 1))

    def test_bot_saves_tournament_source_message_without_creating_normal_games(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            create_database(db_path)
            message_id = save_battle_challenge_message(
                str(db_path),
                "discord-message",
                EXAMPLE_MESSAGE,
                "2026-11-10T12:34:56+00:00",
            )
            self.assertIsNotNone(message_id)
            with sqlite3.connect(db_path) as conn:
                message = conn.execute(
                    "SELECT battle_date, raw_content FROM battle_messages WHERE id = ?",
                    (message_id,),
                ).fetchone()
                self.assertEqual(message, ("2026-11-10", EXAMPLE_MESSAGE))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM battles").fetchone()[0], 0)

    def test_registration_saves_games_and_series_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            create_database(db_path)
            with patch.object(insert_app, "DB_PATH", db_path):
                client = insert_app.app.test_client()
                response = client.post("/battle-challenge?message_id=1", data={
                    "message_id": "1",
                    "season_id": "3",
                    "tournament_name": "大阪　バトルチャレンジ",
                    "opponent": "",
                    "match_date": "2026-11-10",
                    "player_id": "1",
                    **game_form(1, "win"),
                    **game_form(2, "lose"),
                    **game_form(3, "win"),
                })
                self.assertEqual(response.status_code, 302)

            with sqlite3.connect(db_path) as conn:
                match = conn.execute("""
                    SELECT match_result, player_score, opponent_score
                    FROM battle_matches
                """).fetchone()
                games = conn.execute("""
                    SELECT game_number, player1_result, match_id
                    FROM battles
                    ORDER BY game_number
                """).fetchall()
            self.assertEqual(match, ("win", 2, 1))
            self.assertEqual(
                [(row[0], row[1]) for row in games],
                [(1, "win"), (2, "lose"), (3, "win")],
            )
            self.assertTrue(all(row[2] == games[0][2] for row in games))

    def test_decisive_result_rejects_later_game_without_partial_write(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            create_database(db_path)
            with patch.object(insert_app, "DB_PATH", db_path):
                response = insert_app.app.test_client().post(
                    "/battle-challenge?message_id=1",
                    data={
                        "message_id": "1",
                        "season_id": "3",
                        "match_date": "2026-11-10",
                        **game_form(1, "win"),
                        **game_form(2, "win"),
                        **game_form(3, "lose"),
                    },
                )
            self.assertEqual(response.status_code, 400)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM battle_matches").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM battles").fetchone()[0], 0)

    def test_one_game_series_can_be_registered_as_undecided(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            create_database(db_path)
            with patch.object(insert_app, "DB_PATH", db_path):
                response = insert_app.app.test_client().post(
                    "/battle-challenge?message_id=1",
                    data={
                        "message_id": "1",
                        "season_id": "3",
                        "match_date": "2026-11-10",
                        **game_form(1, "win"),
                    },
                )
            self.assertEqual(response.status_code, 302)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT match_result, player_score, opponent_score FROM battle_matches"
                    ).fetchone(),
                    (None, 1, 0),
                )

    def test_export_keeps_normal_json_separate_and_includes_tournament_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            create_database(db_path)
            with sqlite3.connect(db_path) as raw:
                raw.execute("""
                    INSERT INTO battles (
                        battle_message_id, battle_number, player1_pokemon_id,
                        player2_pokemon_id, first_player, player1_result, player2_result
                    ) VALUES (2, 1, 1, 2, 1, 'lose', 'win')
                """)
                raw.row_factory = sqlite3.Row
                with raw:
                    match_id = create_battle_match(
                        raw,
                        battle_message_id=1,
                        season_id=3,
                        tournament_name="大阪　バトルチャレンジ",
                        opponent=None,
                        match_date="2026-11-10",
                        player_id=1,
                        games=[
                            {"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "win", "first_player": 1},
                            {"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "lose", "first_player": 2},
                            {"player1_pokemon_id": 1, "player2_pokemon_id": 2, "player1_result": "win", "first_player": 1},
                        ],
                    )

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            original = (
                output_result.ACTIVE_SEASON_ID,
                output_result.ACTIVE_POKEMON_IDS,
                output_result.ACTIVE_POKEMON_PLACEHOLDERS,
                output_result.OUTPUT_DIR,
            )
            output_result.ACTIVE_SEASON_ID = 3
            output_result.ACTIVE_POKEMON_IDS = (1, 2)
            output_result.ACTIVE_POKEMON_PLACEHOLDERS = "?, ?"
            output_result.OUTPUT_DIR = Path(directory) / "out"
            try:
                output_result.export_battles(conn)
                matches = output_result.export_battle_matches(conn)
                output_result.export_battle_challenge_stats(conn, matches)
            finally:
                conn.close()
                (
                    output_result.ACTIVE_SEASON_ID,
                    output_result.ACTIVE_POKEMON_IDS,
                    output_result.ACTIVE_POKEMON_PLACEHOLDERS,
                    output_result.OUTPUT_DIR,
                ) = original

            out_dir = Path(directory) / "out"
            normal_json = json.loads((out_dir / "battles.json").read_text())
            match_json = json.loads((out_dir / "battle_matches.json").read_text())
            stats = json.loads((out_dir / "battle_challenge_stats.json").read_text())
            self.assertEqual(len(normal_json["battles"]), 1)
            self.assertEqual(match_json["matches"][0]["match_id"], match_id)
            self.assertEqual(match_json["matches"][0]["player_score"], 2)
            game = match_json["matches"][0]["games"][0]
            self.assertEqual(game["game_number"], 1)
            self.assertEqual(game["battle_type"], "challenge")
            self.assertEqual(game["player1"]["pokemon"], "カヌチャン")
            self.assertEqual(stats["series"]["wins"], 1)
            self.assertEqual(stats["games"]["games"], 3)
            self.assertEqual(stats["game_scopes"]["normal"]["games"], 1)
            self.assertEqual(stats["game_scopes"]["all"]["games"], 4)
            self.assertIn("usage_rate", stats["pokemon"]["battle_challenge"][0])

    def test_migration_backs_up_and_preserves_existing_battles(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript("""
                    CREATE TABLE seasons (id INTEGER PRIMARY KEY);
                    CREATE TABLE players (id INTEGER PRIMARY KEY);
                    CREATE TABLE pokemon (id INTEGER PRIMARY KEY);
                    CREATE TABLE battle_messages (id INTEGER PRIMARY KEY);
                    CREATE TABLE battles (
                        id INTEGER PRIMARY KEY,
                        battle_message_id INTEGER NOT NULL,
                        battle_number INTEGER NOT NULL,
                        player1_id INTEGER,
                        player2_id INTEGER,
                        player1_pokemon_id INTEGER NOT NULL,
                        player2_pokemon_id INTEGER NOT NULL,
                        first_player INTEGER,
                        player1_result TEXT NOT NULL,
                        player2_result TEXT NOT NULL,
                        battle_type TEXT NOT NULL DEFAULT 'normal'
                    );
                    INSERT INTO pokemon VALUES (1);
                    INSERT INTO pokemon VALUES (2);
                    INSERT INTO battle_messages VALUES (3);
                    INSERT INTO battles VALUES (17, 3, 4, 1, 2, 1, 2, 1, 'win', 'lose', 'normal');
                """)

            backup = migrate_database(db_path)
            self.assertTrue(backup.is_file())
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("""
                    SELECT id, battle_message_id, battle_number, player1_result,
                        player2_result, battle_type, match_id, game_number
                    FROM battles
                """).fetchone()
                self.assertEqual(row, (17, 3, 4, "win", "lose", "normal", None, None))
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            with sqlite3.connect(backup) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM battles").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
