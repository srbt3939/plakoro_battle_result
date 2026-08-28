# python
import sqlite3
import json
from pathlib import Path


# =========================
# 設定
# =========================

DB_PATH = "pokemon.db"
OUTPUT_DIR = Path("data")


# =========================
# DB接続
# =========================

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# =========================
# JSON保存
# =========================

def save_json(filename, data):

    OUTPUT_DIR.mkdir(exist_ok=True)

    path = OUTPUT_DIR / filename

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=4
        )

    print(f"出力: {path}")


# =========================
# ① 全Battleデータ
# =========================

def export_battles(conn):

    rows = conn.execute("""
        SELECT
            b.id,
            b.battle_message_id,
            b.battle_number,

            b.player1_id,
            b.player2_id,

            b.player1_pokemon_id,
            p1.name AS player1_pokemon,

            b.player2_pokemon_id,
            p2.name AS player2_pokemon,

            b.first_player,

            b.player1_result,
            b.player2_result

        FROM battles AS b

        JOIN pokemon AS p1
            ON b.player1_pokemon_id = p1.id

        JOIN pokemon AS p2
            ON b.player2_pokemon_id = p2.id

        ORDER BY
            b.battle_message_id,
            b.battle_number
    """).fetchall()


    battles = []

    for row in rows:

        battles.append({
            "id": row["id"],

            "battle_message_id": row["battle_message_id"],
            "battle_number": row["battle_number"],

            "player1": {
                "id": row["player1_id"],
                "pokemon_id": row["player1_pokemon_id"],
                "pokemon": row["player1_pokemon"],
                "result": row["player1_result"]
            },

            "player2": {
                "id": row["player2_id"],
                "pokemon_id": row["player2_pokemon_id"],
                "pokemon": row["player2_pokemon"],
                "result": row["player2_result"]
            },

            "first_player": row["first_player"]
        })


    save_json(
        "battles.json",
        {
            "battles": battles
        }
    )


# =========================
# ② ポケモン使用率
# =========================

def export_usage(conn):

    rows = conn.execute("""
        SELECT
            p.id,
            p.name,
            COUNT(*) AS usage_count

        FROM pokemon AS p

        JOIN battles AS b
            ON p.id = b.player1_pokemon_id
            OR p.id = b.player2_pokemon_id

        GROUP BY
            p.id,
            p.name

        ORDER BY
            usage_count DESC,
            p.id
    """).fetchall()


    total_usage = sum(
        row["usage_count"]
        for row in rows
    )


    pokemon = []

    for rank, row in enumerate(rows, start=1):

        usage_count = row["usage_count"]

        usage_rate = (
            usage_count / total_usage * 100
            if total_usage > 0
            else 0
        )


        pokemon.append({

            "rank": rank,

            "pokemon_id": row["id"],

            "name": row["name"],

            "usage_count": usage_count,

            "usage_rate": round(
                usage_rate,
                1
            )

        })


    save_json(
        "usage.json",
        {
            "total_usage": total_usage,
            "pokemon": pokemon
        }
    )


# =========================
# ③ ポケモン戦績
# =========================

def export_pokemon_stats(conn):

    pokemon_rows = conn.execute("""
        SELECT
            id,
            name
        FROM pokemon
        ORDER BY id
    """).fetchall()


    stats = []


    for pokemon in pokemon_rows:

        pokemon_id = pokemon["id"]


        # -------------------------
        # 総合戦績
        # -------------------------

        total = conn.execute("""
            SELECT
                COUNT(*) AS battles,

                SUM(
                    CASE
                        WHEN player1_pokemon_id = ?
                             AND player1_result = 'win'
                        THEN 1

                        WHEN player2_pokemon_id = ?
                             AND player2_result = 'win'
                        THEN 1

                        ELSE 0
                    END
                ) AS wins

            FROM battles

            WHERE
                player1_pokemon_id = ?
                OR player2_pokemon_id = ?
        """, (
            pokemon_id,
            pokemon_id,
            pokemon_id,
            pokemon_id
        )).fetchone()


        total_battles = total["battles"]
        total_wins = total["wins"] or 0
        total_losses = total_battles - total_wins


        # -------------------------
        # 先攻
        # -------------------------

        first = conn.execute("""
            SELECT

                COUNT(*) AS battles,

                SUM(
                    CASE

                        WHEN
                            (first_player = 1
                             AND player1_pokemon_id = ?
                             AND player1_result = 'win')

                            OR

                            (first_player = 2
                             AND player2_pokemon_id = ?
                             AND player2_result = 'win')

                        THEN 1

                        ELSE 0

                    END
                ) AS wins

            FROM battles

            WHERE

                (
                    first_player = 1
                    AND player1_pokemon_id = ?
                )

                OR

                (
                    first_player = 2
                    AND player2_pokemon_id = ?
                )
        """, (
            pokemon_id,
            pokemon_id,
            pokemon_id,
            pokemon_id
        )).fetchone()


        first_battles = first["battles"]
        first_wins = first["wins"] or 0
        first_losses = first_battles - first_wins


        # -------------------------
        # 後攻
        # -------------------------

        second = conn.execute("""
            SELECT

                COUNT(*) AS battles,

                SUM(
                    CASE

                        WHEN
                            (first_player = 1
                             AND player2_pokemon_id = ?
                             AND player2_result = 'win')

                            OR

                            (first_player = 2
                             AND player1_pokemon_id = ?
                             AND player1_result = 'win')

                        THEN 1

                        ELSE 0

                    END
                ) AS wins

            FROM battles

            WHERE

                (
                    first_player = 1
                    AND player2_pokemon_id = ?
                )

                OR

                (
                    first_player = 2
                    AND player1_pokemon_id = ?
                )
        """, (
            pokemon_id,
            pokemon_id,
            pokemon_id,
            pokemon_id
        )).fetchone()


        second_battles = second["battles"]
        second_wins = second["wins"] or 0
        second_losses = second_battles - second_wins


        # -------------------------
        # 不明
        # -------------------------

        unknown = conn.execute("""
            SELECT
                COUNT(*) AS battles,

                SUM(
                    CASE

                        WHEN
                            player1_pokemon_id = ?
                            AND player1_result = 'win'

                        THEN 1

                        WHEN
                            player2_pokemon_id = ?
                            AND player2_result = 'win'

                        THEN 1

                        ELSE 0

                    END
                ) AS wins

            FROM battles

            WHERE

                first_player IS NULL

                AND

                (
                    player1_pokemon_id = ?
                    OR player2_pokemon_id = ?
                )
        """, (
            pokemon_id,
            pokemon_id,
            pokemon_id,
            pokemon_id
        )).fetchone()


        unknown_battles = unknown["battles"]
        unknown_wins = unknown["wins"] or 0
        unknown_losses = unknown_battles - unknown_wins


        # -------------------------
        # 勝率計算
        # -------------------------

        def win_rate(wins, battles):

            if battles == 0:
                return 0

            return round(
                wins / battles * 100,
                1
            )


        stats.append({

            "pokemon_id": pokemon_id,

            "name": pokemon["name"],

            "total": {

                "battles": total_battles,

                "wins": total_wins,

                "losses": total_losses,

                "win_rate": win_rate(
                    total_wins,
                    total_battles
                )
            },

            "first_player": {

                "battles": first_battles,

                "wins": first_wins,

                "losses": first_losses,

                "win_rate": win_rate(
                    first_wins,
                    first_battles
                )
            },

            "second_player": {

                "battles": second_battles,

                "wins": second_wins,

                "losses": second_losses,

                "win_rate": win_rate(
                    second_wins,
                    second_battles
                )
            },

            "unknown": {

                "battles": unknown_battles,

                "wins": unknown_wins,

                "losses": unknown_losses,

                "win_rate": win_rate(
                    unknown_wins,
                    unknown_battles
                )
            }

        })


    save_json(
        "pokemon_stats.json",
        {
            "pokemon": stats
        }
    )


# =========================
# ④ ポケモン同士の対戦成績
# =========================

def export_matchups(conn):

    rows = conn.execute("""
        SELECT

            b.player1_pokemon_id AS p1_id,
            p1.name AS p1_name,

            b.player2_pokemon_id AS p2_id,
            p2.name AS p2_name,

            b.player1_result,
            b.player2_result

        FROM battles AS b

        JOIN pokemon AS p1
            ON b.player1_pokemon_id = p1.id

        JOIN pokemon AS p2
            ON b.player2_pokemon_id = p2.id
    """).fetchall()


    matchups = {}


    for row in rows:

        p1_id = row["p1_id"]
        p2_id = row["p2_id"]


        # -------------------------
        # 組み合わせを正規化
        # -------------------------

        if p1_id < p2_id:

            pokemon1_id = p1_id
            pokemon1_name = row["p1_name"]

            pokemon2_id = p2_id
            pokemon2_name = row["p2_name"]

            pokemon1_result = row["player1_result"]
            pokemon2_result = row["player2_result"]

        else:

            pokemon1_id = p2_id
            pokemon1_name = row["p2_name"]

            pokemon2_id = p1_id
            pokemon2_name = row["p1_name"]

            pokemon1_result = row["player2_result"]
            pokemon2_result = row["player1_result"]


        key = (
            pokemon1_id,
            pokemon2_id
        )


        if key not in matchups:

            matchups[key] = {

                "pokemon1": {
                    "id": pokemon1_id,
                    "name": pokemon1_name
                },

                "pokemon2": {
                    "id": pokemon2_id,
                    "name": pokemon2_name
                },

                "battles": 0,

                "pokemon1_wins": 0,

                "pokemon2_wins": 0

            }


        matchups[key]["battles"] += 1


        if pokemon1_result == "win":

            matchups[key]["pokemon1_wins"] += 1

        elif pokemon2_result == "win":

            matchups[key]["pokemon2_wins"] += 1


    # -------------------------
    # 勝率を追加
    # -------------------------

    result = []


    for matchup in matchups.values():

        battles = matchup["battles"]

        pokemon1_wins = matchup["pokemon1_wins"]
        pokemon2_wins = matchup["pokemon2_wins"]


        result.append({

            "pokemon1": matchup["pokemon1"],

            "pokemon2": matchup["pokemon2"],

            "battles": battles,

            "pokemon1_wins": pokemon1_wins,

            "pokemon2_wins": pokemon2_wins,

            "pokemon1_win_rate": round(
                pokemon1_wins / battles * 100,
                1
            ),

            "pokemon2_win_rate": round(
                pokemon2_wins / battles * 100,
                1
            )

        })


    # 試合数の多い順
    result.sort(
        key=lambda x: x["battles"],
        reverse=True
    )


    save_json(
        "matchups.json",
        {
            "matchups": result
        }
    )


# =========================
# メイン
# =========================

def main():

    print("JSON出力を開始します。")
    print()


    conn = get_connection()


    try:

        export_battles(conn)

        export_usage(conn)

        export_pokemon_stats(conn)

        export_matchups(conn)

    finally:

        conn.close()


    print()
    print("JSON出力が完了しました。")


if __name__ == "__main__":
    main()
