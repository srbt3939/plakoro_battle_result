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

    return render_template(
        "review_index.html",
        stats=result,
        total_usage=total_usage
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )