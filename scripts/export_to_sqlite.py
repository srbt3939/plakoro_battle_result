"""Reverse export DynamoDB -> SQLite + sidecar JSON for rollback (手順8 §5-2 / 追加レビューC-4)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.battle_matches import calculate_series_result  # noqa: E402
from scripts.check_integrity import _scan_table  # noqa: E402


SQLITE_SCHEMA = """
CREATE TABLE seasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    description TEXT
);
CREATE TABLE players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE pokemon (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    emoji_id TEXT UNIQUE,
    type1 TEXT NOT NULL,
    type2 TEXT,
    weaknesses TEXT NOT NULL
);
CREATE TABLE battle_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_message_id TEXT UNIQUE,
    season_id INTEGER,
    battle_date TEXT,
    raw_content TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (season_id) REFERENCES seasons(id)
);
CREATE TABLE battle_matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_type TEXT NOT NULL,
    tournament_name TEXT,
    opponent TEXT,
    match_date TEXT NOT NULL,
    match_result TEXT,
    player_score INTEGER NOT NULL,
    opponent_score INTEGER NOT NULL,
    season_id INTEGER NOT NULL,
    player_id INTEGER,
    battle_message_id INTEGER UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY (season_id) REFERENCES seasons(id),
    FOREIGN KEY (player_id) REFERENCES players(id),
    FOREIGN KEY (battle_message_id) REFERENCES battle_messages(id)
);
CREATE TABLE battles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_message_id INTEGER NOT NULL,
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
    UNIQUE (match_id, game_number),
    FOREIGN KEY (battle_message_id) REFERENCES battle_messages(id),
    FOREIGN KEY (player1_id) REFERENCES players(id),
    FOREIGN KEY (player2_id) REFERENCES players(id),
    FOREIGN KEY (player1_pokemon_id) REFERENCES pokemon(id),
    FOREIGN KEY (player2_pokemon_id) REFERENCES pokemon(id),
    FOREIGN KEY (match_id) REFERENCES battle_matches(match_id)
);
"""


def export_dynamodb_to_sqlite(
    dynamodb_client: Any,
    table_prefix: str,
    output_sqlite_path: Path,
    output_sidecar_path: Path,
) -> dict[str, Any]:
    if output_sqlite_path.exists():
        raise FileExistsError(f"Output SQLite already exists: {output_sqlite_path}")
    if output_sidecar_path.exists():
        raise FileExistsError(f"Output sidecar JSON already exists: {output_sidecar_path}")

    seasons = sorted(_scan_table(dynamodb_client, f"{table_prefix}Seasons"), key=lambda x: int(x["seasonId"]))
    players = sorted(_scan_table(dynamodb_client, f"{table_prefix}Players"), key=lambda x: int(x["playerId"]))
    discord_accounts = _scan_table(dynamodb_client, f"{table_prefix}DiscordAccounts")
    pokemon = sorted(_scan_table(dynamodb_client, f"{table_prefix}Pokemon"), key=lambda x: int(x["pokemonId"]))
    messages = sorted(_scan_table(dynamodb_client, f"{table_prefix}BattleMessages"), key=lambda x: int(x["messageId"]))
    matches = sorted(_scan_table(dynamodb_client, f"{table_prefix}BattleMatches"), key=lambda x: int(x["matchId"]))
    battles = sorted(_scan_table(dynamodb_client, f"{table_prefix}Battles"), key=lambda x: int(x["battleId"]))
    reset_requests = _scan_table(dynamodb_client, f"{table_prefix}ResetRequests")
    reset_history = _scan_table(dynamodb_client, f"{table_prefix}ResetHistory")
    audit_logs = _scan_table(dynamodb_client, f"{table_prefix}BattleAuditLogs")

    active_matches = [m for m in matches if not m.get("isDeleted")]
    deleted_matches = [m for m in matches if m.get("isDeleted")]
    active_battles = [b for b in battles if not b.get("isDeleted")]
    deleted_battles = [b for b in battles if b.get("isDeleted")]

    primary_by_player: dict[int, str] = {}
    accounts_by_player: dict[int, list[dict[str, Any]]] = {}
    for acc in discord_accounts:
        accounts_by_player.setdefault(int(acc["playerId"]), []).append(acc)

    for p in players:
        pid = int(p["playerId"])
        if p.get("discordUserId"):
            primary_by_player[pid] = p["discordUserId"]
        elif accounts_by_player.get(pid):
            primary_by_player[pid] = accounts_by_player[pid][0]["discordUserId"]
        else:
            raise RuntimeError(f"Player#{pid} has no Discord User ID.")

    output_sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output_sqlite_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SQLITE_SCHEMA)

        for s in seasons:
            conn.execute(
                "INSERT INTO seasons (id, name, start_date, end_date, description) VALUES (?, ?, ?, ?, ?)",
                (
                    int(s["seasonId"]),
                    s["name"],
                    s.get("startDate"),
                    s.get("endDate"),
                    s.get("description"),
                ),
            )

        for p in players:
            pid = int(p["playerId"])
            conn.execute(
                "INSERT INTO players (id, discord_user_id, name, created_at) VALUES (?, ?, ?, ?)",
                (
                    pid,
                    primary_by_player[pid],
                    p["name"],
                    p.get("createdAt"),
                ),
            )

        for pk in pokemon:
            conn.execute(
                "INSERT INTO pokemon (id, name, emoji_id, type1, type2, weaknesses) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    int(pk["pokemonId"]),
                    pk["name"],
                    pk.get("emojiId"),
                    pk["type1"],
                    pk.get("type2"),
                    json.dumps(pk.get("weaknesses", []), ensure_ascii=False),
                ),
            )

        for m in messages:
            conn.execute(
                """
                INSERT INTO battle_messages (id, discord_message_id, season_id, battle_date, raw_content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(m["messageId"]),
                    m["discordMessageId"],
                    int(m["seasonId"]),
                    m.get("battleDate", ""),
                    m.get("rawContent", ""),
                    m["createdAt"],
                ),
            )

        for mt in active_matches:
            conn.execute(
                """
                INSERT INTO battle_matches (
                    match_id, match_type, tournament_name, opponent, match_date,
                    match_result, player_score, opponent_score, season_id,
                    player_id, battle_message_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(mt["matchId"]),
                    mt["matchType"],
                    mt.get("tournamentName"),
                    mt.get("opponent"),
                    mt["matchDate"],
                    mt.get("matchResult"),
                    int(mt["playerScore"]),
                    int(mt["opponentScore"]),
                    int(mt["seasonId"]),
                    int(mt["playerId"]) if mt.get("playerId") is not None else None,
                    int(mt["messageId"]),
                    mt["createdAt"],
                ),
            )

        for b in active_battles:
            conn.execute(
                """
                INSERT INTO battles (
                    id, battle_message_id, battle_number, player1_id, player2_id,
                    player1_pokemon_id, player2_pokemon_id, first_player,
                    player1_result, player2_result, battle_type, match_id, game_number
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(b["battleId"]),
                    int(b["messageId"]),
                    int(b["battleNumber"]),
                    int(b["player1Id"]) if b.get("player1Id") is not None else None,
                    int(b["player2Id"]) if b.get("player2Id") is not None else None,
                    int(b["player1PokemonId"]),
                    int(b["player2PokemonId"]),
                    int(b["firstPlayer"]) if b.get("firstPlayer") is not None else None,
                    b["player1Result"],
                    b["player2Result"],
                    b.get("battleType") or "normal",
                    int(b["matchId"]) if b.get("matchId") is not None else None,
                    int(b["gameNumber"]) if b.get("gameNumber") is not None else None,
                ),
            )
        conn.commit()

    # Sidecar JSON holds attributes that have no representation in SQLite
    players_meta = {
        str(int(p["playerId"])): {
            "isPublic": bool(p.get("isPublic", True)),
            "resetAt": p.get("resetAt"),
            "resetAfterBattleId": p.get("resetAfterBattleId"),
        }
        for p in players
    }
    extra_discord_accounts = [
        acc
        for acc in discord_accounts
        if acc["discordUserId"] != primary_by_player.get(int(acc["playerId"]))
    ]
    sidecar_payload = {
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "tablePrefix": table_prefix,
        "players_meta": players_meta,
        "extra_discord_accounts": extra_discord_accounts,
        "deleted_battles": deleted_battles,
        "deleted_matches": deleted_matches,
        "reset_requests": reset_requests,
        "reset_history": reset_history,
        "battle_audit_logs": audit_logs,
    }
    output_sidecar_path.write_text(
        json.dumps(sidecar_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Reverse verification
    errors: list[str] = []
    with sqlite3.connect(output_sqlite_path) as conn:
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            errors.append(f"SQLite foreign_key_check violations: {len(fk_violations)}")

        expected_sqlite_counts = {
            "seasons": len(seasons),
            "players": len(players),
            "pokemon": len(pokemon),
            "battle_messages": len(messages),
            "battle_matches": len(active_matches),
            "battles": len(active_battles),
        }
        for tbl, exp_cnt in expected_sqlite_counts.items():
            act_cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            if act_cnt != exp_cnt:
                errors.append(f"{tbl}: count mismatch ({act_cnt} != {exp_cnt})")

        sql_battle_ids = {r[0] for r in conn.execute("SELECT id FROM battles").fetchall()}
        if sql_battle_ids != {int(b["battleId"]) for b in active_battles}:
            errors.append("battles ID set mismatch")

        for m in active_matches:
            mid = int(m["matchId"])
            g_results = [
                r[0]
                for r in conn.execute(
                    "SELECT player1_result FROM battles WHERE match_id = ? ORDER BY game_number",
                    (mid,),
                ).fetchall()
            ]
            res, ps, os_ = calculate_series_result(g_results)
            if m.get("matchResult") != res or int(m["playerScore"]) != ps or int(m["opponentScore"]) != os_:
                errors.append(f"Match#{mid}: reverse-verified BO3 score mismatch")

    if errors:
        raise RuntimeError(f"Reverse export verification failed: {errors}")

    return {
        "ok": True,
        "sqlite_path": str(output_sqlite_path),
        "sidecar_path": str(output_sidecar_path),
        "counts": {
            "seasons": len(seasons),
            "players": len(players),
            "pokemon": len(pokemon),
            "battle_messages": len(messages),
            "battle_matches": len(active_matches),
            "battles": len(active_battles),
            "extra_discord_accounts": len(extra_discord_accounts),
            "deleted_battles_in_sidecar": len(deleted_battles),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export DynamoDB back to SQLite + sidecar JSON.")
    parser.add_argument("--prefix", default=os.environ.get("PLAKORO_DYNAMODB_TABLE_PREFIX", "plakoro_prod_"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-northeast-1"))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    parser.add_argument("--output-db", type=Path, default=BASE_DIR / f"pokemon_rollback_{ts}.db")
    parser.add_argument("--output-sidecar", type=Path, default=BASE_DIR / f"rollback_sidecar_{ts}.json")
    args = parser.parse_args(argv)

    import boto3

    session = (
        boto3.Session(profile_name=args.profile, region_name=args.region)
        if args.profile
        else boto3.Session(region_name=args.region)
    )
    client = session.client("dynamodb")
    report = export_dynamodb_to_sqlite(client, args.prefix, args.output_db, args.output_sidecar)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
