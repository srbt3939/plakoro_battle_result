import json
import re
from flask import Flask, abort, render_template, request, redirect, url_for
import sqlite3
from pathlib import Path

if __package__:
    from .battle_parser import parse_discord_message
else:
    from battle_parser import parse_discord_message


BASE_DIR = Path(__file__).resolve().parent.parent
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "app" / "templates"),
    static_folder=str(BASE_DIR / "docs" / "assets"),
    static_url_path="/static",
)

DB_PATH = BASE_DIR / "data" / "pokemon.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# =========================
# Battle Message登録
# =========================

@app.route("/message", methods=["GET", "POST"])
def message():

    conn = get_connection()

    seasons = conn.execute("""
        SELECT *
        FROM seasons
        ORDER BY id
    """).fetchall()

    if request.method == "POST":

        discord_message_id = request.form["discord_message_id"]
        season_id = request.form["season_id"]

        battle_date = request.form["battle_date"]

        raw_content = request.form["raw_content"]
        created_at = request.form["created_at"]

        cursor = conn.execute("""
            INSERT INTO battle_messages (
                discord_message_id,
                season_id,
                battle_date,
                raw_content,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            discord_message_id,
            season_id,
            battle_date,
            raw_content,
            created_at
        ))

        message_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return redirect(url_for("battle", message_id=message_id))

    conn.close()

    return render_template(
        "message_form.html",
        seasons=seasons
    )


# =========================
# Battle登録
# =========================

@app.route("/battle", methods=["GET", "POST"])
def battle():

    conn = get_connection()

    # ポケモン一覧
    pokemon = conn.execute("""
        SELECT *
        FROM pokemon
        ORDER BY id
    """).fetchall()
    emoji_data = json.loads(
        (BASE_DIR / "docs" / "data" / "emoji.json").read_text(encoding="utf-8")
    )
    pokemon_emoji_ids = {
        pokemon_data["id"]: re.fullmatch(
            r"<a?:[^:>]+:(\d+)>",
            pokemon_data["emoji"],
        ).group(1)
        for pokemon_data in emoji_data["pokemon"]
    }
    battle_symbol_emoji_ids = {
        symbol: re.fullmatch(
            r"<a?:[^:>]+:(\d+)>",
            emoji_data["symbols"][symbol],
        ).group(1)
        for symbol in ("first", "second", "win", "lose")
    }

    # Battle Message一覧
    messages = conn.execute("""
        SELECT *
        FROM battle_messages
        ORDER BY id DESC
    """).fetchall()

    # Player一覧
    players = conn.execute("""
        SELECT *
        FROM players
        ORDER BY id
    """).fetchall()

    if request.method == "POST":
        if request.form.get("action") == "load_message":
            message_id = request.form.get("battle_message_id", type=int)
            if message_id is None or not any(
                message["id"] == message_id for message in messages
            ):
                conn.close()
                abort(400)
            return redirect(url_for("battle", message_id=message_id))

        battle_message_id = request.form["battle_message_id"]
        player1_id = request.form["player1_id"] or None
        player2_id = request.form["player2_id"] or None

        player1_pokemon_ids = request.form.getlist("player1_pokemon_id")
        player2_pokemon_ids = request.form.getlist("player2_pokemon_id")
        first_players = request.form.getlist("first_player")
        player1_results = request.form.getlist("player1_result")

        battle_count = len(player1_pokemon_ids)
        if (
            battle_count == 0
            or len(player2_pokemon_ids) != battle_count
            or len(first_players) != battle_count
            or len(player1_results) != battle_count
            or any(result not in ("win", "lose") for result in player1_results)
            or any(first not in ("", "1", "2") for first in first_players)
        ):
            conn.close()
            abort(400)

        for player1_pokemon_id, player2_pokemon_id, first_player, player1_result in zip(
            player1_pokemon_ids,
            player2_pokemon_ids,
            first_players,
            player1_results
        ):
            player2_result = {"win": "lose", "lose": "win"}[player1_result]

            conn.execute("""
                INSERT INTO battles (
                    battle_message_id,
                    battle_number,
                    player1_pokemon_id,
                    player2_pokemon_id,
                    player1_id,
                    player2_id,
                    first_player,
                    player1_result,
                    player2_result
                )
                SELECT
                    ?,
                    COALESCE(MAX(battle_number), 0) + 1,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                FROM battles
                WHERE battle_message_id = ?
            """, (
                battle_message_id,
                player1_pokemon_id,
                player2_pokemon_id,
                player1_id,
                player2_id,
                first_player or None,
                player1_result,
                player2_result,
                battle_message_id
            ))

        conn.commit()
        conn.close()

        return redirect("/")

    selected_message_id = request.args.get("message_id", type=int)
    selected_message = next(
        (message for message in messages if message["id"] == selected_message_id),
        None,
    )
    initial_battles = []
    parsed_battle_count = 0

    if selected_message is not None:
        raw_content = selected_message["raw_content"] or ""
        parsed_battles = parse_discord_message(raw_content)
        parsed_battle_count = len(parsed_battles)
        player_names = re.split(
            r"\s*(?:vs|ｖｓ)\s*",
            next((line.strip() for line in raw_content.splitlines() if line.strip()), ""),
            maxsplit=1,
            flags=re.IGNORECASE,
        )
        players_by_name = {player["name"].casefold(): player["id"] for player in players}

        player1_id = players_by_name.get(player_names[0].casefold()) if len(player_names) == 2 else None
        player2_id = players_by_name.get(player_names[1].casefold()) if len(player_names) == 2 else None

        initial_battles = [
            {
                **battle,
                "player1_id": player1_id,
                "player2_id": player2_id,
            }
            for battle in parsed_battles
        ]

    if not initial_battles:
        initial_battles = [{
            "player1_pokemon_id": None,
            "player2_pokemon_id": None,
            "first_player": None,
            "player1_result": None,
            "player1_id": None,
            "player2_id": None,
        }]

    conn.close()

    return render_template(
        "battle_form.html",
        pokemon=pokemon,
        pokemon_emoji_ids=pokemon_emoji_ids,
        battle_symbol_emoji_ids=battle_symbol_emoji_ids,
        messages=messages,
        players=players,
        selected_message_id=selected_message_id,
        initial_battles=initial_battles,
        parsed_battle_count=parsed_battle_count,
        selected_message=selected_message,
    )


# =========================
# Player登録
# =========================

@app.route("/player", methods=["GET", "POST"])
def player():

    conn = get_connection()

    if request.method == "POST":

        discord_user_id = request.form["discord_user_id"]
        name = request.form["name"]
        created_at = request.form["created_at"]

        conn.execute("""
            INSERT INTO players (
                discord_user_id,
                name,
                created_at
            )
            VALUES (?, ?, ?)
        """, (
            discord_user_id,
            name,
            created_at
        ))

        conn.commit()
        conn.close()

        return redirect("/player")

    # 登録済みPlayer一覧
    players = conn.execute("""
        SELECT *
        FROM players
        ORDER BY id
    """).fetchall()

    conn.close()

    return render_template(
        "player_form.html",
        players=players
    )


# =========================
# トップ
# =========================

@app.route("/")
def index():

    conn = get_connection()

    # Battle一覧
    battles = conn.execute("""
        SELECT
            battles.id,
            battles.battle_message_id,
            battles.battle_number,

            p1.name AS player1_pokemon,
            p2.name AS player2_pokemon,

            player1.name AS player1_name,
            player2.name AS player2_name,

            battles.first_player,

            battles.player1_result,
            battles.player2_result

        FROM battles

        JOIN pokemon AS p1
            ON battles.player1_pokemon_id = p1.id

        JOIN pokemon AS p2
            ON battles.player2_pokemon_id = p2.id

        LEFT JOIN players AS player1
            ON battles.player1_id = player1.id

        LEFT JOIN players AS player2
            ON battles.player2_id = player2.id

        WHERE battles.id IN (
            SELECT id
            FROM battles
            ORDER BY id DESC
            LIMIT 50
        )

        ORDER BY battles.id
    """).fetchall()


    # 最後に登録したBattle Message
    latest_message = conn.execute("""
        SELECT *
        FROM battle_messages
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()


    conn.close()

    return render_template(
        "insert_index.html",
        battles=battles,
        latest_message=latest_message
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )