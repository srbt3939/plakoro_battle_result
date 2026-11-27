"""End-to-end tests for Lambda HTTP API: OAuth2, multi-account, privacy, reset, and GSI lag (手順7)."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse

from app.lambda_api import (
    AuthService,
    DiscordTemporaryError,
    PlakoroApiApp,
    SeasonDataCache,
    derive_aes256_key,
)
from app.repository import DynamoDBBattleRepository
from scripts.build_test_fixtures import ANONYMIZED_DB_PATH, FIXED_TODAY
from scripts.migrate_to_dynamodb import execute_migration
from tests.fake_dynamodb import InMemoryDynamoDBClient


class FakeDiscordClient:
    def __init__(self) -> None:
        self.users_by_code: dict[str, tuple[str, str, str]] = {}
        self.member_tokens: set[str] = set()
        self.fail_Temporary: bool = False
        self.membership_calls: int = 0

    def register_login(self, code: str, token: str, discord_uid: str, name: str, *, is_member: bool = True) -> None:
        self.users_by_code[code] = (token, discord_uid, name)
        if is_member:
            self.member_tokens.add(token)
        else:
            self.member_tokens.discard(token)

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        _ = redirect_uri
        token, _uid, _name = self.users_by_code[code]
        return {"access_token": token, "expires_in": 604800}

    def get_current_user(self, access_token: str) -> dict:
        for _code, (tok, uid, name) in self.users_by_code.items():
            if tok == access_token:
                return {"id": uid, "global_name": name}
        raise AssertionError("Unknown token")

    def check_guild_membership(self, access_token: str, guild_id: str) -> str:
        _ = guild_id
        self.membership_calls += 1
        if self.fail_Temporary:
            raise DiscordTemporaryError("Discord 503", retry_after=2)
        return "member" if access_token in self.member_tokens else "not_member"


def _event(
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    cookies: list[str] | None = None,
    headers: dict[str, str] | None = None,
    body: dict | None = None,
) -> dict:
    return {
        "version": "2.0",
        "rawPath": path,
        "queryStringParameters": query or {},
        "cookies": cookies or [],
        "headers": headers or {},
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {
            "requestId": "req-test-123",
            "http": {"method": method, "path": path},
        },
    }


def _extract_cookie_pair(set_cookie_headers: list[str], name: str) -> str:
    for sc in set_cookie_headers:
        first = sc.split(";", 1)[0]
        if first.startswith(f"{name}="):
            return first
    raise KeyError(f"Cookie {name} not found in {set_cookie_headers}")


class LambdaApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ddb = InMemoryDynamoDBClient("api_")
        with tempfile.TemporaryDirectory() as tmp:
            execute_migration(ANONYMIZED_DB_PATH, self.ddb, "api_", backup_dir=Path(tmp))

        # Link a 2nd Discord account (hand-camera account) to Player #1
        self.repo = DynamoDBBattleRepository(table_prefix="api_", dynamodb_client=self.ddb)
        self.repo.link_discord_account(1, "900000000000990001", label="手元カメラ用")

        self.now = 1_795_000_000.0
        self.discord = FakeDiscordClient()
        self.origin = "https://d1234example.cloudfront.net"
        self.auth = AuthService(
            dynamodb_client=self.ddb,
            table_prefix="api_",
            discord_client=self.discord,
            client_id="client-123",
            guild_id="guild-999",
            redirect_uri=f"{self.origin}/api/auth/callback",
            token_enc_key=derive_aes256_key("unit-test-32-byte-secret-material"),
            now_fn=lambda: self.now,
        )
        self.cache = SeasonDataCache(sleep_fn=lambda _s: None)
        self.api = PlakoroApiApp(
            dynamodb_client=self.ddb,
            table_prefix="api_",
            auth_service=self.auth,
            allowed_origin=self.origin,
            season_cache=self.cache,
            today_fn=lambda: FIXED_TODAY,
        )

    def _login_as(self, code: str, token: str, discord_uid: str, name: str, *, is_member: bool = True) -> dict:
        self.discord.register_login(code, token, discord_uid, name, is_member=is_member)
        start_resp = self.api.handle(_event("GET", "/api/auth/login", query={"returnTo": "/players/index.html"}))
        self.assertEqual(start_resp["statusCode"], 302)
        state_cookie = _extract_cookie_pair(start_resp["cookies"], "__Host-plakoro_oauth_state")
        parsed_loc = urllib.parse.urlparse(start_resp["headers"]["Location"])
        state_val = urllib.parse.parse_qs(parsed_loc.query)["state"][0]

        cb_resp = self.api.handle(
            _event(
                "GET",
                "/api/auth/callback",
                query={"code": code, "state": state_val},
                cookies=[state_cookie],
            )
        )
        return {"callback_resp": cb_resp, "state": state_val, "state_cookie": state_cookie}

    def test_unauthenticated_and_non_member_blocked(self) -> None:
        unauth = self.api.handle(_event("GET", "/api/overview"))
        self.assertEqual(unauth["statusCode"], 401)
        self.assertEqual(json.loads(unauth["body"])["error"]["code"], "UNAUTHENTICATED")

        non_mem = self._login_as("code-outsider", "tok-outsider", "77777", "Outsider", is_member=False)
        self.assertEqual(non_mem["callback_resp"]["statusCode"], 302)
        self.assertIn("auth_error=not_a_member", non_mem["callback_resp"]["headers"]["Location"])

    def test_multi_discord_account_resolves_to_same_player_and_state_replay_rejected(self) -> None:
        # Player #1's primary Discord ID in anonymized DB is 900000000000000001
        res_main = self._login_as("c-main", "t-main", "900000000000000001", "P1Main")
        self.assertEqual(res_main["callback_resp"]["statusCode"], 302)
        self.assertEqual(res_main["callback_resp"]["headers"]["Location"], "/players/index.html")
        main_cookie = _extract_cookie_pair(res_main["callback_resp"]["cookies"], "__Host-plakoro_session")

        # Replaying the exact same OAuth state must fail with 400
        replay = self.api.handle(
            _event(
                "GET",
                "/api/auth/callback",
                query={"code": "c-main", "state": res_main["state"]},
                cookies=[res_main["state_cookie"]],
            )
        )
        self.assertEqual(replay["statusCode"], 400)

        # Logging in with Player #1's 2nd Discord account (hand-camera) resolves to the SAME playerId=1!
        res_sub = self._login_as("c-sub", "t-sub", "900000000000990001", "P1HandCam")
        sub_cookie = _extract_cookie_pair(res_sub["callback_resp"]["cookies"], "__Host-plakoro_session")

        me_main = json.loads(self.api.handle(_event("GET", "/api/me", cookies=[main_cookie]))["body"])
        me_sub = json.loads(self.api.handle(_event("GET", "/api/me", cookies=[sub_cookie]))["body"])
        self.assertEqual(me_main["player"]["id"], 1)
        self.assertEqual(me_sub["player"]["id"], 1)

        # Brand-new Discord member auto-creates a new playerId (26)
        res_new = self._login_as("c-new", "t-new", "555555555555555555", "BrandNewMember")
        new_cookie = _extract_cookie_pair(res_new["callback_resp"]["cookies"], "__Host-plakoro_session")
        me_new = json.loads(self.api.handle(_event("GET", "/api/me", cookies=[new_cookie]))["body"])
        self.assertEqual(me_new["player"]["id"], 26)

    def test_privacy_authorization_csrf_and_reset_request_conflict(self) -> None:
        p1_cookie = _extract_cookie_pair(
            self._login_as("c1", "t1", "900000000000000001", "P1")["callback_resp"]["cookies"],
            "__Host-plakoro_session",
        )
        p2_cookie = _extract_cookie_pair(
            self._login_as("c2", "t2", "900000000000000002", "P2")["callback_resp"]["cookies"],
            "__Host-plakoro_session",
        )

        # CSRF: wrong Origin on PATCH is rejected with 403 FORBIDDEN
        bad_csrf = self.api.handle(
            _event(
                "PATCH",
                "/api/me/privacy",
                cookies=[p1_cookie],
                headers={"Origin": "https://evil.example", "Content-Type": "application/json"},
                body={"isPublic": False},
            )
        )
        self.assertEqual(bad_csrf["statusCode"], 403)
        self.assertEqual(json.loads(bad_csrf["body"])["error"]["code"], "FORBIDDEN")

        # Valid PATCH sets Player #1 to private
        ok_priv = self.api.handle(
            _event(
                "PATCH",
                "/api/me/privacy",
                cookies=[p1_cookie],
                headers={"Origin": self.origin, "Content-Type": "application/json"},
                body={"isPublic": False},
            )
        )
        self.assertEqual(ok_priv["statusCode"], 200)

        # Player #1 can still view their own detail (200), while Player #2 gets 403 PLAYER_PRIVATE
        own_view = self.api.handle(_event("GET", "/api/players/1", query={"scope": "season_3"}, cookies=[p1_cookie]))
        self.assertEqual(own_view["statusCode"], 200)

        other_view = self.api.handle(_event("GET", "/api/players/1", query={"scope": "season_3"}, cookies=[p2_cookie]))
        self.assertEqual(other_view["statusCode"], 403)
        self.assertEqual(json.loads(other_view["body"])["error"]["code"], "PLAYER_PRIVATE")

        # Reset request succeeds once (201) and rejects duplicate pending (409 CONFLICT)
        r1 = self.api.handle(
            _event(
                "POST",
                "/api/me/reset-requests",
                cookies=[p1_cookie],
                headers={"Origin": self.origin, "Content-Type": "application/json"},
                body={"reason": "新シーズン区切りのため"},
            )
        )
        self.assertEqual(r1["statusCode"], 201)
        r2 = self.api.handle(
            _event(
                "POST",
                "/api/me/reset-requests",
                cookies=[p1_cookie],
                headers={"Origin": self.origin, "Content-Type": "application/json"},
                body={"reason": "連打"},
            )
        )
        self.assertEqual(r2["statusCode"], 409)
        self.assertEqual(json.loads(r2["body"])["error"]["code"], "CONFLICT")

    def test_membership_recheck_30m_cache_24h_failopen_and_revocation(self) -> None:
        cookie = _extract_cookie_pair(
            self._login_as("c1", "t1", "900000000000000001", "P1")["callback_resp"]["cookies"],
            "__Host-plakoro_session",
        )
        calls_after_login = self.discord.membership_calls

        # Within 30 minutes: no Discord API call
        self.now += 600
        self.assertEqual(self.api.handle(_event("GET", "/api/overview", cookies=[cookie]))["statusCode"], 200)
        self.assertEqual(self.discord.membership_calls, calls_after_login)

        # At +35 minutes with temporary Discord 5xx: fail-open within 24h grace!
        self.now += 30 * 60
        self.discord.fail_Temporary = True
        grace_resp = self.api.handle(_event("GET", "/api/overview", cookies=[cookie]))
        self.assertEqual(grace_resp["statusCode"], 200)

        # At +25 hours with Discord still down: 503 TEMPORARILY_UNAVAILABLE
        self.now += 25 * 3600
        unavail_resp = self.api.handle(_event("GET", "/api/overview", cookies=[cookie]))
        self.assertEqual(unavail_resp["statusCode"], 503)

        # Discord recovers, but user left server -> 403 NOT_A_MEMBER & session revoked
        self.discord.fail_Temporary = False
        self.discord.member_tokens.discard("t1")
        left_resp = self.api.handle(_event("GET", "/api/overview", cookies=[cookie]))
        self.assertEqual(left_resp["statusCode"], 403)
        self.assertEqual(json.loads(left_resp["body"])["error"]["code"], "NOT_A_MEMBER")

    def test_gsi_eventual_consistency_lag_retries_and_never_poisons_confirmed_cache(self) -> None:
        cookie = _extract_cookie_pair(
            self._login_as("c1", "t1", "900000000000000001", "P1")["callback_resp"]["cookies"],
            "__Host-plakoro_session",
        )
        # Prime confirmed cache for season 3 (make dataChangedAt old so it becomes confirmed)
        self.ddb.update_item(
            TableName="api_Seasons",
            Key={"seasonId": {"N": "3"}},
            UpdateExpression="SET dataChangedAt = :old",
            ExpressionAttributeValues={":old": {"S": "2026-01-01T00:00:00+00:00"}},
        )
        first = self.api.handle(_event("GET", "/api/overview", query={"scope": "season_3"}, cookies=[cookie]))
        self.assertEqual(first["statusCode"], 200)
        initial_total = json.loads(first["body"])["total_battles"]

        # Register a new battle in season 3, but hide it from GSI for 10 queries (> 3 retries)
        new_ids = self.repo.create_battles(
            400,
            1,
            2,
            [{"player1_pokemon_id": 1, "player2_pokemon_id": 2, "first_player": 1, "player1_result": "win"}],
            request_id="lag-req-1",
        )
        self.ddb.hidden_from_gsi.add(("api_Battles", new_ids[0]))
        self.ddb.gsi_lag_countdown["api_Battles"] = 10

        # Because confirmed cache exists, API serves stale confirmed cache with X-Data-Stale: 1 and does NOT overwrite cache!
        stale_resp = self.api.handle(_event("GET", "/api/overview", query={"scope": "season_3"}, cookies=[cookie]))
        self.assertEqual(stale_resp["statusCode"], 200)
        self.assertEqual(stale_resp["headers"].get("X-Data-Stale"), "1")
        self.assertEqual(json.loads(stale_resp["body"])["total_battles"], initial_total)

        # Once GSI catches up, the very next request sees the new battle immediately!
        self.ddb.gsi_lag_countdown["api_Battles"] = 0
        fresh_resp = self.api.handle(_event("GET", "/api/overview", query={"scope": "season_3"}, cookies=[cookie]))
        self.assertEqual(fresh_resp["statusCode"], 200)
        self.assertNotIn("X-Data-Stale", fresh_resp["headers"])
        self.assertEqual(json.loads(fresh_resp["body"])["total_battles"], initial_total + 1)

        # No rawContent or discordUserId ever leaked in overview, recent-battles, players, or characters responses
        for path, q in (
            ("/api/overview", {"scope": "all"}),
            ("/api/recent-battles", {"type": "all"}),
            ("/api/players", {"scope": "season_3"}),
            ("/api/characters/1", {"scope": "all"}),
            ("/api/players/1", {"scope": "all"}),
        ):
            r = self.api.handle(_event("GET", path, query=q, cookies=[cookie]))
            self.assertEqual(r["statusCode"], 200)
            self.assertNotIn("rawContent", r["body"])
            self.assertNotIn("raw_content", r["body"])
            self.assertNotIn("discordUserId", r["body"])
            self.assertNotIn("900000000000000001", r["body"])


if __name__ == "__main__":
    unittest.main()
