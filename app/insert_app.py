import json
import os
from datetime import date
from pathlib import Path
import re
from urllib.parse import urlparse
import uuid

from flask import Flask, abort, redirect, render_template, request, url_for

if __package__:
    from .battle_matches import (
        is_battle_challenge_message,
        parse_tournament_games,
        tournament_name_from_message,
    )
    from .battle_parser import parse_discord_message
    from .repository import (
        BattleRepository,
        ConflictError,
        DuplicateError,
        NotFoundError,
        get_repository,
    )
else:
    from battle_matches import (
        is_battle_challenge_message,
        parse_tournament_games,
        tournament_name_from_message,
    )
    from battle_parser import parse_discord_message
    from repository import (
        BattleRepository,
        ConflictError,
        DuplicateError,
        NotFoundError,
        get_repository,
    )


BASE_DIR = Path(__file__).resolve().parent.parent
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "app" / "templates"),
    static_folder=str(BASE_DIR / "docs" / "assets"),
    static_url_path="/static",
)

DB_PATH = BASE_DIR / "data" / "pokemon.db"
REPOSITORY: BattleRepository | None = None
ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def get_repo() -> BattleRepository:
    if REPOSITORY is not None:
        return REPOSITORY
    return get_repository(db_path=DB_PATH)


@app.before_request
def verify_local_origin() -> None:
    if request.method != "POST":
        return
    origin = request.headers.get("Origin")
    if not origin:
        return
    parsed = urlparse(origin)
    if parsed.hostname not in ALLOWED_LOCAL_HOSTS:
        abort(403, description="許可されていない送信元（Origin）からのリクエストです。")


@app.context_processor
def inject_connection_info() -> dict:
    repo = get_repo()
    return {
        "repo_backend_name": repo.backend_name,
        "repo_backend_label": repo.backend_label,
        "repo_is_dynamodb": repo.backend_name == "dynamodb",
    }


# =========================
# Battle Message登録
# =========================

@app.route("/message", methods=["GET", "POST"])
def message():
    repo = get_repo()
    seasons = repo.list_seasons()

    if request.method == "POST":
        discord_message_id = request.form.get("discord_message_id", "").strip()
        season_id = request.form.get("season_id", type=int)
        battle_date = request.form.get("battle_date", "").strip()
        raw_content = request.form.get("raw_content", "")

        if not discord_message_id or season_id is None:
            abort(400, description="必須項目を入力してください。")

        try:
            message_id = repo.create_message(
                discord_message_id=discord_message_id,
                season_id=season_id,
                battle_date=battle_date,
                raw_content=raw_content,
            )
        except DuplicateError as error:
            abort(409, description=str(error))
        except ValueError as error:
            abort(400, description=str(error))

        if is_battle_challenge_message(raw_content):
            return redirect(url_for("battle_challenge", message_id=message_id))
        return redirect(url_for("battle", message_id=message_id))

    return render_template("message_form.html", seasons=seasons)


# =========================
# Battle登録
# =========================

@app.route("/battle", methods=["GET", "POST"])
def battle():
    repo = get_repo()
    pokemon = repo.list_pokemon()
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

    players = repo.list_players(order="id")

    if request.method == "POST":
        if request.form.get("action") == "load_message":
            message_id = request.form.get("battle_message_id", type=int)
            if message_id is None or repo.get_message(message_id) is None:
                abort(400)
            return redirect(url_for("battle", message_id=message_id))

        battle_message_id = request.form.get("battle_message_id", type=int)
        if battle_message_id is None:
            abort(400)
        player1_raw = request.form.get("player1_id", "").strip()
        player2_raw = request.form.get("player2_id", "").strip()
        player1_id = int(player1_raw) if player1_raw else None
        player2_id = int(player2_raw) if player2_raw else None
        request_id = request.form.get("request_id", "").strip() or None

        player1_pokemon_ids = request.form.getlist("player1_pokemon_id")
        player2_pokemon_ids = request.form.getlist("player2_pokemon_id")
        first_players = request.form.getlist("first_player")
        player1_results = request.form.getlist("player1_result")
        battle_types = request.form.getlist("battle_type")

        battle_count = len(player1_pokemon_ids)
        if (
            battle_count == 0
            or battle_count > 90
            or len(player2_pokemon_ids) != battle_count
            or len(first_players) != battle_count
            or len(player1_results) != battle_count
            or len(battle_types) != battle_count
            or any(result not in ("win", "lose") for result in player1_results)
            or any(first not in ("", "1", "2") for first in first_players)
            or any(battle_type not in ("normal", "challenge") for battle_type in battle_types)
        ):
            abort(400)

        rows = [
            {
                "player1_pokemon_id": int(p1_pid),
                "player2_pokemon_id": int(p2_pid),
                "first_player": int(fp) if fp else None,
                "player1_result": res,
                "battle_type": btype,
            }
            for p1_pid, p2_pid, fp, res, btype in zip(
                player1_pokemon_ids,
                player2_pokemon_ids,
                first_players,
                player1_results,
                battle_types,
            )
        ]

        try:
            created_ids = repo.create_battles(
                message_id=battle_message_id,
                player1_id=player1_id,
                player2_id=player2_id,
                rows=rows,
                request_id=request_id,
            )
        except NotFoundError as error:
            abort(400, description=str(error))
        except ConflictError as error:
            abort(409, description=str(error))
        except ValueError as error:
            abort(400, description=str(error))

        registered_param = ",".join(str(bid) for bid in created_ids)
        return redirect(url_for("index", registered=registered_param))

    messages = repo.list_messages()
    selected_message_id = request.args.get("message_id", type=int)
    selected_message = (
        repo.get_message(selected_message_id)
        if selected_message_id is not None
        else None
    )
    if selected_message is not None and not any(
        m["id"] == selected_message["id"] for m in messages
    ):
        messages = [selected_message, *messages]

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
                **battle_item,
                "player1_id": player1_id,
                "player2_id": player2_id,
            }
            for battle_item in parsed_battles
        ]

    if not initial_battles:
        initial_battles = [{
            "player1_pokemon_id": None,
            "player2_pokemon_id": None,
            "first_player": None,
            "player1_result": None,
            "battle_type": "normal",
            "player1_id": None,
            "player2_id": None,
        }]

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
        request_id=str(uuid.uuid4()),
    )


# =========================
# Battle Challenge（BO3）登録
# =========================

@app.route("/battle-challenge", methods=["GET", "POST"])
def battle_challenge():
    repo = get_repo()
    seasons = repo.list_seasons()
    pokemon = repo.list_pokemon()
    players = repo.list_players(order="name")
    messages = repo.list_messages(challenge_only=True)

    selected_message_id = request.values.get("message_id", type=int)
    selected_message = (
        repo.get_message(selected_message_id)
        if selected_message_id is not None
        else None
    )
    if selected_message is not None and not is_battle_challenge_message(
        selected_message.get("raw_content")
    ):
        selected_message = None
    if selected_message is not None and not any(
        m["id"] == selected_message["id"] for m in messages
    ):
        messages = [selected_message, *messages]

    if request.method == "POST":
        if selected_message is None:
            abort(400)

        raw_content = selected_message["raw_content"] or ""
        if not is_battle_challenge_message(raw_content):
            abort(400, description="バトルチャレンジを示す語句がメッセージにありません。")
        parsed_games = parse_tournament_games(raw_content)
        if len(parsed_games) > 3:
            abort(400, description="大会対戦は最大3ゲームです。元メッセージを確認してください。")

        if selected_message.get("match_id") is not None:
            abort(409, description="このメッセージの大会対戦は登録済みです。")

        season_id = request.form.get("season_id", type=int)
        match_date = request.form.get("match_date", "").strip()
        try:
            date.fromisoformat(match_date)
        except ValueError:
            abort(400, description="対戦日を入力してください。")
        if not any(season["id"] == season_id for season in seasons):
            abort(400, description="シーズンを選択してください。")

        player_id = request.form.get("player_id", type=int)
        if player_id is None or not any(player["id"] == player_id for player in players):
            abort(400, description="プレイヤー別の戦績に表示するため、投稿者を選択してください。")

        pokemon_ids = {item["id"] for item in pokemon}
        games = []
        encountered_empty_game = False
        for game_number in range(1, 4):
            player1_pokemon = request.form.get(f"player1_pokemon_id_{game_number}", "").strip()
            player2_pokemon = request.form.get(f"player2_pokemon_id_{game_number}", "").strip()
            result = request.form.get(f"player1_result_{game_number}", "").strip()
            first_player = request.form.get(f"first_player_{game_number}", "").strip()
            values = (player1_pokemon, player2_pokemon, result)

            if not any(values):
                encountered_empty_game = True
                continue
            if encountered_empty_game or not all(values):
                abort(400, description="ゲームはGAME 1から順に、必要項目を入力してください。")
            if result not in ("win", "lose") or first_player not in ("", "1", "2"):
                abort(400)
            try:
                player1_pokemon_id = int(player1_pokemon)
                player2_pokemon_id = int(player2_pokemon)
            except ValueError:
                abort(400)
            if player1_pokemon_id not in pokemon_ids or player2_pokemon_id not in pokemon_ids:
                abort(400, description="登録済みのポケモンを選択してください。")

            games.append({
                "player1_pokemon_id": player1_pokemon_id,
                "player2_pokemon_id": player2_pokemon_id,
                "player1_result": result,
                "first_player": int(first_player) if first_player else None,
            })

        if not games:
            abort(400, description="少なくとも1ゲームを入力してください。")

        try:
            match_id = repo.create_battle_match(
                battle_message_id=selected_message_id,
                season_id=season_id,
                tournament_name=request.form.get("tournament_name", "").strip() or None,
                opponent=request.form.get("opponent", "").strip() or None,
                match_date=match_date,
                player_id=player_id,
                games=games,
            )
        except DuplicateError as error:
            abort(409, description=str(error))
        except ConflictError as error:
            abort(409, description=str(error))
        except NotFoundError as error:
            abort(400, description=str(error))
        except ValueError as error:
            abort(400, description=str(error))
        except RuntimeError as error:
            abort(503, description=str(error))

        return redirect(
            url_for("battle_challenge", message_id=selected_message_id, registered=match_id)
        )

    parsed_games = (
        parse_tournament_games(selected_message["raw_content"] or "")
        if selected_message
        else []
    )
    initial_game_count = len(parsed_games)
    initial_games = parsed_games[:3]
    while len(initial_games) < 3:
        initial_games.append({
            "player1_pokemon_id": None,
            "player2_pokemon_id": None,
            "player1_result": None,
            "first_player": None,
        })

    existing_match = None
    if selected_message is not None and selected_message.get("match_id") is not None:
        existing_match = {"match_id": selected_message["match_id"]}

    return render_template(
        "battle_challenge_form.html",
        messages=messages,
        seasons=seasons,
        pokemon=pokemon,
        players=players,
        selected_message=selected_message,
        selected_message_id=selected_message_id,
        initial_games=initial_games,
        initial_game_count=initial_game_count,
        tournament_name=tournament_name_from_message(
            selected_message["raw_content"] if selected_message else ""
        ),
        registered_match_id=request.args.get("registered", type=int),
        existing_match=existing_match,
    )


