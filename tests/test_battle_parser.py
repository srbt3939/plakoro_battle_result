import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.battle_parser import parse_discord_message
from app import insert_app


class BattleParserTests(unittest.TestCase):
    def test_parses_consecutive_current_format_battles(self):
        content = "\n".join((
            "<:01moji_31:1555182348209754244> "
            "<:19rukario:1555182301162377287> "
            "<:03maruVS:1555182421840891967> "
            "<:05emoji_23:1555182462135705610>",
            "<:02emoji_30:1555182396113031228> "
            "<:08emoji_26:1555182522873155735> "
            "<:04batsuVS:1555182440593621082> "
            "<:18metagurosu:1555182267272400897>",
        ))

        self.assertEqual(
            parse_discord_message(content),
            [
                {
                    "first_player": 1,
                    "player1_pokemon_id": 15,
                    "player2_pokemon_id": 1,
                    "player1_result": "win",
                    "player2_result": "lose",
                },
                {
                    "first_player": 2,
                    "player1_pokemon_id": 4,
                    "player2_pokemon_id": 14,
                    "player1_result": "lose",
                    "player2_result": "win",
                },
            ],
        )

    def test_battle_form_prefills_selected_message(self):
        content = "\n".join((
            "Kai vs Mina",
            "<:01moji_31:1555182348209754244> "
            "<:19rukario:1555182301162377287> "
            "<:03maruVS:1555182421840891967> "
            "<:05emoji_23:1555182462135705610>",
            "<:02emoji_30:1555182396113031228> "
            "<:08emoji_26:1555182522873155735> "
            "<:04batsuVS:1555182440593621082> "
            "<:18metagurosu:1555182267272400897>",
        ))

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript("""
                    CREATE TABLE pokemon (id INTEGER PRIMARY KEY, name TEXT);
                    CREATE TABLE battle_messages (
                        id INTEGER PRIMARY KEY,
                        discord_message_id TEXT,
                        season_id INTEGER,
                        battle_date TEXT,
                        raw_content TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT);
                    CREATE TABLE seasons (id INTEGER PRIMARY KEY, name TEXT);
                """)
                conn.executemany(
                    "INSERT INTO pokemon (id, name) VALUES (?, ?)",
                    [(1, "フシギダネ"), (4, "ピカチュウ"), (14, "メタグロス"), (15, "ルカリオ")],
                )
                conn.executemany(
                    "INSERT INTO players (id, name) VALUES (?, ?)",
                    [(1, "Kai"), (2, "Mina")],
                )
                conn.execute("INSERT INTO seasons (id, name) VALUES (1, 'Test season')")
                conn.execute(
                    "INSERT INTO battle_messages (id, battle_date, raw_content) VALUES (?, ?, ?)",
                    (1, "2026-10-14", content),
                )

            with patch.object(insert_app, "DB_PATH", db_path):
                client = insert_app.app.test_client()
                response = client.get("/battle?message_id=1")
                registration = client.post("/message", data={
                    "discord_message_id": "123",
                    "season_id": "1",
                    "battle_date": "2026-10-15",
                    "raw_content": content,
                    "created_at": "2026-10-15T00:00:00",
                })
                redirected_form = client.get(registration.headers["Location"])

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("2試合を読み取りました", html)
        self.assertRegex(html, r'<option value="15"\s+selected>')
        self.assertRegex(html, r'<option value="4"\s+selected>')
        self.assertRegex(html, r'<option value="1"\s+selected>\s*Kai')
        self.assertRegex(html, r'<option value="2"\s+selected>\s*Mina')
        self.assertEqual(registration.status_code, 302)
        self.assertEqual(registration.headers["Location"], "/battle?message_id=2")
        self.assertIn("2試合を読み取りました", redirected_form.get_data(as_text=True))

    def test_parses_legacy_five_emoji_format(self):
        content = (
            "<:emoji_31:1538096814773768212>"
            "<:rukario:1544186357923188746>"
            "<:maruVS:1545291082860859412>"
            "<:emoji_23:1536404559155433603>"
            "<:emoji_30:1538096705478860810>"
        )

        self.assertEqual(
            parse_discord_message(content),
            [
                {
                    "first_player": 1,
                    "player1_pokemon_id": 15,
                    "player2_pokemon_id": 1,
                    "player1_result": "win",
                    "player2_result": "lose",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
