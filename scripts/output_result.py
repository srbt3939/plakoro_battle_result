import sqlite3
import json
from datetime import date, timedelta
from pathlib import Path


# =========================
# 設定
# =========================

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "pokemon.db"
OUTPUT_DIR = BASE_DIR / "docs" / "data"

# 集計対象のseason。新season開始時は SEASON_ALLOWED_POKEMON_IDS に
# そのseasonのポケモンID範囲を追加するだけでよい（ここは自動で追従する）。
# season_id ごとに docs/data/season_{id}/ 以下へ、
# 全season合算は docs/data/all/ 以下へ出力する。
SEASON_ALLOWED_POKEMON_IDS = {
    1: tuple(range(1, 13)),
    2: tuple(range(1, 16))
}

SEASONS_TO_EXPORT = list(SEASON_ALLOWED_POKEMON_IDS.keys()) + ["all"]

# 現在処理中のseason（configure_seasonで切り替える）
ACTIVE_SEASON_ID = None
ACTIVE_POKEMON_IDS = ()
ACTIVE_POKEMON_PLACEHOLDERS = ""


def resolve_pokemon_ids(season_id):
    """
    season_id: 通常のseason_id、または "all"

    "all" のときは SEASON_ALLOWED_POKEMON_IDS の全season分の和集合を返す。
    """

    if season_id == "all":

        all_ids = set()

        for ids in SEASON_ALLOWED_POKEMON_IDS.values():
            all_ids.update(ids)

        return tuple(sorted(all_ids))

    return SEASON_ALLOWED_POKEMON_IDS[season_id]


def configure_season(season_id):
    """
    以降のexport_*呼び出しとsave_jsonの出力先を、指定したseasonに切り替える。
    season_id: 通常のseason_id、または "all"
    """

    global ACTIVE_SEASON_ID, ACTIVE_POKEMON_IDS, ACTIVE_POKEMON_PLACEHOLDERS, OUTPUT_DIR

    ACTIVE_SEASON_ID = season_id
    ACTIVE_POKEMON_IDS = resolve_pokemon_ids(season_id)
    ACTIVE_POKEMON_PLACEHOLDERS = ", ".join("?" for _ in ACTIVE_POKEMON_IDS)

    folder_name = "all" if season_id == "all" else f"season_{season_id}"
    OUTPUT_DIR = BASE_DIR / "docs" / "data" / folder_name


def parse_weaknesses(value):
    if not value:
        return []

    try:
        weaknesses = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [value]

    return weaknesses if isinstance(weaknesses, list) else [weaknesses]


def pokemon_details(row):
    return {
        "type1": row["type1"],
        "type2": row["type2"],
        "weaknesses": parse_weaknesses(row["weaknesses"])
    }


# =========================
# season切り替え関連
# =========================

def get_season_filter_sql():
    """
    season絞り込みのSQL断片とパラメータを返す。
    全season合算モード ("all") のときは絞り込みなし（空文字・空リスト）を返す。
    """

    if ACTIVE_SEASON_ID == "all":
        return "", []

    return "AND bm.season_id = ?", [ACTIVE_SEASON_ID]


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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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

    season_clause, season_params = get_season_filter_sql()

    rows = conn.execute("""
        SELECT
            b.id,
            b.battle_message_id,
            b.battle_number,

            b.player1_id,
            pl1.name AS player1_name,

            b.player2_id,
            pl2.name AS player2_name,

            b.player1_pokemon_id,
            p1.name AS player1_pokemon,
            p1.type1 AS player1_type1,
            p1.type2 AS player1_type2,
            p1.weaknesses AS player1_weaknesses,

            b.player2_pokemon_id,
            p2.name AS player2_pokemon,
            p2.type1 AS player2_type1,
            p2.type2 AS player2_type2,
            p2.weaknesses AS player2_weaknesses,

            b.first_player,

            b.player1_result,
            b.player2_result,

            bm.season_id

        FROM battles AS b

        JOIN battle_messages AS bm
            ON b.battle_message_id = bm.id

        JOIN pokemon AS p1
            ON b.player1_pokemon_id = p1.id

        JOIN pokemon AS p2
            ON b.player2_pokemon_id = p2.id

        LEFT JOIN players AS pl1
            ON b.player1_id = pl1.id

        LEFT JOIN players AS pl2
            ON b.player2_id = pl2.id

        WHERE b.player1_pokemon_id IN ({ph})
            AND b.player2_pokemon_id IN ({ph})
            {season_clause}

        ORDER BY
            b.battle_message_id,
            b.battle_number
    """.format(ph=ACTIVE_POKEMON_PLACEHOLDERS, season_clause=season_clause), (
        *ACTIVE_POKEMON_IDS,
        *ACTIVE_POKEMON_IDS,
        *season_params
    )).fetchall()


    battles = []

    for row in rows:

        battles.append({
            "id": row["id"],

            "battle_message_id": row["battle_message_id"],
            "battle_number": row["battle_number"],

            "player1": {
                "id": row["player1_id"],
                "name": row["player1_name"],
                "pokemon_id": row["player1_pokemon_id"],
                "pokemon": row["player1_pokemon"],
                "type1": row["player1_type1"],
                "type2": row["player1_type2"],
                "weaknesses": parse_weaknesses(row["player1_weaknesses"]),
                "result": row["player1_result"]
            },

            "player2": {
                "id": row["player2_id"],
                "name": row["player2_name"],
                "pokemon_id": row["player2_pokemon_id"],
                "pokemon": row["player2_pokemon"],
                "type1": row["player2_type1"],
                "type2": row["player2_type2"],
                "weaknesses": parse_weaknesses(row["player2_weaknesses"]),
                "result": row["player2_result"]
            },

            "first_player": row["first_player"],

            # 全season合算モードでは、各battleが実際にどのseasonのものかを
            # season_idで区別できるようにしておく。
            "season_id": row["season_id"]
        })


    save_json(
        "battles.json",
        {
            "battles": battles
        }
    )


# =========================
# ② 直近2週間の試合数
# =========================

def export_battle_trend(conn):

    season_clause, season_params = get_season_filter_sql()

    today = date.today()
    start_date = today - timedelta(days=13)

    rows = conn.execute("""
        SELECT
            bm.battle_date,
            COUNT(b.id) AS battle_count

        FROM battle_messages AS bm

        JOIN battles AS b
            ON b.battle_message_id = bm.id

        WHERE b.player1_pokemon_id IN ({ph})
            AND b.player2_pokemon_id IN ({ph})
            AND bm.battle_date BETWEEN ? AND ?
            {season_clause}

        GROUP BY bm.battle_date
        ORDER BY bm.battle_date
    """.format(ph=ACTIVE_POKEMON_PLACEHOLDERS, season_clause=season_clause), (
        *ACTIVE_POKEMON_IDS,
        *ACTIVE_POKEMON_IDS,
        start_date.isoformat(),
        today.isoformat(),
        *season_params
    )).fetchall()

    counts = {
        row["battle_date"]: row["battle_count"]
        for row in rows
    }

    days = [
        {
            "date": (start_date + timedelta(days=offset)).isoformat(),
            "battle_count": counts.get(
                (start_date + timedelta(days=offset)).isoformat(),
                0
            )
        }
        for offset in range(14)
    ]

    save_json(
        "battle_trend.json",
        {
            "days": days
        }
    )


# =========================
# ② ポケモン使用率
# =========================

def export_usage(conn):

    season_clause, season_params = get_season_filter_sql()

    rows = conn.execute("""
        SELECT
            p.id,
            p.name,
            p.type1,
            p.type2,
            p.weaknesses,
            COUNT(*) AS usage_count

        FROM pokemon AS p

        JOIN battles AS b
            ON p.id = b.player1_pokemon_id
            OR p.id = b.player2_pokemon_id

        JOIN battle_messages AS bm
            ON b.battle_message_id = bm.id

        WHERE b.player1_pokemon_id IN ({ph})
            AND b.player2_pokemon_id IN ({ph})
            {season_clause}

        GROUP BY
            p.id,
            p.name

        ORDER BY
            usage_count DESC,
            p.id
    """.format(ph=ACTIVE_POKEMON_PLACEHOLDERS, season_clause=season_clause), (
        *ACTIVE_POKEMON_IDS,
        *ACTIVE_POKEMON_IDS,
        *season_params
    )).fetchall()


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

            **pokemon_details(row),

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

    season_clause, season_params = get_season_filter_sql()

    conn.execute("DROP TABLE IF EXISTS target_battles")
    conn.execute("""
        CREATE TEMP TABLE target_battles AS
        SELECT b.*
        FROM battles AS b
        JOIN battle_messages AS bm
            ON b.battle_message_id = bm.id
        WHERE b.player1_pokemon_id IN ({ph})
            AND b.player2_pokemon_id IN ({ph})
            {season_clause}
    """.format(ph=ACTIVE_POKEMON_PLACEHOLDERS, season_clause=season_clause), (
        *ACTIVE_POKEMON_IDS,
        *ACTIVE_POKEMON_IDS,
        *season_params
    ))

    pokemon_rows = conn.execute("""
        SELECT
            id,
            name,
            type1,
            type2,
            weaknesses
        FROM pokemon
        WHERE EXISTS (
            SELECT 1
            FROM target_battles AS b
            WHERE (
                    b.player1_pokemon_id = pokemon.id
                    OR b.player2_pokemon_id = pokemon.id
                )
        )
            AND id IN ({ph})
        ORDER BY id
        """.format(ph=ACTIVE_POKEMON_PLACEHOLDERS), ACTIVE_POKEMON_IDS).fetchall()


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

            FROM target_battles

            WHERE
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

            FROM target_battles

            WHERE
                (
                    (first_player = 1 AND player1_pokemon_id = ?)
                    OR
                    (first_player = 2 AND player2_pokemon_id = ?)
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

            FROM target_battles

            WHERE
                (
                    (first_player = 1 AND player2_pokemon_id = ?)
                    OR
                    (first_player = 2 AND player1_pokemon_id = ?)
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

            FROM target_battles

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

            **pokemon_details(pokemon),

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

    season_clause, season_params = get_season_filter_sql()

    rows = conn.execute("""
        SELECT

            b.player1_pokemon_id AS p1_id,
            p1.name AS p1_name,
            p1.type1 AS p1_type1,
            p1.type2 AS p1_type2,
            p1.weaknesses AS p1_weaknesses,

            b.player2_pokemon_id AS p2_id,
            p2.name AS p2_name,
            p2.type1 AS p2_type1,
            p2.type2 AS p2_type2,
            p2.weaknesses AS p2_weaknesses,

            b.player1_result,
            b.player2_result

        FROM battles AS b

        JOIN pokemon AS p1
            ON b.player1_pokemon_id = p1.id

        JOIN pokemon AS p2
            ON b.player2_pokemon_id = p2.id

        JOIN battle_messages AS bm
            ON b.battle_message_id = bm.id

        WHERE b.player1_pokemon_id IN ({ph})
            AND b.player2_pokemon_id IN ({ph})
            {season_clause}
    """.format(ph=ACTIVE_POKEMON_PLACEHOLDERS, season_clause=season_clause), (
        *ACTIVE_POKEMON_IDS,
        *ACTIVE_POKEMON_IDS,
        *season_params
    )).fetchall()


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
            pokemon1_details = {
                "type1": row["p1_type1"],
                "type2": row["p1_type2"],
                "weaknesses": parse_weaknesses(row["p1_weaknesses"])
            }

            pokemon2_id = p2_id
            pokemon2_name = row["p2_name"]
            pokemon2_details = {
                "type1": row["p2_type1"],
                "type2": row["p2_type2"],
                "weaknesses": parse_weaknesses(row["p2_weaknesses"])
            }

            pokemon1_result = row["player1_result"]
            pokemon2_result = row["player2_result"]

        else:

            pokemon1_id = p2_id
            pokemon1_name = row["p2_name"]
            pokemon1_details = {
                "type1": row["p2_type1"],
                "type2": row["p2_type2"],
                "weaknesses": parse_weaknesses(row["p2_weaknesses"])
            }

            pokemon2_id = p1_id
            pokemon2_name = row["p1_name"]
            pokemon2_details = {
                "type1": row["p1_type1"],
                "type2": row["p1_type2"],
                "weaknesses": parse_weaknesses(row["p1_weaknesses"])
            }

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
                    "name": pokemon1_name,
                    **pokemon1_details
                },

                "pokemon2": {
                    "id": pokemon2_id,
                    "name": pokemon2_name,
                    **pokemon2_details
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

    conn = get_connection()

    try:

        for season_id in SEASONS_TO_EXPORT:

            configure_season(season_id)

            if season_id == "all":
                print("全season合算モードでJSON出力を開始します。")
            else:
                print(f"season_id={season_id} を対象にJSON出力を開始します。")

            export_battles(conn)

            export_battle_trend(conn)

            export_usage(conn)

            export_pokemon_stats(conn)

            export_matchups(conn)

            print()

    finally:

        conn.close()


    print("JSON出力が完了しました。")


if __name__ == "__main__":
    main()