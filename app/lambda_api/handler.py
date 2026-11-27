"""AWS Lambda HTTP API v2 router for Online Plakoro (手順7 §3・§4・§5・§6)."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
import re
from typing import Any, Callable

from ..aggregation import (
    build_battle_matches_json,
    build_battle_trend_json,
    build_battles_json,
    build_recent_feed,
    compute_battle_challenge_stats,
    compute_character_detail,
    compute_matchups,
    compute_player_detail,
    compute_player_list,
    compute_pokemon_stats,
    compute_usage,
    filter_and_merge_cells,
    today_jst,
)
from ..dynamodb_serde import deserialize_item, from_attr, serialize_item
from .auth import AuthService, DiscordHttpClient, parse_cookies
from .cache import SeasonDataCache, log_event
from .crypto import derive_aes256_key
from .errors import ApiError, error_response, json_response, redirect_response

_SSM_SECRETS_CACHE: dict[str, str] = {}
_DEFAULT_SEASON_CACHE = SeasonDataCache()


def _load_ssm_secrets(ssm_client: Any, names: list[str]) -> dict[str, str]:
    missing = [n for n in names if n and n not in _SSM_SECRETS_CACHE]
    if missing:
        resp = ssm_client.get_parameters(Names=missing, WithDecryption=True)
        for p in resp.get("Parameters", []):
            _SSM_SECRETS_CACHE[p["Name"]] = p["Value"]
    return {n: _SSM_SECRETS_CACHE.get(n, "") for n in names if n}


class PlakoroApiApp:
    """Testable application object wrapping DynamoDB, Auth, and SeasonDataCache."""

    def __init__(
        self,
        *,
        dynamodb_client: Any,
        table_prefix: str,
        auth_service: AuthService,
        allowed_origin: str,
        season_cache: SeasonDataCache | None = None,
        today_fn: Callable[[], date] = today_jst,
        exclude_mirror: bool = False,
    ):
        self.dynamodb = dynamodb_client
        self.prefix = table_prefix
        self.auth = auth_service
        self.allowed_origin = allowed_origin.rstrip("/")
        self.cache = season_cache or SeasonDataCache()
        self._today_fn = today_fn
        self.exclude_mirror = exclude_mirror

    def _verify_csrf_origin(
        self,
        headers: dict[str, str],
        *,
        require_json_content_type: bool = True,
    ) -> None:
        origin = (headers.get("origin") or "").rstrip("/")
        if not origin or (self.allowed_origin and origin != self.allowed_origin):
            log_event("access_denied", reason="origin_mismatch")
            raise ApiError(403, "FORBIDDEN", "不正な送信元からのリクエストです。")
        if require_json_content_type:
            ctype = (headers.get("content-type") or "").lower()
            if not ctype.startswith("application/json"):
                raise ApiError(403, "FORBIDDEN", "Content-Type は application/json を指定してください。")

    def _parse_scope(
        self,
        scope_raw: str | None,
        seasons_list: list[dict[str, Any]],
    ) -> str | int:
        valid_ids = {int(s["id"]) for s in seasons_list}
        if not scope_raw:
            return max(valid_ids) if valid_ids else "all"
        s = scope_raw.strip()
        if s == "all":
            return "all"
        m = re.fullmatch(r"season_(\d+)", s)
        if m:
            sid = int(m.group(1))
            if sid in valid_ids:
                return sid
        if s.isdigit() and int(s) in valid_ids:
            return int(s)
        raise ApiError(400, "BAD_REQUEST", "指定されたシーズン（scope）が不正です。")

    def _load_scope_data(
        self,
        scope: str | int,
        master_version: int,
        seasons_list: list[dict[str, Any]],
    ) -> tuple[dict, list[dict], list[dict], set[int], bool]:
        if scope == "all":
            snapshots = []
            any_stale = False
            union_allowed: set[int] = set()
            all_battles: list[dict] = []
            all_matches: list[dict] = []
            for s in seasons_list:
                snap, stale = self.cache.get_season_snapshot(
                    self.dynamodb, self.prefix, int(s["id"]), master_version
                )
                snapshots.append(snap)
                any_stale = any_stale or stale
                union_allowed.update(snap.allowed_pokemon_ids)
                all_battles.extend(snap.battles)
                all_matches.extend(snap.matches)
            cells = filter_and_merge_cells([sn.cells for sn in snapshots], union_allowed)
            return cells, all_battles, all_matches, union_allowed, any_stale

        snap, stale = self.cache.get_season_snapshot(
            self.dynamodb, self.prefix, int(scope), master_version
        )
        allowed = set(snap.allowed_pokemon_ids)
        cells = filter_and_merge_cells([snap.cells], allowed)
        return cells, snap.battles, snap.matches, allowed, stale

    def _get_player_direct(self, player_id: int) -> dict[str, Any]:
        resp = self.dynamodb.get_item(
            TableName=f"{self.prefix}Players",
            Key={"playerId": {"N": str(int(player_id))}},
            ConsistentRead=True,
        )
        item = deserialize_item(resp.get("Item"))
        if item is None:
            raise ApiError(404, "NOT_FOUND", "指定されたプレイヤーが見つかりません。")
        return {
            "id": int(item["playerId"]),
            "name": item["name"],
            "is_public": bool(item.get("isPublic", True)),
            "reset_at": item.get("resetAt"),
            "reset_after_battle_id": item.get("resetAfterBattleId"),
        }

    def _get_pending_reset_request(self, player_id: int) -> dict[str, Any] | None:
        resp = self.dynamodb.query(
            TableName=f"{self.prefix}ResetRequests",
            IndexName="player-index",
            KeyConditionExpression="playerId = :pid",
            ExpressionAttributeValues={":pid": {"N": str(int(player_id))}},
            ScanIndexForward=False,
        )
        for raw in resp.get("Items", []):
            parsed = deserialize_item(raw)
            if parsed and parsed.get("status") == "pending":
                return {
                    "requestId": int(parsed["requestId"]),
                    "status": parsed["status"],
                    "requestedAt": parsed.get("requestedAt"),
                }
        return None

    def handle(self, event: dict[str, Any], context: Any = None) -> dict[str, Any]:
        req_ctx = event.get("requestContext") or {}
        request_id = (
            req_ctx.get("requestId")
            or getattr(context, "aws_request_id", None)
            or "local-req"
        )
        http_info = req_ctx.get("http") or {}
        method = (http_info.get("method") or event.get("httpMethod") or "GET").upper()
        raw_path = event.get("rawPath") or event.get("path") or "/"
        query = event.get("queryStringParameters") or {}
        raw_headers = event.get("headers") or {}
        headers = {k.lower(): v for k, v in raw_headers.items() if isinstance(v, str)}
        cookies = parse_cookies(event)

        try:
            # 1. Public OAuth endpoints
            if method == "GET" and raw_path == "/api/auth/login":
                auth_url, set_cookies = self.auth.start_login(query.get("returnTo"))
                return redirect_response(auth_url, request_id=request_id, cookies=set_cookies)

            if method == "GET" and raw_path == "/api/auth/callback":
                try:
                    return_to, set_cookies = self.auth.handle_callback(
                        code=query.get("code"),
                        state=query.get("state"),
                        cookies=cookies,
                    )
                    return redirect_response(return_to, request_id=request_id, cookies=set_cookies)
                except ApiError as err:
                    if err.code == "NOT_A_MEMBER":
                        return redirect_response(
                            "/index.html?auth_error=not_a_member",
                            request_id=request_id,
                        )
                    raise

            if method == "POST" and raw_path == "/api/auth/logout":
                self._verify_csrf_origin(headers, require_json_content_type=False)
                clear_cookies = self.auth.logout(cookies)
                return json_response(
                    200,
                    {"ok": True},
                    request_id=request_id,
                    cookies=clear_cookies,
                )

            # 2. All other endpoints require authenticated Discord server member session
            session = self.auth.require_member_session(cookies)
            viewer_player_id = int(session["playerId"])

            if method == "GET" and raw_path == "/api/me":
                _, _, _, seasons_list = self.cache.get_masters(self.dynamodb, self.prefix)
                me = self._get_player_direct(viewer_player_id)
                pending_req = self._get_pending_reset_request(viewer_player_id)
                return json_response(
                    200,
                    {
                        "player": {
                            "id": me["id"],
                            "name": me["name"],
                            "isPublic": me["is_public"],
                            "resetAt": me["reset_at"],
                            "resetAfterBattleId": me["reset_after_battle_id"],
                        },
                        "pendingResetRequest": pending_req,
                        "seasons": seasons_list,
                    },
                    request_id=request_id,
                )

            if method == "PATCH" and raw_path == "/api/me/privacy":
                self._verify_csrf_origin(headers, require_json_content_type=True)
                try:
                    payload = json.loads(event.get("body") or "{}")
                except json.JSONDecodeError as exc:
                    raise ApiError(400, "BAD_REQUEST", "JSON形式が不正です。") from exc
                if not isinstance(payload.get("isPublic"), bool):
                    raise ApiError(400, "BAD_REQUEST", "isPublic には真偽値（true / false）を指定してください。")
                new_val = bool(payload["isPublic"])
                self.dynamodb.update_item(
                    TableName=f"{self.prefix}Players",
                    Key={"playerId": {"N": str(viewer_player_id)}},
                    UpdateExpression="SET isPublic = :pub",
                    ConditionExpression="attribute_exists(playerId)",
                    ExpressionAttributeValues={":pub": {"BOOL": new_val}},
                )
                return json_response(
                    200,
                    {"id": viewer_player_id, "isPublic": new_val},
                    request_id=request_id,
                )

            if method == "POST" and raw_path == "/api/me/reset-requests":
                self._verify_csrf_origin(headers, require_json_content_type=True)
                try:
                    payload = json.loads(event.get("body") or "{}")
                except json.JSONDecodeError as exc:
                    raise ApiError(400, "BAD_REQUEST", "JSON形式が不正です。") from exc
                reason = str(payload.get("reason") or "").strip()[:200]
                if self._get_pending_reset_request(viewer_player_id) is not None:
                    raise ApiError(409, "CONFLICT", "承認待ちのリセット申請がすでに存在します。")

                cnt_resp = self.dynamodb.update_item(
                    TableName=f"{self.prefix}Counters",
                    Key={"counterName": {"S": "resetRequestId"}},
                    UpdateExpression="ADD #v :one",
                    ExpressionAttributeNames={"#v": "value"},
                    ExpressionAttributeValues={":one": {"N": "1"}},
                    ReturnValues="UPDATED_NEW",
                )
                req_id = int(from_attr(cnt_resp["Attributes"]["value"]))
                now_iso = datetime.now(timezone.utc).isoformat()
                req_item = {
                    "requestId": req_id,
                    "playerId": viewer_player_id,
                    "status": "pending",
                    "requestedAt": now_iso,
                    "reason": reason or None,
                }
                self.dynamodb.put_item(
                    TableName=f"{self.prefix}ResetRequests",
                    Item=serialize_item(req_item),
                    ConditionExpression="attribute_not_exists(requestId)",
                )
                log_event("reset_requested", playerId=viewer_player_id, requestId=req_id)
                return json_response(
                    201,
                    {"requestId": req_id, "status": "pending", "requestedAt": now_iso},
                    request_id=request_id,
                )

            # 3. Read / Aggregation endpoints
            mv, pokemon_by_id, players_by_id, seasons_list = self.cache.get_masters(
                self.dynamodb, self.prefix
            )

            if method == "GET" and raw_path == "/api/overview":
                scope = self._parse_scope(query.get("scope"), seasons_list)
                cells, battles, matches, allowed, stale = self._load_scope_data(
                    scope, mv, seasons_list
                )
                matches_by_id = {int(m["match_id"]): m for m in matches}
                battles_json = build_battles_json(
                    battles, matches_by_id, pokemon_by_id, players_by_id, allowed
                )["battles"]
                trend_json = build_battle_trend_json(battles, allowed, today=self._today_fn())
                usage_json = compute_usage(cells, pokemon_by_id, allowed)
                stats_json = compute_pokemon_stats(
                    cells,
                    pokemon_by_id,
                    allowed,
                    exclude_mirror=self.exclude_mirror,
                    include_mirror_field=True,
                )
                matchups_json = compute_matchups(
                    battles,
                    pokemon_by_id,
                    allowed,
                    exclude_mirror=self.exclude_mirror,
                )
                recent_3 = sorted(battles_json, key=lambda b: int(b["id"]))[-3:]
                extra_h = {"X-Data-Stale": "1"} if stale else None
                return json_response(
                    200,
                    {
                        "total_battles": len(battles_json),
                        "recent_battles": recent_3,
                        "battle_trend": trend_json,
                        "usage": usage_json,
                        "pokemon_stats": stats_json,
                        "matchups": matchups_json,
                    },
                    request_id=request_id,
                    extra_headers=extra_h,
                )

            if method == "GET" and raw_path == "/api/recent-battles":
                bfilter = (query.get("type") or "all").strip()
                if bfilter not in ("all", "normal", "challenge"):
                    raise ApiError(400, "BAD_REQUEST", "type には all / normal / challenge を指定してください。")
                try:
                    limit = min(50, max(1, int(query.get("limit") or 30)))
                except ValueError as exc:
                    raise ApiError(400, "BAD_REQUEST", "limit が不正です。") from exc

                cells, battles, matches, allowed, stale = self._load_scope_data(
                    "all", mv, seasons_list
                )
                matches_by_id = {int(m["match_id"]): m for m in matches}
                formatted_battles = build_battles_json(
                    battles, matches_by_id, pokemon_by_id, players_by_id, allowed
                )["battles"]
                formatted_matches = build_battle_matches_json(
                    matches, battles, pokemon_by_id, players_by_id
                )["matches"]
                challenge_stats = compute_battle_challenge_stats(
                    cells, formatted_matches, pokemon_by_id, allowed
                )
                feed = build_recent_feed(
                    formatted_battles,
                    formatted_matches,
                    battle_filter=bfilter,
                    limit=limit,
                )
                extra_h = {"X-Data-Stale": "1"} if stale else None
                return json_response(
                    200,
                    {
                        "items": feed,
                        "battle_challenge_stats": challenge_stats,
                    },
                    request_id=request_id,
                    extra_headers=extra_h,
                )

            char_match = re.fullmatch(r"/api/characters/(\d+)", raw_path)
            if method == "GET" and char_match:
                cid = int(char_match.group(1))
                if cid not in pokemon_by_id:
                    raise ApiError(404, "NOT_FOUND", "指定されたキャラクターが見つかりません。")
                scope = self._parse_scope(query.get("scope"), seasons_list)
                cells, battles, matches, allowed, stale = self._load_scope_data(
                    scope, mv, seasons_list
                )
                matches_by_id = {int(m["match_id"]): m for m in matches}
                formatted_battles = build_battles_json(
                    battles, matches_by_id, pokemon_by_id, players_by_id, allowed
                )["battles"]
                usage_pokemon = compute_usage(cells, pokemon_by_id, allowed)["pokemon"]
                matchups = compute_matchups(
                    battles,
                    pokemon_by_id,
                    allowed,
                    exclude_mirror=self.exclude_mirror,
                )["matchups"]
                detail = compute_character_detail(
                    pokemon_by_id[cid],
                    formatted_battles,
                    matchups,
                    usage_pokemon,
                    players_by_id,
                )
                extra_h = {"X-Data-Stale": "1"} if stale else None
                return json_response(200, detail, request_id=request_id, extra_headers=extra_h)

            if method == "GET" and raw_path == "/api/players":
                scope = self._parse_scope(query.get("scope"), seasons_list)
                _cells, battles, matches, allowed, stale = self._load_scope_data(
                    scope, mv, seasons_list
                )
                matches_by_id = {int(m["match_id"]): m for m in matches}
                formatted_battles = build_battles_json(
                    battles, matches_by_id, pokemon_by_id, players_by_id, allowed
                )["battles"]
                plist = compute_player_list(list(players_by_id.values()), formatted_battles)
                extra_h = {"X-Data-Stale": "1"} if stale else None
                return json_response(200, plist, request_id=request_id, extra_headers=extra_h)

            player_match = re.fullmatch(r"/api/players/(\d+)", raw_path)
            if method == "GET" and player_match:
                target_pid = int(player_match.group(1))
                target_player = self._get_player_direct(target_pid)
                if not target_player["is_public"] and viewer_player_id != target_pid:
                    log_event(
                        "access_denied",
                        reason="PLAYER_PRIVATE",
                        viewerPlayerId=viewer_player_id,
                        targetPlayerId=target_pid,
                    )
                    raise ApiError(403, "PLAYER_PRIVATE", "このプレイヤーの戦績は非公開です。")

                btype = (query.get("type") or "all").strip()
                if btype not in ("all", "normal", "challenge"):
                    raise ApiError(400, "BAD_REQUEST", "type には all / normal / challenge を指定してください。")
                scope = self._parse_scope(query.get("scope"), seasons_list)
                _cells, battles, matches, allowed, stale = self._load_scope_data(
                    scope, mv, seasons_list
                )
                matches_by_id = {int(m["match_id"]): m for m in matches}
                formatted_battles = build_battles_json(
                    battles, matches_by_id, pokemon_by_id, players_by_id, allowed
                )["battles"]
                pdetail = compute_player_detail(
                    target_player,
                    formatted_battles,
                    battle_type=btype,
                    reset_after_battle_id=target_player["reset_after_battle_id"],
                )
                extra_h = {"X-Data-Stale": "1"} if stale else None
                return json_response(200, pdetail, request_id=request_id, extra_headers=extra_h)

            raise ApiError(404, "NOT_FOUND", "指定されたAPIエンドポイントが存在しません。")

        except ApiError as err:
            return error_response(err, request_id=request_id)
        except Exception as exc:
            log_event("unhandled_error", requestId=request_id, errorType=exc.__class__.__name__)
            return error_response(
                ApiError(500, "INTERNAL_ERROR", "サーバー内部エラーが発生しました。"),
                request_id=request_id,
            )


_APP_INSTANCE: PlakoroApiApp | None = None


def _build_default_app() -> PlakoroApiApp:
    import boto3

    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    dynamodb = boto3.client("dynamodb", region_name=region)
    ssm = boto3.client("ssm", region_name=region)

    secret_param = os.environ["SSM_DISCORD_CLIENT_SECRET"]
    enc_key_param = os.environ["SSM_SESSION_ENC_KEY"]
    secrets_map = _load_ssm_secrets(ssm, [secret_param, enc_key_param])

    allowed_origin = os.environ["ALLOWED_ORIGIN"].rstrip("/")
    client_id = os.environ["DISCORD_CLIENT_ID"]
    guild_id = os.environ["DISCORD_GUILD_ID"]
    prefix = os.environ.get("PLAKORO_DYNAMODB_TABLE_PREFIX", "plakoro_prod_")

    discord_http = DiscordHttpClient(
        client_id=client_id,
        client_secret=secrets_map[secret_param],
    )
    auth = AuthService(
        dynamodb_client=dynamodb,
        table_prefix=prefix,
        discord_client=discord_http,
        client_id=client_id,
        guild_id=guild_id,
        redirect_uri=f"{allowed_origin}/api/auth/callback",
        token_enc_key=derive_aes256_key(secrets_map[enc_key_param]),
    )
    return PlakoroApiApp(
        dynamodb_client=dynamodb,
        table_prefix=prefix,
        auth_service=auth,
        allowed_origin=allowed_origin,
        season_cache=_DEFAULT_SEASON_CACHE,
        exclude_mirror=os.environ.get("PLAKORO_EXCLUDE_MIRROR") == "1",
    )


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    global _APP_INSTANCE
    if _APP_INSTANCE is None:
        _APP_INSTANCE = _build_default_app()
    return _APP_INSTANCE.handle(event, context)
