import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# パス設定
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

EMOJI_JSON_PATH = BASE_DIR / "docs/data/emoji.json"

CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:([^:>]+):\d+>")


def normalize_emoji_name(name: str) -> str:
    name = re.sub(r"^\d+", "", name)
    if name.startswith("moji_"):
        return "emoji_" + name[len("moji_"):]
    return name


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
            "pokemon_by_name": ...,
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
    pokemon_by_name = {
        normalize_emoji_name(CUSTOM_EMOJI_PATTERN.fullmatch(emoji).group(1)): pokemon_id
        for emoji, pokemon_id in pokemon_by_emoji.items()
    }
    first_or_second_by_name = {
        normalize_emoji_name(CUSTOM_EMOJI_PATTERN.fullmatch(symbols["first"]).group(1)): 1,
        normalize_emoji_name(CUSTOM_EMOJI_PATTERN.fullmatch(symbols["second"]).group(1)): 2,
    }
    result_by_name = {
        normalize_emoji_name(CUSTOM_EMOJI_PATTERN.fullmatch(symbols["vs-win-lose"]).group(1)): "win",
        normalize_emoji_name(CUSTOM_EMOJI_PATTERN.fullmatch(symbols["vs-lose-win"]).group(1)): "lose",
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
        "pokemon_by_name": pokemon_by_name,
        "first_or_second_by_name": first_or_second_by_name,
        "result_by_name": result_by_name,
        "emoji_pattern": emoji_pattern,
    }


# ============================================================
# 戦績文字列を抽出
# ============================================================

def extract_battle_strings(content: str) -> list[str]:
    """
    Discordメッセージから戦績文字列を抽出する。

    戦績は以下の4要素、旧形式では5要素で構成される。

        ① 自分の先攻/後攻
        ② 自分のポケモン
        ③ VS表記と戦績表記
        ④ 相手のポケモン
        ⑤ 相手の先攻/後攻（旧形式のみ）

    コメント、文章、改行、試合番号などは無視する。

    Returns:
        [
            "戦績絵文字からなる文字列",
            "戦績絵文字からなる文字列",
            ...
        ]
    """

    if not content:
        return []

    maps = build_emoji_maps()
    first_or_second = maps["first_or_second_by_name"]
    pokemon_by_name = maps["pokemon_by_name"]
    result_by_name = maps["result_by_name"]

    recognized_names = set(first_or_second) | set(pokemon_by_name) | set(result_by_name)
    recognized_emojis = [
        (match.group(0), normalize_emoji_name(match.group(1)))
        for match in CUSTOM_EMOJI_PATTERN.finditer(content)
        if normalize_emoji_name(match.group(1)) in recognized_names
    ]

    battle_strings = []
    index = 0
    while index < len(recognized_emojis):
        remaining = recognized_emojis[index:]
        names = [emoji[1] for emoji in remaining]

        # Older messages include the second player's first/second marker.
        is_four_emoji_battle = (
            len(names) >= 4
            and names[0] in first_or_second
            and names[1] in pokemon_by_name
            and names[2] in result_by_name
            and names[3] in pokemon_by_name
        )
        next_battle_follows = (
            len(names) >= 8
            and names[4] in first_or_second
            and names[5] in pokemon_by_name
            and names[6] in result_by_name
            and names[7] in pokemon_by_name
        )

        if (
            len(names) >= 5
            and names[0] in first_or_second
            and names[1] in pokemon_by_name
            and names[2] in result_by_name
            and names[3] in pokemon_by_name
            and names[4] in first_or_second
            and not next_battle_follows
        ):
            battle_strings.append("".join(emoji[0] for emoji in remaining[:5]))
            index += 5
        elif is_four_emoji_battle:
            battle_strings.append("".join(emoji[0] for emoji in remaining[:4]))
            index += 4
        else:
            index += 1

    return battle_strings


# ============================================================
# 戦績1件を解析
# ============================================================

def parse_battle_string(battle_string: str) -> dict | None:
    """
    4個または5個の絵文字からなる戦績文字列を解析する。

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

    # --------------------------------------------------------
    # 戦績文字列から絵文字を抽出
    # --------------------------------------------------------

    emojis = [
        normalize_emoji_name(name)
        for name in CUSTOM_EMOJI_PATTERN.findall(battle_string)
    ]

    # 4要素形式の現行メッセージと、5要素形式の旧メッセージを受け付ける。
    if len(emojis) not in (4, 5):
        return None

    # --------------------------------------------------------
    # 解析した要素を展開
    # --------------------------------------------------------

    my_first = emojis[0]
    my_pokemon = emojis[1]
    battle_result = emojis[2]
    opponent_pokemon = emojis[3]
    opponent_first = emojis[4] if len(emojis) == 5 else None

    # --------------------------------------------------------
    # 先攻/後攻確認
    # --------------------------------------------------------

    first_or_second = maps["first_or_second_by_name"]
    if my_first not in first_or_second:
        return None

    if opponent_first is not None and opponent_first not in first_or_second:
        return None

    # --------------------------------------------------------
    # ポケモン確認
    # --------------------------------------------------------

    pokemon_by_name = maps["pokemon_by_name"]
    if my_pokemon not in pokemon_by_name:
        return None

    if opponent_pokemon not in pokemon_by_name:
        return None

    # --------------------------------------------------------
    # 統合された勝敗表記を確認
    # --------------------------------------------------------

    result_by_name = maps["result_by_name"]
    if battle_result not in result_by_name:
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

    first_player = first_or_second[my_first]

    # --------------------------------------------------------
    # 勝敗
    # --------------------------------------------------------

    player1_result = result_by_name[battle_result]
    player2_result = {"win": "lose", "lose": "win"}[player1_result]

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    return {
        "first_player": first_player,

        "player1_pokemon_id":
            pokemon_by_name[my_pokemon],

        "player2_pokemon_id":
            pokemon_by_name[opponent_pokemon],

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

    return [
        result
        for battle_string in extract_battle_strings(content)
        if (result := parse_battle_string(battle_string)) is not None
    ]


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