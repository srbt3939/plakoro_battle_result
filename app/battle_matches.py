import sqlite3
from datetime import datetime, timezone

if __package__:
    from .battle_parser import BATTLE_CHALLENGE_PATTERN, parse_discord_message
else:
    from battle_parser import BATTLE_CHALLENGE_PATTERN, parse_discord_message


def is_battle_challenge_message(content: str | None) -> bool:
    return bool(content and BATTLE_CHALLENGE_PATTERN.search(content))


def tournament_name_from_message(content: str | None) -> str:
    if not content:
        return ""
    for line in content.splitlines():
        stripped = line.strip()
        if BATTLE_CHALLENGE_PATTERN.search(stripped):
            return stripped
    return ""


def parse_tournament_games(content: str) -> list[dict]:
    if not is_battle_challenge_message(content):
        return []
    return parse_discord_message(content)


def calculate_series_result(results: list[str]) -> tuple[str | None, int, int]:
    player_wins = results.count("win")
    opponent_wins = results.count("lose")
    if player_wins >= 2:
        return "win", player_wins, opponent_wins
    if opponent_wins >= 2:
        return "lose", player_wins, opponent_wins
    return None, player_wins, opponent_wins


def save_battle_challenge_message(
    db_path: str,
    message_id: int | str,
    content: str,
    created_at: str | None = None,
) -> int | None:
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    message_date = created_at[:10]
    with sqlite3.connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM battle_messages WHERE discord_message_id = ?",
            (str(message_id),),
        ).fetchone()
        if existing:
            return None
        cursor = conn.execute("""
            INSERT INTO battle_messages (
                discord_message_id, battle_date, raw_content, created_at
            )
            VALUES (?, ?, ?, ?)
        """, (str(message_id), message_date, content, created_at))
        return cursor.lastrowid


def create_battle_match(
    conn: sqlite3.Connection,
    *,
    battle_message_id: int,
    season_id: int,
    tournament_name: str | None,
    opponent: str | None,
    match_date: str,
    player_id: int | None,
    games: list[dict],
) -> int:
    if not 1 <= len(games) <= 3:
        raise ValueError("A Battle Challenge series must contain 1 to 3 games.")

    results = [game["player1_result"] for game in games]
    match_result, player_score, opponent_score = calculate_series_result(results)
    if player_score >= 2 or opponent_score >= 2:
        decisive_game = next(
            index
            for index in range(len(results))
            if results[:index + 1].count("win") >= 2
            or results[:index + 1].count("lose") >= 2
        )
        if decisive_game != len(games) - 1:
            raise ValueError("Games cannot be added after the series is decided.")

    cursor = conn.execute("""
        INSERT INTO battle_matches (
            match_type, tournament_name, opponent, match_date, match_result,
            player_score, opponent_score, season_id, player_id,
            battle_message_id, created_at
        )
        VALUES ('battle_challenge', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tournament_name or None,
        opponent or None,
        match_date,
        match_result,
        player_score,
        opponent_score,
        season_id,
        player_id,
        battle_message_id,
        datetime.now(timezone.utc).isoformat(),
    ))
    match_id = cursor.lastrowid

    next_battle_number = conn.execute("""
        SELECT COALESCE(MAX(battle_number), 0) + 1
        FROM battles
        WHERE battle_message_id = ?
    """, (battle_message_id,)).fetchone()[0]

    for game_number, game in enumerate(games, start=1):
        result = game["player1_result"]
        conn.execute("""
            INSERT INTO battles (
                battle_message_id, battle_number, player1_id, player2_id,
                player1_pokemon_id, player2_pokemon_id, first_player,
                player1_result, player2_result, battle_type, match_id, game_number
            )
            VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, 'challenge', ?, ?)
        """, (
            battle_message_id,
            next_battle_number + game_number - 1,
            player_id,
            game["player1_pokemon_id"],
            game["player2_pokemon_id"],
            game["first_player"],
            result,
            {"win": "lose", "lose": "win"}[result],
            match_id,
            game_number,
        ))

    return match_id