# =========================
# Player登録・サブアカウント追加紐づけ
# =========================

@app.route("/player", methods=["GET", "POST"])
def player():
    repo = get_repo()

    if request.method == "POST":
        action = request.form.get("action", "create_player")
        if action == "link_discord":
            target_player_id = request.form.get("player_id", type=int)
            discord_user_id = request.form.get("discord_user_id", "").strip()
            label = request.form.get("label", "").strip() or None
            if target_player_id is None or not discord_user_id:
                abort(400, description="対象プレイヤーとDiscord User IDを入力してください。")
            try:
                repo.link_discord_account(
                    player_id=target_player_id,
                    discord_user_id=discord_user_id,
                    label=label,
                )
            except DuplicateError as error:
                abort(409, description=str(error))
            except (NotFoundError, ValueError, NotImplementedError) as error:
                abort(400, description=str(error))
            return redirect(url_for("player"))

        discord_user_id = request.form.get("discord_user_id", "").strip()
        name = request.form.get("name", "").strip()
        if not discord_user_id or not name:
            abort(400, description="Discord User IDと名前を入力してください。")
        try:
            repo.create_player(discord_user_id=discord_user_id, name=name)
        except DuplicateError as error:
            abort(409, description=str(error))
        except ValueError as error:
            abort(400, description=str(error))
        return redirect(url_for("player"))

    players = repo.list_players(order="id", include_accounts=True)
    return render_template("player_form.html", players=players)


# =========================
# 論理削除（確認画面＋理由必須）
# =========================

@app.route("/battle/<int:battle_id>/delete", methods=["GET", "POST"])
def delete_battle_route(battle_id: int):
    repo = get_repo()
    target = repo.get_battle(battle_id)
    if target is None:
        abort(404, description="指定された対戦が見つかりません。")
    if target.get("match_id") is not None:
        return redirect(url_for("delete_match_route", match_id=target["match_id"]))

    if request.method == "POST":
        reason = request.form.get("reason", "").strip()
        if not reason:
            abort(400, description="削除理由を入力してください。")
        try:
            repo.delete_battle(battle_id, reason)
        except NotFoundError as error:
            abort(404, description=str(error))
        except ValueError as error:
            abort(400, description=str(error))
        return redirect(url_for("index"))

    return render_template("confirm_delete.html", kind="battle", target=target)


@app.route("/match/<int:match_id>/delete", methods=["GET", "POST"])
def delete_match_route(match_id: int):
    repo = get_repo()
    target = repo.get_match(match_id)
    if target is None:
        abort(404, description="指定された大会対戦が見つかりません。")

    if request.method == "POST":
        reason = request.form.get("reason", "").strip()
        if not reason:
            abort(400, description="削除理由を入力してください。")
        try:
            repo.delete_match(match_id, reason)
        except NotFoundError as error:
            abort(404, description=str(error))
        except ConflictError as error:
            abort(409, description=str(error))
        except ValueError as error:
            abort(400, description=str(error))
        return redirect(url_for("index"))

    return render_template("confirm_delete.html", kind="match", target=target)


# =========================
# トップ
# =========================

@app.route("/")
def index():
    repo = get_repo()
    registered_raw = request.args.get("registered", "")
    include_ids = [
        int(part)
        for part in registered_raw.split(",")
        if part.strip().isdigit()
    ]
    battles = repo.list_recent_battles(limit=50, include_ids=include_ids or None)
    latest_message = repo.get_latest_message()
    return render_template(
        "insert_index.html",
        battles=battles,
        latest_message=latest_message,
    )


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=os.environ.get("PLAKORO_DEBUG") == "1",
    )
