"""Discord OAuth2 authentication, session management, and guild membership re-check (手順7 §1・§2)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import secrets
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from ..dynamodb_serde import deserialize_item, from_attr, serialize_item
from .cache import log_event
from .crypto import decrypt_access_token, encrypt_access_token
from .errors import ApiError

SESSION_COOKIE_NAME = "__Host-plakoro_session"
STATE_COOKIE_NAME = "__Host-plakoro_oauth_state"

OAUTH_STATE_TTL_SECONDS = 600
SESSION_MAX_LIFETIME_SECONDS = 7 * 24 * 3600
MEMBERSHIP_CACHE_SECONDS = 30 * 60
MEMBERSHIP_FAILOPEN_GRACE_SECONDS = 24 * 3600


class DiscordTemporaryError(Exception):
    """Raised when Discord API returns 429 (after 1 retry), 5xx, or times out."""

    def __init__(self, message: str, retry_after: int = 5):
        super().__init__(message)
        self.retry_after = retry_after


def hash_token(prefix: str, raw_token: str) -> str:
    digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return f"{prefix}#{digest}"


def sanitize_return_to(raw_path: str | None) -> str:
    if not raw_path:
        return "/index.html"
    path = raw_path.strip()
    if not path.startswith("/") or path.startswith("//") or "\\" in path or "\n" in path or "\r" in path:
        return "/index.html"
    return path


def parse_cookies(event: dict[str, Any]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    cookie_list = event.get("cookies") or []
    headers = event.get("headers") or {}
    header_cookie = headers.get("cookie") or headers.get("Cookie") or ""
    raw_parts = list(cookie_list)
    if header_cookie:
        raw_parts.extend(header_cookie.split(";"))
    for entry in raw_parts:
        if "=" in entry:
            name, val = entry.split("=", 1)
            cookies[name.strip()] = val.strip()
    return cookies


def format_cookie(
    name: str,
    value: str,
    *,
    max_age: int,
    secure: bool = True,
) -> str:
    parts = [
        f"{name}={value}",
        "Path=/",
        f"Max-Age={max_age}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


class DiscordHttpClient:
    """Minimal stdlib HTTPS client for Discord OAuth2 & Guild Member verification."""

    API_BASE = "https://discord.com/api/v10"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        sleep_fn: Callable[[float], None] = time.sleep,
        timeout_sec: float = 3.0,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self._sleep = sleep_fn
        self._timeout = timeout_sec

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:
        for attempt in range(2):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    body = resp.read().decode("utf-8")
                    return resp.status, (json.loads(body) if body else {})
            except urllib.error.HTTPError as err:
                body_raw = err.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(body_raw) if body_raw else {}
                except json.JSONDecodeError:
                    parsed = {}

                if err.code == 429:
                    retry_after_raw = err.headers.get("Retry-After") or parsed.get("retry_after") or 1
                    try:
                        wait_sec = float(retry_after_raw)
                    except (TypeError, ValueError):
                        wait_sec = 1.0
                    if attempt == 0 and wait_sec <= 2.0:
                        self._sleep(max(0.05, wait_sec))
                        continue
                    raise DiscordTemporaryError(
                        "Discord API rate limited.",
                        retry_after=max(1, int(round(wait_sec))),
                    ) from err
                if err.code >= 500:
                    raise DiscordTemporaryError("Discord API server error.") from err
                return err.code, parsed
            except (urllib.error.URLError, TimeoutError) as err:
                raise DiscordTemporaryError("Discord API network timeout.") from err

        raise DiscordTemporaryError("Discord API unreachable.")

    def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        form = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            }
        ).encode("utf-8")
        status, payload = self._request_json(
            "POST",
            f"{self.API_BASE}/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "PlakoroBattleResult/1.0",
            },
            data=form,
        )
        if status != 200 or "access_token" not in payload:
            raise ApiError(401, "UNAUTHENTICATED", "Discordログイン認証に失敗しました。")
        return payload

    def get_current_user(self, access_token: str) -> dict[str, Any]:
        status, payload = self._request_json(
            "GET",
            f"{self.API_BASE}/users/@me",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "PlakoroBattleResult/1.0",
            },
        )
        if status != 200 or "id" not in payload:
            raise ApiError(401, "UNAUTHENTICATED", "Discordユーザー情報の取得に失敗しました。")
        return payload

    def check_guild_membership(self, access_token: str, guild_id: str) -> str:
        """Return 'member', 'not_member', or 'unauthorized'; raise DiscordTemporaryError on 429/5xx."""
        status, payload = self._request_json(
            "GET",
            f"{self.API_BASE}/users/@me/guilds/{urllib.parse.quote(guild_id)}/member",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "PlakoroBattleResult/1.0",
            },
        )
        if status == 200:
            return "member"
        if status == 401:
            return "unauthorized"
        if status in (403, 404) or payload.get("code") in (10004, 10007):
            return "not_member"
        raise DiscordTemporaryError(f"Unexpected Discord status {status}")


class AuthService:
    def __init__(
        self,
        *,
        dynamodb_client: Any,
        table_prefix: str,
        discord_client: Any,
        client_id: str,
        guild_id: str,
        redirect_uri: str,
        token_enc_key: bytes,
        now_fn: Callable[[], float] = time.time,
    ):
        self.dynamodb = dynamodb_client
        self.prefix = table_prefix
        self.discord = discord_client
        self.client_id = client_id
        self.guild_id = guild_id
        self.redirect_uri = redirect_uri
        self.token_enc_key = token_enc_key
        self._now = now_fn

    def _now_iso(self) -> str:
        return datetime.fromtimestamp(self._now(), tz=timezone.utc).isoformat()

    def start_login(self, return_to_raw: str | None) -> tuple[str, list[str]]:
        return_to = sanitize_return_to(return_to_raw)
        state = secrets.token_urlsafe(32)
        now_epoch = int(self._now())
        state_item = {
            "sessionId": hash_token("state", state),
            "kind": "oauthState",
            "returnTo": return_to,
            "createdAt": self._now_iso(),
            "expiresAt": now_epoch + OAUTH_STATE_TTL_SECONDS,
        }
        self.dynamodb.put_item(
            TableName=f"{self.prefix}Sessions",
            Item=serialize_item(state_item),
        )
        params = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "scope": "identify guilds.members.read",
                "redirect_uri": self.redirect_uri,
                "state": state,
            }
        )
        authorize_url = f"https://discord.com/oauth2/authorize?{params}"
        cookie = format_cookie(STATE_COOKIE_NAME, state, max_age=OAUTH_STATE_TTL_SECONDS)
        return authorize_url, [cookie]

    def _resolve_or_create_player(self, discord_user_id: str, display_name: str) -> int:
        acc_resp = self.dynamodb.get_item(
            TableName=f"{self.prefix}DiscordAccounts",
            Key={"discordUserId": {"S": discord_user_id}},
            ConsistentRead=True,
        )
        acc = deserialize_item(acc_resp.get("Item"))
        if acc is not None:
            return int(acc["playerId"])

        cnt_resp = self.dynamodb.update_item(
            TableName=f"{self.prefix}Counters",
            Key={"counterName": {"S": "playerId"}},
            UpdateExpression="ADD #v :one",
            ExpressionAttributeNames={"#v": "value"},
            ExpressionAttributeValues={":one": {"N": "1"}},
            ReturnValues="UPDATED_NEW",
        )
        new_player_id = int(from_attr(cnt_resp["Attributes"]["value"]))
        now_iso = self._now_iso()

        try:
            self.dynamodb.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": f"{self.prefix}DiscordAccounts",
                            "Item": serialize_item(
                                {
                                    "discordUserId": discord_user_id,
                                    "playerId": new_player_id,
                                    "createdAt": now_iso,
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(discordUserId)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": f"{self.prefix}Players",
                            "Item": serialize_item(
                                {
                                    "playerId": new_player_id,
                                    "name": display_name or f"Player{new_player_id}",
                                    "discordUserId": discord_user_id,
                                    "createdAt": now_iso,
                                    "isPublic": True,
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(playerId)",
                        }
                    },
                    {
                        "Update": {
                            "TableName": f"{self.prefix}Counters",
                            "Key": {"counterName": {"S": "masterVersion"}},
                            "UpdateExpression": "ADD #v :one",
                            "ExpressionAttributeNames": {"#v": "value"},
                            "ExpressionAttributeValues": {":one": {"N": "1"}},
                        }
                    },
                ]
            )
            return new_player_id
        except Exception:
            retry_resp = self.dynamodb.get_item(
                TableName=f"{self.prefix}DiscordAccounts",
                Key={"discordUserId": {"S": discord_user_id}},
                ConsistentRead=True,
            )
            retry_acc = deserialize_item(retry_resp.get("Item"))
            if retry_acc is not None:
                return int(retry_acc["playerId"])
            raise

    def handle_callback(
        self,
        *,
        code: str | None,
        state: str | None,
        cookies: dict[str, str],
    ) -> tuple[str, list[str]]:
        cookie_state = cookies.get(STATE_COOKIE_NAME)
        if not code or not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
            log_event("auth_login_failed", reason="state_mismatch")
            raise ApiError(400, "BAD_REQUEST", "ログイン状態（state）の検証に失敗しました。")

        state_key = hash_token("state", state)
        state_resp = self.dynamodb.get_item(
            TableName=f"{self.prefix}Sessions",
            Key={"sessionId": {"S": state_key}},
            ConsistentRead=True,
        )
        state_item = deserialize_item(state_resp.get("Item"))
        now_epoch = int(self._now())
        if (
            state_item is None
            or state_item.get("kind") != "oauthState"
            or state_item.get("usedAt")
            or int(state_item.get("expiresAt", 0)) <= now_epoch
        ):
            log_event("auth_login_failed", reason="state_expired_or_reused")
            raise ApiError(400, "BAD_REQUEST", "ログインセッションの有効期限が切れました。もう一度お試しください。")

        # Consume state atomically without needing DeleteItem permission
        try:
            self.dynamodb.update_item(
                TableName=f"{self.prefix}Sessions",
                Key={"sessionId": {"S": state_key}},
                UpdateExpression="SET usedAt = :now_iso, expiresAt = :now_ep",
                ConditionExpression="attribute_not_exists(usedAt)",
                ExpressionAttributeValues={
                    ":now_iso": {"S": self._now_iso()},
                    ":now_ep": {"N": str(now_epoch)},
                },
            )
        except Exception as exc:
            log_event("auth_login_failed", reason="state_replay")
            raise ApiError(400, "BAD_REQUEST", "このログイン要求はすでに使用されています。") from exc

        try:
            token_data = self.discord.exchange_code(code, self.redirect_uri)
            access_token = str(token_data["access_token"])
            expires_in = int(token_data.get("expires_in", SESSION_MAX_LIFETIME_SECONDS))
            user_info = self.discord.get_current_user(access_token)
            membership = self.discord.check_guild_membership(access_token, self.guild_id)
        except DiscordTemporaryError as exc:
            log_event("auth_login_failed", reason="discord_temporary_error")
            raise ApiError(
                503,
                "TEMPORARILY_UNAVAILABLE",
                "Discordとの通信が一時的に混み合っています。しばらくしてから再度ログインしてください。",
                retry_after=exc.retry_after,
            ) from exc

        if membership != "member":
            log_event("auth_login_failed", reason="not_a_member")
            raise ApiError(403, "NOT_A_MEMBER", "このサイトはDiscordサーバーのメンバー限定です。")

        discord_user_id = str(user_info["id"])
        display_name = str(user_info.get("global_name") or user_info.get("username") or "Player")
        player_id = self._resolve_or_create_player(discord_user_id, display_name)

        lifetime = max(60, min(SESSION_MAX_LIFETIME_SECONDS, expires_in - 60))
        raw_session_token = secrets.token_urlsafe(32)
        now_iso = self._now_iso()
        session_item = {
            "sessionId": hash_token("sess", raw_session_token),
            "kind": "session",
            "playerId": player_id,
            "discordUserId": discord_user_id,
            "encryptedAccessToken": encrypt_access_token(access_token, self.token_enc_key),
            "createdAt": now_iso,
            "lastMembershipCheckAt": now_iso,
            "expiresAt": now_epoch + lifetime,
        }
        self.dynamodb.put_item(
            TableName=f"{self.prefix}Sessions",
            Item=serialize_item(session_item),
        )
        log_event("auth_login_succeeded", playerId=player_id)

        return_to = sanitize_return_to(state_item.get("returnTo"))
        set_cookies = [
            format_cookie(SESSION_COOKIE_NAME, raw_session_token, max_age=lifetime),
            format_cookie(STATE_COOKIE_NAME, "", max_age=0),
        ]
        return return_to, set_cookies

    def logout(self, cookies: dict[str, str]) -> list[str]:
        raw_token = cookies.get(SESSION_COOKIE_NAME)
        if raw_token:
            sess_key = hash_token("sess", raw_token)
            now_epoch = int(self._now())
            try:
                self.dynamodb.update_item(
                    TableName=f"{self.prefix}Sessions",
                    Key={"sessionId": {"S": sess_key}},
                    UpdateExpression="SET revokedAt = :now_iso, expiresAt = :now_ep",
                    ConditionExpression="attribute_exists(sessionId)",
                    ExpressionAttributeValues={
                        ":now_iso": {"S": self._now_iso()},
                        ":now_ep": {"N": str(now_epoch)},
                    },
                )
            except Exception:
                pass
        return [format_cookie(SESSION_COOKIE_NAME, "", max_age=0)]

    def require_member_session(self, cookies: dict[str, str]) -> dict[str, Any]:
        raw_token = cookies.get(SESSION_COOKIE_NAME)
        if not raw_token:
            raise ApiError(401, "UNAUTHENTICATED", "ログインが必要です。")

        sess_key = hash_token("sess", raw_token)
        resp = self.dynamodb.get_item(
            TableName=f"{self.prefix}Sessions",
            Key={"sessionId": {"S": sess_key}},
            ConsistentRead=True,
        )
        sess = deserialize_item(resp.get("Item"))
        now_epoch = self._now()
        if (
            sess is None
            or sess.get("kind") != "session"
            or sess.get("revokedAt")
            or int(sess.get("expiresAt", 0)) <= int(now_epoch)
        ):
            raise ApiError(401, "UNAUTHENTICATED", "セッションの有効期限が切れました。再度ログインしてください。")

        last_check_str = sess.get("lastMembershipCheckAt")
        elapsed = MEMBERSHIP_CACHE_SECONDS + 1.0
        if last_check_str:
            try:
                last_dt = datetime.fromisoformat(last_check_str.replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                elapsed = now_epoch - last_dt.timestamp()
            except ValueError:
                elapsed = MEMBERSHIP_CACHE_SECONDS + 1.0

        if elapsed > MEMBERSHIP_CACHE_SECONDS:
            enc_tok = sess.get("encryptedAccessToken")
            if not enc_tok:
                raise ApiError(401, "UNAUTHENTICATED", "再ログインが必要です。")
            try:
                access_token = decrypt_access_token(enc_tok, self.token_enc_key)
            except Exception as exc:
                raise ApiError(401, "UNAUTHENTICATED", "セッション情報の復号に失敗しました。") from exc

            try:
                status = self.discord.check_guild_membership(access_token, self.guild_id)
            except DiscordTemporaryError as exc:
                if elapsed <= MEMBERSHIP_FAILOPEN_GRACE_SECONDS:
                    log_event(
                        "membership_recheck_failed",
                        playerId=int(sess["playerId"]),
                        graceApplied=True,
                    )
                    return sess
                log_event(
                    "membership_recheck_failed",
                    playerId=int(sess["playerId"]),
                    graceApplied=False,
                )
                raise ApiError(
                    503,
                    "TEMPORARILY_UNAVAILABLE",
                    "メンバー資格の確認が一時的に行えません。しばらくしてからやり直してください。",
                    retry_after=exc.retry_after,
                ) from exc

            if status == "unauthorized":
                raise ApiError(401, "UNAUTHENTICATED", "Discordセッションが失効しました。再度ログインしてください。")
            if status != "member":
                self.dynamodb.update_item(
                    TableName=f"{self.prefix}Sessions",
                    Key={"sessionId": {"S": sess_key}},
                    UpdateExpression="SET revokedAt = :now_iso, expiresAt = :now_ep",
                    ExpressionAttributeValues={
                        ":now_iso": {"S": self._now_iso()},
                        ":now_ep": {"N": str(int(now_epoch))},
                    },
                )
                log_event("access_denied", reason="NOT_A_MEMBER", playerId=int(sess["playerId"]))
                raise ApiError(403, "NOT_A_MEMBER", "このサイトはDiscordサーバーのメンバー限定です。")

            now_iso = self._now_iso()
            self.dynamodb.update_item(
                TableName=f"{self.prefix}Sessions",
                Key={"sessionId": {"S": sess_key}},
                UpdateExpression="SET lastMembershipCheckAt = :now_iso",
                ExpressionAttributeValues={":now_iso": {"S": now_iso}},
            )
            sess["lastMembershipCheckAt"] = now_iso

        return sess
