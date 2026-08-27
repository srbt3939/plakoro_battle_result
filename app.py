from flask import Flask, render_template, request, redirect
import sqlite3


app = Flask(__name__)

DB_PATH = "pokemon.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# =========================
# Battle Message登録
# =========================

@app.route("/message", methods=["GET", "POST"])
def message():

    if request.method == "POST":

        discord_message_id = request.form["discord_message_id"]

        battle_date = request.form["battle_date"]

        raw_content = request.form["raw_content"]
        created_at = request.form["created_at"]

        conn = get_connection()

        conn.execute("""
            INSERT INTO battle_messages (
                discord_message_id,
                battle_date,
                raw_content,
                created_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            discord_message_id,
            battle_date,
            raw_content,
            created_at
        ))

        conn.commit()
        conn.close()

        return redirect("/")

    return render_template("message_form.html")


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

    # Battle Message一覧
    messages = conn.execute("""
        SELECT *
        FROM battle_messages
        ORDER BY id DESC
    """).fetchall()

    if request.method == "POST":

        battle_message_id = request.form["battle_message_id"]

        player1_pokemon_id = request.form["player1_pokemon_id"]
        player2_pokemon_id = request.form["player2_pokemon_id"]

        first_player = request.form["first_player"]

        player1_result = request.form["player1_result"]
        player2_result = request.form["player2_result"]

        conn.execute("""
            INSERT INTO battles (
                battle_message_id,
                battle_number,
                player1_pokemon_id,
                player2_pokemon_id,
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
                ?
            FROM battles
            WHERE battle_message_id = ?
        """, (
            battle_message_id,
            player1_pokemon_id,
            player2_pokemon_id,
            first_player,
            player1_result,
            player2_result,
            battle_message_id
        ))

        conn.commit()
        conn.close()

        return redirect("/")

    conn.close()

    return render_template(
        "battle_form.html",
        pokemon=pokemon,
        messages=messages
    )


# =========================
# トップ
# =========================

@app.route("/")
def index():

    conn = get_connection()

    battles = conn.execute("""
        SELECT
            battles.id,
            battles.battle_number,

            p1.name AS player1_pokemon,
            p2.name AS player2_pokemon,

            battles.first_player,

            battles.player1_result,
            battles.player2_result

        FROM battles

        JOIN pokemon AS p1
            ON battles.player1_pokemon_id = p1.id

        JOIN pokemon AS p2
            ON battles.player2_pokemon_id = p2.id

        ORDER BY battles.id 
    """).fetchall()

    conn.close()

    return render_template(
        "index.html",
        battles=battles
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )