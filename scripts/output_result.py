import sqlite3
import json
import shutil
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
    2: tuple(range(1, 16)),
    # Season 3 characters are not finalized yet. Keep the existing roster
    # available so the season can start collecting battles immediately.
    3: tuple(range(1, 22))
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
            b.battle_type,

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
            AND b.match_id IS NULL
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
            "battle_type": row["battle_type"] or "normal",

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


def calculate_series_result(results):
    player_wins = results.count("win")
    opponent_wins = results.count("lose")
    if player_wins >= 2:
        return "win", player_wins, opponent_wins
    if opponent_wins >= 2:
        return "lose", player_wins, opponent_wins
    return None, player_wins, opponent_wins


def export_battle_matches(conn):
    season_filter = "" if ACTIVE_SEASON_ID == "all" else "WHERE m.season_id = ?"
    season_params = [] if ACTIVE_SEASON_ID == "all" else [ACTIVE_SEASON_ID]
    match_rows = conn.execute(f"""
        SELECT
            m.match_id, m.match_type, m.tournament_name, m.opponent, m.match_date,
            m.season_id, m.player_id, player.name AS player_name,
            m.battle_message_id
        FROM battle_matches AS m
        LEFT JOIN players AS player ON player.id = m.player_id
        {season_filter}
        ORDER BY m.match_date DESC, m.match_id DESC
    """, season_params).fetchall()
    game_rows = conn.execute(f"""
        SELECT
            b.match_id, b.id, b.battle_message_id, b.battle_number,
            b.game_number, b.player1_id, b.player2_id,
            b.player1_pokemon_id, p1.name AS player1_pokemon,
            p1.type1 AS player1_type1, p1.type2 AS player1_type2,
            p1.weaknesses AS player1_weaknesses,
            b.player2_pokemon_id, p2.name AS player2_pokemon,
            p2.type1 AS player2_type1, p2.type2 AS player2_type2,
            p2.weaknesses AS player2_weaknesses,
            b.first_player, b.player1_result, b.player2_result,
            m.season_id,
            player1.name AS player1_name, player2.name AS player2_name,
            m.opponent
        FROM battles AS b
        JOIN battle_matches AS m ON m.match_id = b.match_id
        JOIN pokemon AS p1 ON p1.id = b.player1_pokemon_id
        JOIN pokemon AS p2 ON p2.id = b.player2_pokemon_id
        LEFT JOIN players AS player1 ON player1.id = b.player1_id
        LEFT JOIN players AS player2 ON player2.id = b.player2_id
        {"WHERE m.season_id = ?" if ACTIVE_SEASON_ID != "all" else ""}
        ORDER BY b.match_id, b.game_number
    """, season_params).fetchall()
    games_by_match = {}
    for row in game_rows:
        games_by_match.setdefault(row["match_id"], []).append({
            "id": row["id"],
            "battle_message_id": row["battle_message_id"],
            "battle_number": row["battle_number"],
            "game_number": row["game_number"],
            "battle_type": "challenge",
            "match_id": row["match_id"],
            "season_id": row["season_id"],
            "player1": {
                "id": row["player1_id"],
                "name": row["player1_name"],
                "pokemon_id": row["player1_pokemon_id"],
                "pokemon": row["player1_pokemon"],
                "type1": row["player1_type1"],
                "type2": row["player1_type2"],
                "weaknesses": parse_weaknesses(row["player1_weaknesses"]),
                "result": row["player1_result"],
            },
            "player2": {
                "id": row["player2_id"],
                "name": row["player2_name"] or row["opponent"],
                "pokemon_id": row["player2_pokemon_id"],
                "pokemon": row["player2_pokemon"],
                "type1": row["player2_type1"],
                "type2": row["player2_type2"],
                "weaknesses": parse_weaknesses(row["player2_weaknesses"]),
                "result": row["player2_result"],
            },
            "first_player": row["first_player"],
        })

    matches = []
    for row in match_rows:
        games = games_by_match.get(row["match_id"], [])
        result, player_score, opponent_score = calculate_series_result(
            [game["player1"]["result"] for game in games]
        )
        matches.append({
            "match_id": row["match_id"],
            "match_type": row["match_type"],
            "tournament_name": row["tournament_name"],
            "opponent": row["opponent"],
            "match_date": row["match_date"],
            "match_result": result,
            "player_score": player_score,
            "opponent_score": opponent_score,
            "season_id": row["season_id"],
            "player": {"id": row["player_id"], "name": row["player_name"]},
            "battle_message_id": row["battle_message_id"],
            "games": games,
        })
    save_json("battle_matches.json", {"matches": matches})
    return matches


def export_battle_challenge_stats(conn, matches):
    season_clause, season_params = get_season_filter_sql()
    tournament_season_clause = (
        "AND m.season_id = ?" if ACTIVE_SEASON_ID != "all" else ""
    )
    rows = conn.execute("""
        SELECT
            b.match_id, b.player1_pokemon_id, b.player2_pokemon_id,
            b.player1_result, b.player2_result
        FROM battles AS b
        JOIN battle_messages AS bm ON bm.id = b.battle_message_id
        WHERE b.player1_pokemon_id IN ({ph})
            AND b.player2_pokemon_id IN ({ph})
            AND b.match_id IS NULL
            {season_clause}
        UNION ALL
        SELECT
            b.match_id, b.player1_pokemon_id, b.player2_pokemon_id,
            b.player1_result, b.player2_result
        FROM battles AS b
        JOIN battle_matches AS m ON m.match_id = b.match_id
        WHERE b.player1_pokemon_id IN ({ph})
            AND b.player2_pokemon_id IN ({ph})
            {tournament_season_clause}
    """.format(
        ph=ACTIVE_POKEMON_PLACEHOLDERS,
        season_clause=season_clause,
        tournament_season_clause=tournament_season_clause,
    ), (
        *ACTIVE_POKEMON_IDS, *ACTIVE_POKEMON_IDS, *season_params,
        *ACTIVE_POKEMON_IDS, *ACTIVE_POKEMON_IDS,
        *([] if ACTIVE_SEASON_ID == "all" else [ACTIVE_SEASON_ID]),
    )).fetchall()

    def game_totals(game_rows):
        return {
            "games": len(game_rows),
            "wins": sum(row["player1_result"] == "win" for row in game_rows),
            "losses": sum(row["player1_result"] == "lose" for row in game_rows),
        }

    tournament_games = [row for row in rows if row["match_id"] is not None]
    normal_games = [row for row in rows if row["match_id"] is None]
    scopes = {
        "normal": normal_games,
        "battle_challenge": tournament_games,
        "all": rows,
    }
    pokemon_scopes = {}
    for scope, scope_rows in scopes.items():
        by_pokemon = {}
        for row in scope_rows:
            for side in (1, 2):
                pokemon_id = row[f"player{side}_pokemon_id"]
                result = row[f"player{side}_result"]
                stats = by_pokemon.setdefault(pokemon_id, {
                    "games": 0, "wins": 0, "losses": 0, "usage_count": 0,
                })
                stats["games"] += 1
                stats["usage_count"] += 1
                stats["wins"] += result == "win"
                stats["losses"] += result == "lose"
        total_usage = sum(stats["usage_count"] for stats in by_pokemon.values())
        pokemon_scopes[scope] = [
            {
                "pokemon_id": pokemon_id,
                "name": conn.execute(
                    "SELECT name FROM pokemon WHERE id = ?", (pokemon_id,)
                ).fetchone()["name"],
                **stats,
                "usage_rate": round(stats["usage_count"] / total_usage * 100, 1)
                    if total_usage else 0,
                "win_rate": round(stats["wins"] / stats["games"] * 100, 1)
                    if stats["games"] else 0,
            }
            for pokemon_id, stats in sorted(by_pokemon.items())
        ]

    series_wins = sum(match["match_result"] == "win" for match in matches)
    series_losses = sum(match["match_result"] == "lose" for match in matches)
    decided_series = series_wins + series_losses
    all_game_stats = game_totals(tournament_games)
    game_count = all_game_stats["games"]
    save_json("battle_challenge_stats.json", {
        "series": {
            "series_count": len(matches),
            "wins": series_wins,
            "losses": series_losses,
            "win_rate": round(series_wins / decided_series * 100, 1) if decided_series else 0,
            "undecided": len(matches) - decided_series,
        },
        "games": {
            **all_game_stats,
            "win_rate": round(all_game_stats["wins"] / game_count * 100, 1) if game_count else 0,
        },
        "game_scopes": {
            scope: {**game_totals(scope_rows)} for scope, scope_rows in scopes.items()
        },
        "pokemon": pokemon_scopes,
    })


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
            AND b.match_id IS NULL
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

        JOIN (
            SELECT
                b.id AS battle_id,
                b.battle_message_id,
                b.player1_pokemon_id AS pokemon_id
            FROM battles AS b

            UNION ALL

            SELECT
                b.id AS battle_id,
                b.battle_message_id,
                b.player2_pokemon_id AS pokemon_id
            FROM battles AS b
        ) AS appearances
            ON p.id = appearances.pokemon_id

        JOIN battle_messages AS bm
            ON appearances.battle_message_id = bm.id

        JOIN battles AS b
            ON b.id = appearances.battle_id

        WHERE b.player1_pokemon_id IN ({ph})
            AND b.player2_pokemon_id IN ({ph})
            AND b.match_id IS NULL
            AND p.id IN ({ph})
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
            AND b.match_id IS NULL
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
            AND b.match_id IS NULL
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
# Player一覧
# （seasonに紐づかないため、season別フォルダではなく
#  docs/data/ 直下に1つだけ出力する）
# =========================

def export_players(conn):

    rows = conn.execute("""
        SELECT
            id,
            name
        FROM players
        ORDER BY name COLLATE NOCASE
    """).fetchall()

    players = [
        {
            "id": row["id"],
            "name": row["name"]
        }
        for row in rows
    ]

    path = BASE_DIR / "docs" / "data" / "players.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "players": players
            },
            f,
            ensure_ascii=False,
            indent=4
        )

    print(f"出力: {path}")


# =========================
# メイン
# =========================

def main():

    conn = get_connection()

    try:

        # Player一覧はseason非依存なので1回だけ出力
        export_players(conn)

        for season_id in SEASONS_TO_EXPORT:
            configure_season(season_id)

            if season_id == "all":
                print("全season合算モードでJSON出力を開始します。")
            else:
                print(f"season_id={season_id} を対象にJSON出力を開始します。")

            export_battles(conn)

            matches = export_battle_matches(conn)
            export_battle_challenge_stats(conn, matches)

            export_battle_trend(conn)

            export_usage(conn)

            export_pokemon_stats(conn)

            export_matchups(conn)

            if season_id == "all":
                shutil.copyfile(
                    OUTPUT_DIR / "usage.json",
                    BASE_DIR / "docs" / "data" / "usage.json"
                )

            print()

    finally:

        conn.close()


    print("JSON出力が完了しました。")


if __name__ == "__main__":
    main()