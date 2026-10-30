import sqlite3
import tempfile
import unittest
import re
from pathlib import Path
from unittest.mock import patch

from app.battle_parser import parse_discord_message
from app import insert_app


class BattleParserTests(unittest.TestCase):
    def test_parses_current_discord_emoji_names(self):
        content = (
            "<:01moji_31:1555182348209754244>"
            "<:18metagurosu:1555182267272401290>"
            "<:04batsuVS:1555182440593621082>"
            "<:19rukario:1555182301162377287>"
        )

        self.assertEqual(
            parse_discord_message(content),
            [
                {
                    "first_player": 1,
                    "player1_pokemon_id": 14,
                    "player2_pokemon_id": 15,
                    "player1_result": "lose",
                    "player2_result": "win",
                    "battle_type": "normal",
                }
            ],
        )

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
                    "battle_type": "normal",
                },
                {
                    "first_player": 2,
                    "player1_pokemon_id": 4,
                    "player2_pokemon_id": 14,
                    "player1_result": "lose",
                    "player2_result": "win",
                    "battle_type": "normal",
                },
            ],
        )

    def test_applies_battle_type_headers_to_each_following_battle(self):
        content = "\n".join((
            "【バトルチャレンジ】",
            "<:01moji_31:1555182348209754244> "
            "<:19rukario:1555182301162377287> "
            "<:03maruVS:1555182421840891967> "
            "<:05emoji_23:1555182462135705610>",
            "【通常対戦】",
            "<:02emoji_30:1555182396113031228> "
            "<:08emoji_26:1555182522873155735> "
            "<:04batsuVS:1555182440593621082> "
            "<:18metagurosu:1555182267272400897>",
        ))

        battles = parse_discord_message(content)

        self.assertEqual([battle["battle_type"] for battle in battles], ["challenge", "normal"])

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
        self.assertRegex(html, r'<option value="15"[^>]*selected>')
        self.assertRegex(html, r'<option value="4"[^>]*selected>')
        self.assertRegex(html, r'<option value="1"\s+selected>\s*Kai')
        self.assertRegex(html, r'<option value="2"\s+selected>\s*Mina')
        self.assertIn('data-emoji-id="1555182301162377287"', html)
        self.assertIn('data-emoji-id="1555182462135705610"', html)
        self.assertIn('data-emoji-id="1555182348209754244"', html)
        self.assertIn('data-emoji-id="1555182396113031228"', html)
        self.assertIn('data-emoji-id="1555182011461927043"', html)
        self.assertIn('data-emoji-id="1555181988900507709"', html)
        self.assertEqual(html.count('class="battle-emoji-row"'), 3)
        self.assertEqual(html.count('<div class="emoji-preview"'), 12)
        emoji_response = client.get(
            "/static/images/emoji_images/1555182301162377287.webp"
        )
        self.assertEqual(emoji_response.status_code, 200)
        emoji_response.close()
        for emoji_id in (
            "1555182348209754244",
            "1555182396113031228",
            "1555182421840891967",
            "1555182440593621082",
        ):
            emoji_response = client.get(
                f"/static/images/emoji_images/{emoji_id}.webp"
            )
            self.assertEqual(emoji_response.status_code, 200)
            emoji_response.close()
        self.assertEqual(registration.status_code, 302)
        self.assertEqual(registration.headers["Location"], "/battle?message_id=2")
        self.assertIn("2試合を読み取りました", redirected_form.get_data(as_text=True))

    def test_index_shows_only_the_latest_50_battles(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript("""
                    CREATE TABLE pokemon (id INTEGER PRIMARY KEY, name TEXT);
                    CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT);
                    CREATE TABLE battle_messages (
                        id INTEGER PRIMARY KEY,
                        discord_message_id TEXT,
                        season_id INTEGER,
                        battle_date TEXT,
                        raw_content TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE battles (
                        id INTEGER PRIMARY KEY,
                        battle_message_id INTEGER,
                        battle_number INTEGER,
                        player1_pokemon_id INTEGER,
                        player2_pokemon_id INTEGER,
                        player1_id INTEGER,
                        player2_id INTEGER,
                        first_player INTEGER,
                        player1_result TEXT,
                        player2_result TEXT
                    );
                """)
                conn.executemany(
                    "INSERT INTO pokemon (id, name) VALUES (?, ?)",
                    [(1, "フシギダネ"), (2, "ヒトカゲ")],
                )
                conn.execute(
                    "INSERT INTO battle_messages (id, battle_date) VALUES (1, '2026-10-20')"
                )
                conn.executemany(
                    """
                    INSERT INTO battles (
                        id, battle_message_id, battle_number,
                        player1_pokemon_id, player2_pokemon_id, first_player,
                        player1_result, player2_result
                    )
                    VALUES (?, 1, ?, 1, 2, 1, 'win', 'lose')
                    """,
                    [(battle_id, battle_id) for battle_id in range(1, 53)],
                )

            with patch.object(insert_app, "DB_PATH", db_path):
                response = insert_app.app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        table = response.get_data(as_text=True).split('<table border="1">', 1)[1]
        rows = re.findall(r"<tr>(.*?)</tr>", table, re.DOTALL)
        battle_ids = [
            re.search(r"<td>\s*(\d+)\s*</td>", row, re.DOTALL).group(1)
            for row in rows[1:]
        ]
        self.assertEqual(battle_ids, [str(battle_id) for battle_id in range(3, 53)])

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
                    "battle_type": "normal",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
