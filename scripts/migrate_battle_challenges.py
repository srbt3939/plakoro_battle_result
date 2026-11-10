import argparse
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "pokemon.db"


def migrate_database(db_path: Path = DEFAULT_DB_PATH) -> Path:
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = db_path.with_name(f"{db_path.name}.backup-{timestamp}")
    source = sqlite3.connect(db_path)
    try:
        backup = sqlite3.connect(backup_path)
        try:
            source.backup(backup)
        finally:
            backup.close()
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    finally:
        source.close()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS battle_matches (
                match_id INTEGER PRIMARY KEY,
                match_type TEXT NOT NULL DEFAULT 'battle_challenge',
                tournament_name TEXT,
                opponent TEXT,
                match_date TEXT NOT NULL,
                match_result TEXT,
                player_score INTEGER NOT NULL DEFAULT 0,
                opponent_score INTEGER NOT NULL DEFAULT 0,
                season_id INTEGER NOT NULL,
                player_id INTEGER,
                battle_message_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (season_id) REFERENCES seasons(id),
                FOREIGN KEY (player_id) REFERENCES players(id),
                FOREIGN KEY (battle_message_id) REFERENCES battle_messages(id)
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_battle_matches_message_id
            ON battle_matches(battle_message_id)
        """)

        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(battles)").fetchall()
        }
        required = {"match_id", "game_number"}
        if not required.issubset(columns):
            battle_type = (
                "battle_type" if "battle_type" in columns else "'normal' AS battle_type"
            )
            conn.execute("""
                CREATE TABLE battles_new (
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
                    FOREIGN KEY (battle_message_id) REFERENCES battle_messages(id),
                    FOREIGN KEY (player1_pokemon_id) REFERENCES pokemon(id),
                    FOREIGN KEY (player2_pokemon_id) REFERENCES pokemon(id),
                    FOREIGN KEY (match_id) REFERENCES battle_matches(match_id),
                    UNIQUE (battle_message_id, battle_number),
                    UNIQUE (match_id, game_number)
                )
            """)
            conn.execute(f"""
                INSERT INTO battles_new (
                    id, battle_message_id, battle_number,
                    player1_id, player2_id, player1_pokemon_id, player2_pokemon_id,
                    first_player, player1_result, player2_result, battle_type,
                    match_id, game_number
                )
                SELECT
                    id, battle_message_id, battle_number,
                    player1_id, player2_id, player1_pokemon_id, player2_pokemon_id,
                    first_player, player1_result, player2_result, {battle_type},
                    NULL, NULL
                FROM battles
            """)
            conn.execute("DROP TABLE battles")
            conn.execute("ALTER TABLE battles_new RENAME TO battles")

        conn.commit()
        return backup_path
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Back up and migrate the database for Battle Challenge BO3."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    backup_path = migrate_database(args.db)
    print(f"Migration complete. Backup: {backup_path}")


if __name__ == "__main__":
    main()
