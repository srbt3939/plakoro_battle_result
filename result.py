from flask import Flask, render_template
import sqlite3

app = Flask(__name__)

DB_PATH = "pokemon.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():

    conn = get_connection()

    # ポケモンごとの使用回数
    stats = conn.execute("""
        SELECT
            pokemon.id,
            pokemon.name,
            COUNT(*) AS usage_count
        FROM pokemon

        JOIN battles
            ON pokemon.id = battles.player1_pokemon_id
            OR pokemon.id = battles.player2_pokemon_id

        GROUP BY pokemon.id

        ORDER BY usage_count DESC
    """).fetchall()

    turn_order_stats = conn.execute("""
        SELECT
            COUNT(*) AS total_games,
            COALESCE(SUM(CASE
                WHEN first_player = 1 AND player1_result = 'win' THEN 1
                WHEN first_player = 2 AND player2_result = 'win' THEN 1
                ELSE 0
            END), 0) AS first_player_wins,
            COALESCE(SUM(CASE
                WHEN first_player = 2 AND player1_result = 'win' THEN 1
                WHEN first_player = 1 AND player2_result = 'win' THEN 1
                ELSE 0
            END), 0) AS second_player_wins
        FROM battles
    """).fetchone()

    conn.close()

    # 全ポケモン使用数
    total_usage = sum(
        row["usage_count"]
        for row in stats
    )

    # 使用率を計算
    result = []

    for row in stats:

        usage_rate = (
            row["usage_count"] / total_usage * 100
            if total_usage > 0
            else 0
        )

        result.append({
            "id": row["id"],
            "name": row["name"],
            "usage_count": row["usage_count"],
            "usage_rate": round(usage_rate, 1)
        })

    total_games = turn_order_stats["total_games"]

    turn_order_result = {
        "first_player_games": total_games,
        "first_player_wins": turn_order_stats["first_player_wins"],
        "first_player_win_rate": round(
            turn_order_stats["first_player_wins"] / total_games * 100,
            1
        ) if total_games > 0 else 0,
        "second_player_games": total_games,
        "second_player_wins": turn_order_stats["second_player_wins"],
        "second_player_win_rate": round(
            turn_order_stats["second_player_wins"] / total_games * 100,
            1
        ) if total_games > 0 else 0
    }

    return render_template(
        "review_index.html",
        stats=result,
        total_usage=total_usage,
        turn_order_result=turn_order_result
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )