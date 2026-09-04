import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# パス設定
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

EMOJI_JSON_PATH = BASE_DIR / "docs/emoji.json"


# ============================================================
# emoji.json
# ============================================================

def load_emoji_data():
    """
    emoji.jsonを読み込む。
    """

    with open(EMOJI_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 絵文字情報を作成
# ============================================================

def build_emoji_maps():
    """
    emoji.jsonから戦績解析用の辞書を作成する。

    Returns:
        {
            "symbols": ...,
            "pokemon_by_emoji": ...,
            "emoji_pattern": ...
        }
    """

    data = load_emoji_data()

    symbols = data["symbols"]

    # --------------------------------------------------------
    # ポケモン
    #
    # emoji -> pokemon_id
    # --------------------------------------------------------

    pokemon_by_emoji = {
        pokemon["emoji"]: pokemon["id"]
        for pokemon in data["pokemon"]
    }

    # --------------------------------------------------------
    # 戦績に登場する絵文字
    # --------------------------------------------------------

    battle_emojis = set()

    # ポケモン
    battle_emojis.update(pokemon_by_emoji.keys())

    # 先攻・後攻・勝敗表記
    battle_emojis.add(symbols["first"])
    battle_emojis.add(symbols["second"])
    battle_emojis.add(symbols["vs-win-lose"])
    battle_emojis.add(symbols["vs-lose-win"])

    # 空文字などを除外
    battle_emojis.discard("")

    # --------------------------------------------------------
    # 正規表現
    #
    # 長いものから先に並べる
    # --------------------------------------------------------

    emoji_pattern = "|".join(
        re.escape(emoji)
        for emoji in sorted(
            battle_emojis,
            key=len,
            reverse=True
        )
    )

    return {
        "symbols": symbols,
        "pokemon_by_emoji": pokemon_by_emoji,
        "emoji_pattern": emoji_pattern,
    }


# ============================================================
# 戦績文字列を抽出
# ============================================================

def extract_battle_strings(content: str) -> list[str]:
    """
    Discordメッセージから戦績文字列を抽出する。

    戦績は以下の5要素で構成される。

        ① 自分の先攻/後攻
        ② 自分のポケモン
        ③ VS表記と戦績表記
        ④ 相手のポケモン
        ⑤ 相手の先攻/後攻

    コメント、文章、改行、試合番号などは無視する。

    Returns:
        [
            "5個の絵文字からなる戦績文字列",
            "5個の絵文字からなる戦績文字列",
            ...
        ]
    """

    if not content:
        return []

    maps = build_emoji_maps()

    symbols = maps["symbols"]
    emoji_pattern = maps["emoji_pattern"]

    # --------------------------------------------------------
    # 戦績の各要素
    # --------------------------------------------------------

    first_or_second = (
        rf"(?:{re.escape(symbols['first'])}"
        rf"|{re.escape(symbols['second'])})"
    )

    pokemon = (
        rf"(?:"
        + "|".join(
            re.escape(emoji)
            for emoji in maps["pokemon_by_emoji"].keys()
        )
        + ")"
    )

    battle_result = (
        rf"(?:{re.escape(symbols['vs-win-lose'])}"
        rf"|{re.escape(symbols['vs-lose-win'])})"
    )

    # --------------------------------------------------------
    # 5要素が連続している部分だけを戦績として認識
    #
    # ①先攻/後攻
    # ②ポケモン
    # ③VS表記と勝敗
    # ④ポケモン
    # ⑤先攻/後攻
    #
    # Discordのカスタム絵文字は
    # <:name:id>
    # という文字列なので、これを直接判定する。
    # --------------------------------------------------------

    pattern = re.compile(
        first_or_second
        + pokemon
        + battle_result
        + pokemon
        + first_or_second
    )

    return pattern.findall(content)


# ============================================================
# 戦績1件を解析
# ============================================================

def parse_battle_string(battle_string: str) -> dict | None:
    """
    5個の絵文字からなる戦績文字列を解析する。

    Returns:
        {
            "first_player": 1,
            "player1_pokemon_id": 4,
            "player2_pokemon_id": 1,
            "player1_result": "win",
            "player2_result": "lose"
        }

    解析できない場合:
        None
    """

    if not battle_string:
        return None

    maps = build_emoji_maps()

    symbols = maps["symbols"]
    pokemon_by_emoji = maps["pokemon_by_emoji"]

    # --------------------------------------------------------
    # 戦績文字列から5個の絵文字を抽出
    # --------------------------------------------------------

    emoji_pattern = re.compile(
        maps["emoji_pattern"]
    )

    emojis = emoji_pattern.findall(
        battle_string
    )

    # 5個でなければ不正
    if len(emojis) != 5:
        return None

    # --------------------------------------------------------
    # 5要素を展開
    # --------------------------------------------------------

    my_first = emojis[0]
    my_pokemon = emojis[1]
    battle_result = emojis[2]
    opponent_pokemon = emojis[3]
    opponent_first = emojis[4]

    # --------------------------------------------------------
    # 先攻/後攻確認
    # --------------------------------------------------------

    if my_first not in (
        symbols["first"],
        symbols["second"],
    ):
        return None

    if opponent_first not in (
        symbols["first"],
        symbols["second"],
    ):
        return None

    # --------------------------------------------------------
    # ポケモン確認
    # --------------------------------------------------------

    if my_pokemon not in pokemon_by_emoji:
        return None

    if opponent_pokemon not in pokemon_by_emoji:
        return None

    # --------------------------------------------------------
    # 統合された勝敗表記を確認
    # --------------------------------------------------------

    if battle_result not in (
        symbols["vs-win-lose"],
        symbols["vs-lose-win"],
    ):
        return None

    # --------------------------------------------------------
    # Player 1 / Player 2 の決定
    #
    # 今回は、
    # 「メッセージを投稿した自分」をPlayer 1
    # として扱う。
    #
    # そのため、自分が先攻ならfirst_player=1、
    # 自分が後攻ならfirst_player=2。
    # --------------------------------------------------------

    if my_first == symbols["first"]:
        first_player = 1
    else:
        first_player = 2

    # --------------------------------------------------------
    # 勝敗
    # --------------------------------------------------------

    if battle_result == symbols["vs-win-lose"]:
        player1_result = "win"
        player2_result = "lose"
    else:
        player1_result = "lose"
        player2_result = "win"

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    return {
        "first_player": first_player,

        "player1_pokemon_id":
            pokemon_by_emoji[my_pokemon],

        "player2_pokemon_id":
            pokemon_by_emoji[opponent_pokemon],

        "player1_result":
            player1_result,

        "player2_result":
            player2_result,
    }


# ============================================================
# Discordメッセージ全体を解析
# ============================================================

def parse_discord_message(content: str) -> list[dict]:
    """
    Discordメッセージから全試合を解析する。

    例えば、

        今日は3戦しました！

        [戦績1]

        この試合は惜しかった

        [戦績2]

        次！

        [戦績3]

    のようなメッセージでもOK。

    Returns:
        [
            {...},
            {...},
            {...}
        ]
    """

    battle_strings = extract_battle_strings(content)

    results = []

    for battle_string in battle_strings:

        result = parse_battle_string(
            battle_string
        )

        if result is not None:
            results.append(result)

    return results


# ============================================================
# SQLite登録
# ============================================================

def register_battles(
    db_path: str,
    message_id: int | str,
    player1_id: int | None,
    player2_id: int | None,
    content: str,
    created_at: str | None = None,
) -> list[int]:
    """
    Discordメッセージから戦績を取得し、
    SQLiteへ登録する。

    Parameters
    ----------
    db_path:
        SQLiteファイルのパス

    message_id:
        DiscordメッセージID

    player1_id:
        Player 1のDiscord User ID

    player2_id:
        Player 2のDiscord User ID

    content:
        Discordメッセージ本文

    created_at:
        Discordメッセージの作成日時。省略時はUTC現在時刻。

    Returns
    -------
    list[int]
        登録された battles.id の一覧。

        戦績がなかった場合:
            []

        既に登録済みの場合:
            []
    """

    # --------------------------------------------------------
    # メッセージを解析
    # --------------------------------------------------------

    battles = parse_discord_message(
        content
    )

    if not battles:
        return []

    # --------------------------------------------------------
    # SQLite接続
    # --------------------------------------------------------

    conn = sqlite3.connect(
        db_path
    )

    try:
        cursor = conn.cursor()

        # ----------------------------------------------------
        # 二重登録チェック
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT id
            FROM battle_messages
            WHERE discord_message_id = ?
            """,
            (str(message_id),)
        )

        existing = cursor.fetchone()

        if existing is not None:
            print(
                f"Battle message already exists: "
                f"{message_id}"
            )

            return []

        # ----------------------------------------------------
        # battle_messages登録
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO battle_messages (
                discord_message_id,
                raw_content,
                created_at
            )
            VALUES (?, ?, ?)
            """,
            (
                str(message_id),
                content,
                created_at or datetime.now(timezone.utc).isoformat(),
            )
        )

        battle_message_id = cursor.lastrowid

        # ----------------------------------------------------
        # battles登録
        # ----------------------------------------------------

        registered_ids = []

        for battle_number, battle in enumerate(
            battles,
            start=1
        ):

            cursor.execute(
                """
                INSERT INTO battles (
                    battle_message_id,
                    battle_number,
                    player1_id,
                    player2_id,
                    player1_pokemon_id,
                    player2_pokemon_id,
                    first_player,
                    player1_result,
                    player2_result
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    battle_message_id,

                    battle_number,

                    player1_id,
                    player2_id,

                    battle["player1_pokemon_id"],
                    battle["player2_pokemon_id"],

                    battle["first_player"],

                    battle["player1_result"],
                    battle["player2_result"],
                )
            )

            registered_ids.append(
                cursor.lastrowid
            )

        # ----------------------------------------------------
        # 確定
        # ----------------------------------------------------

        conn.commit()

        return registered_ids

    except Exception:
        # ----------------------------------------------------
        # エラーが発生した場合は全て取り消す
        # ----------------------------------------------------

        conn.rollback()

        raise

    finally:
        # ----------------------------------------------------
        # DB切断
        # ----------------------------------------------------

        conn.close()